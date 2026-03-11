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

from database import init_db, create_user, get_user_by_telegram, update_subscription, cancel_subscription, get_user_info, create_user_admin, Session, User

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST") or os.getenv("RENDER_EXTERNAL_HOSTNAME")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() in {"1", "true", "yes", "on"}
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "477510130"))

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
    return user_id == ADMIN_ID

# ===== КЛАВИАТУРЫ =====
def get_main_menu(telegram_id, force_user_menu=False):
    """
    Главное меню
    force_user_menu=True - принудительно показать обычное меню (для кнопки "Назад")
    """
    from database import Session, User
    session = Session()
    user = get_user_by_telegram(telegram_id)
    session.close()

    # Если админ хочет обычное меню ИЛИ это не админ
    if force_user_menu or not is_admin(telegram_id):
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
    
    # Админское меню (только если не force_user_menu)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Создать бесплатный аккаунт")
    kb.row("📊 Статистика")
    kb.row("🔙 Обычное меню")
    return kb

# ===== СОСТОЯНИЯ =====
class RegState(StatesGroup):
    waiting_carwash_name = State()
    waiting_owner_name = State()

class AdminCreateState(StatesGroup):
    waiting_carwash_name = State()
    waiting_owner_name = State()
    waiting_days = State()

# ===== КОМАНДЫ (обрабатываются первыми) =====
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Бот для управления автомойкой.",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message_handler(commands=['admin'])
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Нет прав")
    await message.answer("🔧 Админ-панель", reply_markup=get_main_menu(message.from_user.id))

# ===== АДМИНСКИЕ КНОПКИ (должны быть ДО обычных!) =====
@dp.message_handler(Text(equals="➕ Создать бесплатный аккаунт"))
async def admin_start_create(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Нет доступа")
    
    await AdminCreateState.waiting_carwash_name.set()
    await message.answer("🔧 Создание бесплатного аккаунта\n\nВведите название автомойки:")

@dp.message_handler(Text(equals="🔙 Обычное меню"))
async def back_to_menu(message: types.Message):
    # force_user_menu=True - показываем обычное меню даже для админа
    await message.answer("Главное меню:", reply_markup=get_main_menu(message.from_user.id, force_user_menu=True))

@dp.message_handler(state=AdminCreateState.waiting_carwash_name)
async def admin_process_name(message: types.Message, state: FSMContext):
    login = transliterate(message.text)
    
    session = Session()
    existing = session.query(User).filter_by(login=login).first()
    session.close()
    
    if existing:
        login = f"{login}{random.randint(1,99)}"
    
    await state.update_data(carwash=message.text, login=login)
    await AdminCreateState.waiting_owner_name.set()
    await message.answer(f"Логин: {login}\nВведите имя владельца:")

@dp.message_handler(state=AdminCreateState.waiting_owner_name)
async def admin_process_owner(message: types.Message, state: FSMContext):
    await state.update_data(owner=message.text)
    await AdminCreateState.waiting_days.set()
    await message.answer("На сколько дней активировать подписку? (введите число):")

@dp.message_handler(state=AdminCreateState.waiting_days)
async def admin_process_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0 or days > 3650:
            raise ValueError()
    except ValueError:
        await message.answer("❌ Введите число от 1 до 3650:")
        return
    
    data = await state.get_data()
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
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
            f"✅ Бесплатный аккаунт создан!\n\n"
            f"🏢 {data['carwash']}\n"
            f"👤 {data['owner']}\n"
            f"🔑 Логин: {result['login']}\n"
            f"🔒 Пароль: {result['password']}\n"
            f"📅 До: {end_date.strftime('%d.%m.%Y')} ({days} дней)",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer(f"❌ Ошибка: {error}")

@dp.message_handler(Text(equals="📊 Статистика"))
async def admin_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Нет доступа")
    
    session = Session()
    total = session.query(User).count()
    active = session.query(User).filter(User.subscription_end > datetime.now()).count()
    session.close()
    
    await message.answer(f"📊 Всего: {total}\n✅ Активных: {active}")

@dp.message_handler(Text(equals="🔙 Обычное меню"))
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_menu(message.from_user.id))

# ===== ОБЫЧНАЯ РЕГИСТРАЦИЯ =====
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
    await message.answer(f"Логин: {login}\nВведите ваше имя:")

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
            f"✅ Аккаунт создан!\nЛогин: {result['login']}\nПароль: {result['password']}",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer(f"❌ Ошибка: {error}")

# ===== ОПЛАТА =====
@dp.message_handler(Text(equals=["💳 Оплатить подписку", "💳 Продлить подписку"]))
async def buy_sub(message: types.Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user:
        return await message.answer("Сначала зарегистрируйтесь!")
    
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("1 мес", callback_data="sub_1"))
    kb.add(types.InlineKeyboardButton("6 мес", callback_data="sub_6"))
    kb.add(types.InlineKeyboardButton("12 мес", callback_data="sub_12"))
    
    await message.answer("Выберите период:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("sub_"))
async def process_pay(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[1])
    end_date = update_subscription(callback.from_user.id, months)
    
    if end_date:
        await callback.message.edit_text(f"✅ До: {end_date.strftime('%d.%m.%Y')}")
        await callback.message.answer("Готово!", reply_markup=get_main_menu(callback.from_user.id))

@dp.message_handler(Text(equals=["ℹ️ Моя подписка", "ℹ️ Мой аккаунт"]))
async def info(message: types.Message):
    info = get_user_info(message.from_user.id)
    if not info:
        return await message.answer("Сначала зарегистрируйтесь!")
    await message.answer(f"🔑 {info['login']}\n📅 {info['status']}")

@dp.message_handler(Text(equals="❌ Отменить подписку"))
async def cancel(message: types.Message):
    cancel_subscription(message.from_user.id)
    await message.answer("❌ Отменено", reply_markup=get_main_menu(message.from_user.id))

# ===== ЗАПУСК =====
async def on_startup(dp):
    if USE_WEBHOOK and WEBHOOK_HOST:
        webhook_host = WEBHOOK_HOST.replace("https://", "").replace("http://", "").strip("/")
        webhook_url = f"https://{webhook_host}/webhook/{TOKEN}"
        logging.info("Запускаю webhook: %s", webhook_url)
        await bot.set_webhook(webhook_url)
    else:
        logging.info("Webhook отключен, бот запущен в polling режиме")

async def on_shutdown(dp):
    if USE_WEBHOOK:
        await bot.delete_webhook()

if __name__ == "__main__":
    if USE_WEBHOOK and WEBHOOK_HOST:
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
        executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)
