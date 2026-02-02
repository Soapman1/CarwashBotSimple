import asyncio
import logging
import os
import random
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup

from database import init_db, create_user, get_user_by_telegram, update_subscription, cancel_subscription, get_user_info, Session, User

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))  # ID админа (твой Telegram ID)

if not TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

init_db()

# ===== ТРАНСЛИТЕРАЦИЯ =====
def transliterate(name):
    letters = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', ' ': ''
    }
    result = ""
    for char in name.lower():
        result += letters.get(char, char)
    result = ''.join(c for c in result if c.isalnum())
    return result.capitalize()[:20]

def is_admin(user_id):
    """Проверка, является ли пользователь админом"""
    return user_id == ADMIN_ID

# ===== КЛАВИАТУРЫ =====
def get_main_menu(telegram_id):
    """Главное меню в зависимости от статуса и прав"""
    user = get_user_by_telegram(telegram_id)
    
    # Админское меню (видно только админу)
    if is_admin(telegram_id):
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("➕ Создать бесплатный аккаунт")
        kb.row("📊 Статистика")
        kb.row("🔙 Обычное меню")
        return kb
    
    # Обычное меню для пользователей
    if not user:
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(types.KeyboardButton("📝 Зарегистрироваться"))
        return kb
    
    has_sub = user.subscription_end and user.subscription_end > datetime.now()
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    if has_sub:
        kb.row("💳 Продлить подписку")
        kb.row("ℹ️ Моя подписка")
        kb.row("❌ Отменить подписку")
    else:
        kb.row("💳 Оплатить подписку")
        kb.row("ℹ️ Мой аккаунт")
    
    return kb

# ===== СОСТОЯНИЯ =====
class RegState(StatesGroup):
    waiting_carwash_name = State()
    waiting_owner_name = State()

class AdminCreateState(StatesGroup):
    waiting_carwash_name = State()
    waiting_owner_name = State()
    waiting_days = State()

# ===== ОБРАБОТЧИКИ =====
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Бот для управления автомойкой.",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message_handler(commands=['admin'])
async def cmd_admin(message: types.Message):
    """Команда для входа в админ-панель"""
    if not is_admin(message.from_user.id):
        return await message.answer("❌ У вас нет прав администратора")
    
    await message.answer(
        "🔧 <b>Админ-панель</b>\n\n"
        "Вы можете создавать бесплатные аккаунты на любой срок.",
        parse_mode="HTML",
        reply_markup=get_main_menu(message.from_user.id)
    )

# ===== ОБЫЧНАЯ РЕГИСТРАЦИЯ (как было) =====
@dp.message_handler(Text(equals="📝 Зарегистрироваться"))
async def start_reg(message: types.Message):
    if get_user_by_telegram(message.from_user.id):
        return await message.answer("Вы уже зарегистрированы!")
    
    await RegState.waiting_carwash_name.set()
    await message.answer("Введите название автомойки:")

@dp.message_handler(state=RegState.waiting_carwash_name)
async def process_name(message: types.Message, state: FSMContext):
    login = transliterate(message.text)
    await state.update_data(carwash=message.text, login=login)
    await RegState.waiting_owner_name.set()
    await message.answer(f"Логин: <b>{login}</b>\nВведите ваше имя:", parse_mode="HTML")

