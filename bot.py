import asyncio
import logging
import os
import random
import string
from datetime import datetime

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiohttp import web

from database import init_db, create_user, get_user_by_telegram, update_subscription, cancel_subscription, get_user_info

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
# ===== WEBHOOK НАСТРОЙКИ (для Render) =====
WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_HOSTNAME')  # Render дает автоматически
WEBHOOK_PATH = f'/webhook/{TOKEN}'  # Уникальный путь
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

if not TOKEN:
    raise ValueError("BOT_TOKEN не установлен! Добавь переменную в Render.")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация базы данных
init_db()

async def on_startup(dp):
    # Устанавливаем webhook
    if WEBHOOK_HOST:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        print("⚠️ Webhook host не найден, используем polling")
    
    # Запускаем health check сервер
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

async def on_shutdown(dp):
    # Удаляем webhook при остановке
    await bot.delete_webhook()
    print("❌ Webhook удален")

# ===== ТРАНСЛИТЕРАЦИЯ =====
def transliterate(name):
    """Превращает 'Солнце' в 'Solntse'"""
    letters = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        ' ': '', '-': '', '_': ''
    }
    result = ""
    for char in name.lower():
        result += letters.get(char, char)
    # Убираем все не-буквы и не-цифры
    result = ''.join(c for c in result if c.isalnum())
    return result.capitalize()[:20]

# ===== КЛАВИАТУРЫ =====
def get_main_menu(telegram_id):
    """Главное меню в зависимости от статуса"""
    user = get_user_by_telegram(telegram_id)
    
    if not user:
        # Не зарегистрирован
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add(types.KeyboardButton("📝 Зарегистрироваться"))
        return keyboard
    
    # Проверяем подписку
    has_sub = user.subscription_end and user.subscription_end > datetime.now()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    if has_sub:
        keyboard.add(types.KeyboardButton("💳 Продлить подписку"))
        keyboard.add(types.KeyboardButton("ℹ️ Моя подписка"))
        keyboard.add(types.KeyboardButton("❌ Отменить подписку"))
    else:
        keyboard.add(types.KeyboardButton("💳 Оплатить подписку"))
        keyboard.add(types.KeyboardButton("ℹ️ Мой аккаунт"))
    
    return keyboard

# ===== СОСТОЯНИЯ ДЛЯ РЕГИСТРАЦИИ =====
class RegState(StatesGroup):
    waiting_carwash_name = State()
    waiting_owner_name = State()

# ===== КОМАНДЫ =====
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для управления автомойкой.\n\n"
        "Для начала работы необходимо зарегистрироваться.",
        reply_markup=get_main_menu(message.from_user.id)
    )

# ===== РЕГИСТРАЦИЯ =====
@dp.message_handler(Text(equals="📝 Зарегистрироваться"))
async def start_registration(message: types.Message):
    # Проверяем, не зарегистрирован ли уже
    existing = get_user_by_telegram(message.from_user.id)
    if existing:
        await message.answer("Вы уже зарегистрированы!")
        return
    
    await RegState.waiting_carwash_name.set()
    await message.answer("Введите название вашей автомойки (например: 'Солнце'):")

@dp.message_handler(state=RegState.waiting_carwash_name)
async def process_carwash_name(message: types.Message, state: FSMContext):
    carwash_name = message.text.strip()
    
    if len(carwash_name) < 2:
        await message.answer("Название слишком короткое! Введите полное название:")
        return
    
    login = transliterate(carwash_name)
    
    # Проверяем, свободен ли логин
    from database import Session, User
    session = Session()
    existing_login = session.query(User).filter_by(login=login).first()
    session.close()
    
    if existing_login:
        # Добавляем цифру к логину если занят
        login = f"{login}{random.randint(1,99)}"
    
    await state.update_data(carwash_name=carwash_name, login=login)
    await RegState.waiting_owner_name.set()
    
    await message.answer(
        f"✅ Отлично! Ваш логин для входа на сайт будет: <b>{login}</b>\n\n"
        f"Теперь введите ваше имя (владельца):",
        parse_mode="HTML"
    )

