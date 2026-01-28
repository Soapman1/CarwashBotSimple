import asyncio
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.filters import Text
from aiohttp import web
import random
import string

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# Бот и диспетчер (aiogram 2.x стиль)
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ===== БАЗА ДАННЫХ (в памяти) =====
users = {}

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
    return result.capitalize()[:20]

# ===== КЛАВИАТУРЫ =====
def get_menu(user_id):
    if user_id not in users:
        return types.ReplyKeyboardMarkup(resize_keyboard=True).add("📝 Зарегистрироваться")
    
    sub_end = users[user_id].get("sub_end")
    if sub_end and datetime.fromisoformat(sub_end) > datetime.now():
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("💳 Продлить подписку")
        kb.row("ℹ️ Моя подписка")
        kb.row("❌ Отменить подписку")
        return kb
    else:
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("💳 Оплатить подписку")
        kb.row("ℹ️ Мой аккаунт")
        return kb

# ===== СОСТОЯНИЯ =====
class RegState(StatesGroup):
    waiting_name = State()
    waiting_owner = State()

# ===== ОБРАБОТЧИКИ =====
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Нажми '📝 Зарегистрироваться' чтобы создать аккаунт.",
        reply_markup=get_menu(message.from_user.id)
    )

@dp.message_handler(Text(equals="📝 Зарегистрироваться"))
async def start_reg(message: types.Message):
    await RegState.waiting_name.set()
    await message.answer("Введите название вашей автомойки (например: 'Саларьево'):")

@dp.message_handler(state=RegState.waiting_name)
async def get_carwash_name(message: types.Message, state: FSMContext):
    name = message.text
    login = transliterate(name)
    
    await state.update_data(carwash=name, login=login)
    await RegState.waiting_owner.set()
    
    await message.answer(
        f"✅ Ваш логин будет: <b>{login}</b>\n\nТеперь введите ваше имя:",
        parse_mode="HTML"
    )

@dp.message_handler(state=RegState.waiting_owner)
async def get_owner(message: types.Message, state: FSMContext):
    data = await state.get_data()
    owner = message.text
    login = data["login"]
    
    # Генерируем пароль
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    
    users[message.from_user.id] = {
        "carwash": data["carwash"],
        "owner": owner,
        "login": login,
        "password": password,
        "sub_end": None
    }
    
    await state.finish()
    
    await message.answer(
        f"🎉 <b>Аккаунт создан!</b>\n\n"
        f"🏢 Автомойка: {data['carwash']}\n"
        f"👤 Владелец: {owner}\n"
        f"🔑 Логин: <code>{login}</code>\n"
        f"🔒 Пароль: <code>{password}</code>\n\n"
        f"⚠️ <b>Сохраните эти данные!</b>",
        parse_mode="HTML",
        reply_markup=get_menu(message.from_user.id)
    )

# ===== ОПЛАТА (БЕСПЛАТНО) =====
@dp.message_handler(Text(equals=["💳 Оплатить подписку", "💳 Продлить подписку"]))
async def buy_sub(message: types.Message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ 1 месяц (тест)", callback_data="pay_1"))
    markup.add(types.InlineKeyboardButton("✅ 6 месяцев (тест)", callback_data="pay_6"))
    
    await message.answer("Выберите период (сейчас бесплатно):", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data.startswith("pay_"))
async def process_payment(callback: types.CallbackQuery):
    months = int(callback.data.split("_")[1])
    end_date = datetime.now() + timedelta(days=30*months)
    users[callback.from_user.id]["sub_end"] = end_date.isoformat()
    
    await bot.edit_message_text(
        f"✅ Подписка активирована до: {end_date.strftime('%d.%m.%Y')}",
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id
    )
    await callback.message.answer("Готово!", reply_markup=get_menu(callback.from_user.id))

@dp.message_handler(Text(equals=["ℹ️ Моя подписка", "ℹ️ Мой аккаунт"]))
async def info(message: types.Message):
    user = users.get(message.from_user.id)
    if not user:
        return await message.answer("Сначала зарегистрируйтесь!")
    
    sub_end = user.get("sub_end")
    status = f"✅ До {datetime.fromisoformat(sub_end).strftime('%d.%m.%Y')}" if sub_end else "❌ Нет"
    
    await message.answer(
        f"📊 <b>Информация</b>\n\n"
        f"🏢 Автомойка: {user['carwash']}\n"
        f"🔑 Логин: <code>{user['login']}</code>\n"
        f"🔒 Пароль: <code>{user['password']}</code>\n"
        f"📅 Подписка: {status}",
        parse_mode="HTML"
    )

@dp.message_handler(Text(equals="❌ Отменить подписку"))
async def cancel_sub(message: types.Message):
    if message.from_user.id in users:
        users[message.from_user.id]["sub_end"] = None
    await message.answer("❌ Подписка отменена", reply_markup=get_menu(message.from_user.id))

# ===== WEB SERVER для Render =====
async def health_check(request):
    return web.Response(text="OK")

async def on_startup(dp):
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)