@dp.message_handler(state=RegState.waiting_owner_name)
async def process_owner(message: types.Message, state: FSMContext):
    data = await state.get_data()
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    result, error = create_user(
        telegram_id=message.from_user.id,
        login=data['login'],
        password=password,
        carwash_name=data['carwash'],
        owner_name=message.text
    )
    
    await state.finish()
    
    if result:
        await message.answer(
            f"✅ Аккаунт создан!\nЛогин: <code>{result['login']}</code>\nПароль: <code>{result['password']}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer(f"❌ Ошибка: {error}")

# ===== АДМИНСКОЕ СОЗДАНИЕ АККАУНТА =====
@dp.message_handler(Text(equals="➕ Создать бесплатный аккаунт"))
async def admin_start_create(message: types.Message):
    """Админ начинает создание бесплатного аккаунта"""
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Нет доступа")
    
    await AdminCreateState.waiting_carwash_name.set()
    await message.answer(
        "🔧 <b>Создание бесплатного аккаунта</b>\n\n"
        "Введите название автомойки:",
        parse_mode="HTML"
    )

@dp.message_handler(state=AdminCreateState.waiting_carwash_name)
async def admin_process_name(message: types.Message, state: FSMContext):
    login = transliterate(message.text)
    
    # Проверяем, свободен ли логин
    session = Session()
    existing = session.query(User).filter_by(login=login).first()
    session.close()
    
    if existing:
        login = f"{login}{random.randint(1,99)}"
    
    await state.update_data(carwash=message.text, login=login)
    await AdminCreateState.waiting_owner_name.set()
    
    await message.answer(
        f"Логин будет: <b>{login}</b>\n\n"
        f"Введите имя владельца:",
        parse_mode="HTML"
    )

@dp.message_handler(state=AdminCreateState.waiting_owner_name)
async def admin_process_owner(message: types.Message, state: FSMContext):
    await state.update_data(owner=message.text)
    await AdminCreateState.waiting_days.set()
    
    await message.answer(
        "На сколько дней активировать подписку?\n"
        "Введите число (например: 30, 90, 365):"
    )

@dp.message_handler(state=AdminCreateState.waiting_days)
async def admin_process_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0 or days > 3650:  # Максимум 10 лет
            raise ValueError()
    except ValueError:
        await message.answer("❌ Введите корректное число дней (1-3650):")
        return
    
    data = await state.get_data()
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    # Создаем пользователя БЕЗ telegram_id (это аккаунт для другого человека)
    from database import create_user_admin
    
    result, error = create_user_admin(
        login=data['login'],
        password=password,
        carwash_name=data['carwash'],
        owner_name=data['owner'],
        days=days
    )
    
    await state.finish()
    
    if result:
        end_date = datetime.now() + timedelta(days=days)
        await message.answer(
            f"✅ <b>Бесплатный аккаунт создан!</b>\n\n"
            f"🏢 Автомойка: {data['carwash']}\n"
            f"👤 Владелец: {data['owner']}\n"
            f"🔑 Логин: <code>{result['login']}</code>\n"
            f"🔒 Пароль: <code>{result['password']}</code>\n"
            f"📅 Подписка до: <b>{end_date.strftime('%d.%m.%Y')}</b> ({days} дней)\n\n"
            f"Отправьте эти данные клиенту!",
            parse_mode="HTML",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer(f"❌ Ошибка: {error}")

@dp.message_handler(Text(equals="📊 Статистика"))
async def admin_stats(message: types.Message):
    """Статистика для админа"""
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Нет доступа")
    
    session = Session()
    total_users = session.query(User).count()
    active_subs = session.query(User).filter(
        User.subscription_end > datetime.now()
    ).count()
    session.close()
    
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных подписок: {active_subs}"
    )

@dp.message_handler(Text(equals="🔙 Обычное меню"))
async def back_to_user_menu(message: types.Message):
    """Вернуться в обычное меню"""
    await message.answer("Главное меню:", reply_markup=get_main_menu(message.from_user.id))

# ===== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (без изменений) =====
@dp.message_handler(Text(equals=["💳 Оплатить подписку", "💳 Продлить подписку"]))
async def buy_sub(message: types.Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user:
        return await message.answer("Сначала зарегистрируйтесь!")
    
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ 1 мес (бесплатно)", callback_data="sub_1"))
    kb.add(types.InlineKeyboardButton("✅ 6 мес (бесплатно)", callback_data="sub_6"))
    kb.add(types.InlineKeyboardButton("✅ 12 мес (бесплатно)", callback_data="sub_12"))
    
    await message.answer("Выберите период:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("sub_"))
async def process_pay(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[1])
    end_date = update_subscription(callback.from_user.id, months)
    
    if end_date:
        await callback.message.edit_text(f"✅ Подписка активирована до: {end_date.strftime('%d.%m.%Y')}")
        await callback.message.answer("Готово!", reply_markup=get_main_menu(callback.from_user.id))
    else:
        await callback.answer("Ошибка!")

@dp.message_handler(Text(equals=["ℹ️ Моя подписка", "ℹ️ Мой аккаунт"]))
async def info(message: types.Message):
    info = get_user_info(message.from_user.id)
    if not info:
        return await message.answer("Сначала зарегистрируйтесь!")
    
    await message.answer(
        f"🔑 Логин: <code>{info['login']}</code>\n🔒 Пароль: <code>{info['password']}</code>\n📅 {info['status']}",
        parse_mode="HTML"
    )

@dp.message_handler(Text(equals="❌ Отменить подписку"))
async def cancel(message: types.Message):
    cancel_subscription(message.from_user.id)
    await message.answer("❌ Подписка отменена", reply_markup=get_main_menu(message.from_user.id))

# ===== ЗАПУСК =====
async def on_startup(dp):
    if RENDER_HOST:
        await bot.set_webhook(f"https://{RENDER_HOST}/webhook/{TOKEN}")
        logging.info(f"Webhook установлен")

async def on_shutdown(dp):
    await bot.delete_webhook()

if __name__ == "__main__":
    if RENDER_HOST:
        executor.start_webhook(
            dispatcher=dp,
            webhook_path=f'/webhook/{TOKEN}',
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host='0.0.0.0',
            port=PORT,
        )
    else:
        executor.start_polling(dp, skip_updates=True)