@dp.message_handler(state=RegState.waiting_owner_name)
async def process_owner_name(message: types.Message, state: FSMContext):
    owner_name = message.text.strip()
    data = await state.get_data()
    
    # Генерируем пароль
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    # Создаем пользователя в базе
    user_data, error = create_user(
        telegram_id=message.from_user.id,
        login=data['login'],
        password=password,
        carwash_name=data['carwash_name'],
        owner_name=owner_name
    )
    
    await state.finish()
    
    if error:
        if "уже занят" in error:
            await message.answer(
                "❌ Ошибка: такой логин уже существует. Попробуйте изменить название автомойки.",
                reply_markup=get_main_menu(message.from_user.id)
            )
        else:
            await message.answer(
                f"❌ Ошибка базы данных: {error}\nПопробуйте позже.",
                reply_markup=get_main_menu(message.from_user.id)
            )
        return
    
    # Успешная регистрация
    await message.answer(
        f"🎉 <b>Аккаунт успешно создан и сохранен в базе!</b>\n\n"
        f"🏢 Автомойка: {data['carwash_name']}\n"
        f"👤 Владелец: {owner_name}\n"
        f"🔑 Логин: <code>{user_data['login']}</code>\n"
        f"🔒 Пароль: <code>{user_data['password']}</code>\n\n"
        f"⚠️ <b>Сохраните эти данные!</b> Они понадобятся для входа на сайт.\n\n"
        f"Теперь вы можете активировать тестовую подписку.",
        parse_mode="HTML",
        reply_markup=get_main_menu(message.from_user.id)
    )

# ===== ОПЛАТА (ТЕСТОВЫЙ РЕЖИМ) =====
@dp.message_handler(Text(equals=["💳 Оплатить подписку", "💳 Продлить подписку"]))
async def show_subscription_options(message: types.Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь!")
        return
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ 1 месяц (бесплатно)", callback_data="sub_1"))
    keyboard.add(types.InlineKeyboardButton("✅ 6 месяцев (бесплатно)", callback_data="sub_6"))
    keyboard.add(types.InlineKeyboardButton("✅ 12 месяцев (бесплатно)", callback_data="sub_12"))
    
    await message.answer(
        "💳 <b>Выбор подписки</b>\n\n"
        "🧪 Тестовый режим: подписка бесплатная!\n"
        "Выберите период:",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[1])
    
    end_date = update_subscription(callback.from_user.id, months)
    
    if end_date:
        await bot.edit_message_text(
            f"✅ <b>Подписка активирована!</b>\n\n"
            f"📅 Действует до: {end_date.strftime('%d.%m.%Y')}\n"
            f"💰 Списано: 0₽ (тестовый режим)\n\n"
            f"Теперь вы можете войти на сайт с вашим логином и паролем.",
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            parse_mode="HTML"
        )
        
        # Обновляем клавиатуру
        await callback.message.answer(
            "Главное меню:",
            reply_markup=get_main_menu(callback.from_user.id)
        )
    else:
        await bot.answer_callback_query(
            callback.id,
            text="❌ Ошибка активации подписки"
        )
    
    await bot.answer_callback_query(callback.id)

# ===== ИНФО =====
@dp.message_handler(Text(equals=["ℹ️ Моя подписка", "ℹ️ Мой аккаунт"]))
async def show_info(message: types.Message):
    info = get_user_info(message.from_user.id)
    
    if not info:
        await message.answer("Сначала зарегистрируйтесь!", reply_markup=get_main_menu(message.from_user.id))
        return
    
    await message.answer(
        f"📊 <b>Информация об аккаунте</b>\n\n"
        f"🏢 Автомойка: {info['carwash_name']}\n"
        f"👤 Владелец: {info['owner_name']}\n"
        f"🔑 Логин: <code>{info['login']}</code>\n"
        f"🔒 Пароль: <code>{info['password']}</code>\n\n"
        f"📅 {info['status']}",
        parse_mode="HTML"
    )

# ===== ОТМЕНА ПОДПИСКИ =====
@dp.message_handler(Text(equals="❌ Отменить подписку"))
async def cancel_sub(message: types.Message):
    success = cancel_subscription(message.from_user.id)
    if success:
        await message.answer(
            "❌ Подписка отменена.\nВы можете активировать её снова в любое время.",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        await message.answer(
            "Не удалось отменить подписку (возможно, она уже неактивна).",
            reply_markup=get_main_menu(message.from_user.id)
        )

# ===== WEB SERVER (для Render) =====
async def health_check(request):
    return web.Response(text="Bot is running! PostgreSQL connected!")

async def on_startup(dp):
    # Запускаем веб-сервер для health checks
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Web server started on port {PORT}")

if __name__ == "__main__":
    if WEBHOOK_HOST:
        # Webhook режим (для Render production)
        from aiogram import executor
        executor.start_webhook(
            dispatcher=dp,
            webhook_path=WEBHOOK_PATH,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host='0.0.0.0',
            port=PORT,
        )
    else:
        # Polling режим (для локального теста)
        from aiogram import executor
        executor.start_polling(
            dp, 
            skip_updates=True, 
            on_startup=on_startup,
            reset_webhook=True  # Сбрасываем webhook если был
        )