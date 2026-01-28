import asyncio
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiohttp
from aiohttp import web

# Настройки из переменных окружения (Render их подставит сам)
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))  # Render даёт порт автоматически

# Логирование
logging.basicConfig(level=logging.INFO)

# Бот и диспетчер
bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ===== БАЗА ДАННЫХ (пока просто в памяти, потом заменим на PostgreSQL) =====
users = {}  # {telegram_id: {"login": "...", "sub_end": "...", ...}}

# ===== ТРАНСЛИТЕРАЦИЯ =====
def transliterate(name):
    """Превращает 'Солнце' в 'Solntse'"""
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
    return result.capitalize()[:20]

# ===== КНОПКИ =====
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_menu(user_id):
    """Возвращает клавиатуру в зависимости от статуса"""
    if user_id not in users:
        # Новый пользователь - только регистрация
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📝 Зарегистрироваться")]], 
            resize_keyboard=True
        )
    
    # Проверяем подписку
    sub_end = users[user_id].get("sub_end")
    if sub_end and datetime.fromisoformat(sub_end) > datetime.now():
        # Активная подписка
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💳 Продлить подписку")],
                [KeyboardButton(text="ℹ️ Моя подписка")],
                [KeyboardButton(text="❌ Отменить подписку")]
            ],
            resize_keyboard=True
        )
    else:
        # Нет подписки
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💳 Оплатить подписку")],
                [KeyboardButton(text="ℹ️ Мой аккаунт")]
            ],
            resize_keyboard=True
        )

# ===== ОБРАБОТЧИКИ =====
class RegState(StatesGroup):
    waiting_name = State()
    waiting_owner = State()

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для управления автомойкой.\n\n"
        "Нажми '📝 Зарегистрироваться' чтобы создать аккаунт.",
        reply_markup=get_menu(message.from_user.id)
    )

@dp.message(F.text == "📝 Зарегистрироваться")
async def start_reg(message: Message, state: FSMContext):
    await state.set_state(RegState.waiting_name)
    await message.answer("Введите название вашей автомойки (например: 'Саларьево'):")

@dp.message(RegState.waiting_name)
async def get_carwash_name(message: Message, state: FSMContext):
    name = message.text
    login = transliterate(name)
    
    await state.update_data(carwash=name, login=login)
    await state.set_state(RegState.waiting_owner)
    
    await message.answer(
        f"✅ Название принято! Ваш логин будет: <b>{login}</b>\n\n"
        f"Теперь введите ваше имя (владельца):"
    )

@dp.message(RegState.waiting_owner)
async def get_owner(message: Message, state: FSMContext):
    data = await state.get_data()
    owner = message.text
    login = data["login"]
    
    # Генерируем пароль
    import random, string
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    # Сохраняем пользователя
    users[message.from_user.id] = {
        "carwash": data["carwash"],
        "owner": owner,
        "login": login,
        "password": password,
        "sub_end": None  # Подписки пока нет
    }
    
    await state.clear()
    
    await message.answer(
        f"🎉 <b>Аккаунт создан!</b>\n\n"
        f"🏢 Автомойка: {data['carwash']}\n"
        f"👤 Владелец: {owner}\n"
        f"🔑 Логин: <code>{login}</code>\n"
        f"🔒 Пароль: <code>{password}</code>\n\n"
        f"⚠️ <b>Сохраните эти данные!</b>\n\n"
        f"Теперь можно активировать тестовую подписку.",
        reply_markup=get_menu(message.from_user.id)
    )

# ===== ОПЛАТА (ЗАГЛУШКА - БЕСПЛАТНО) =====
@dp.message(F.text.in_(["💳 Оплатить подписку", "💳 Продлить подписку"]))
async def buy_sub(message: Message):
    # Создаём кнопки выбора периода
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ 1 месяц (тест)", callback_data="pay_1")],
        [InlineKeyboardButton(text="✅ 6 месяцев (тест)", callback_data="pay_6")]
    ])
    
    await message.answer(
        "💳 <b>Выбор подписки</b>\n\n"
        "Сейчас режим ТЕСТИРОВАНИЯ - подписка бесплатная!\n"
        "Выберите период:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: CallbackQuery):
    months = int(callback.data.split("_")[1])
    
    # Активируем подписку (без реальной оплаты!)
    end_date = datetime.now() + timedelta(days=30*months)
    users[callback.from_user.id]["sub_end"] = end_date.isoformat()
    
    await callback.message.edit_text(
        f"✅ <b>Подписка активирована!</b>\n\n"
        f"📅 Действует до: {end_date.strftime('%d.%m.%Y')}\n"
        f"💰 Списано: 0₽ (тестовый режим)"
    )
    
    await callback.message.answer("Главное меню:", reply_markup=get_menu(callback.from_user.id))

@dp.message(F.text == "ℹ️ Моя подписка")
@dp.message(F.text == "ℹ️ Мой аккаунт")
async def info(message: Message):
    user = users.get(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь!")
        return
    
    sub_end = user.get("sub_end")
    if sub_end:
        end_date = datetime.fromisoformat(sub_end)
        days_left = (end_date - datetime.now()).days
        status = f"✅ Активна (осталось {days_left} дней)"
    else:
        status = "❌ Нет подписки"
    
    await message.answer(
        f"📊 <b>Информация</b>\n\n"
        f"🏢 Автомойка: {user['carwash']}\n"
        f"👤 Владелец: {user['owner']}\n"
        f"🔑 Логин: <code>{user['login']}</code>\n"
        f"🔒 Пароль: <code>{user['password']}</code>\n"
        f"📅 Статус: {status}"
    )

@dp.message(F.text == "❌ Отменить подписку")
async def cancel_sub(message: Message):
    if message.from_user.id in users:
        users[message.from_user.id]["sub_end"] = None
    await message.answer("❌ Подписка отменена", reply_markup=get_menu(message.from_user.id))

# ===== WEB SERVER для RENDER =====
async def health_check(request):
    """Render будет проверять, жив ли сервис"""
    return web.Response(text="Bot is running!")

async def start_web_server():
    """Запускаем веб-сервер для health checks"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    # Запускаем в фоне
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# ===== ЗАПУСК =====
async def main():
    # Запускаем веб-сервер (для Render)
    await start_web_server()
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())