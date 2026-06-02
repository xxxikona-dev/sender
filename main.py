import asyncio
import logging
import random
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pyrogram import Client
from pyrogram.enums import ChatAction
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
    UserDeactivated,
    AuthKeyUnregistered,
)

import config
import database as db

# Настройка вывода логов в консоль
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# БОТ ПОДТЯГИВАЕТ ТОКЕН ИЗ НАСТРОЕК ХОСТИНГА (ОКРУЖЕНИЯ)
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Переменная окружения 'BOT_TOKEN' не найдена на хостинге!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Словарь для временного удержания объектов авторизации Pyrogram {chat_id: client_instance}
active_signups = {}

# Глобальное хранилище индивидуальных настроек пользователей {user_id: {настройки}}
users_mailing_configs = {}

def get_user_settings(user_id: int) -> dict:
    """Возвращает или инициализирует персональные настройки для конкретного пользователя"""
    if user_id not in users_mailing_configs:
        users_mailing_configs[user_id] = {
            "text": "Привет! Это стандартный текст рассылки. Измените его в меню.",
            "min_delay": 60,
            "max_delay": 90,
            "max_waves": 0,
            "enable_typing": True,
            "is_running": False,
            "current_wave": 0
        }
    return users_mailing_configs[user_id]


def get_random_proxy_config() -> dict | None:
    """Читает файл proxies.txt и возвращает случайный прокси в формате для Pyrogram (HTTP)"""
    file_path = "proxies.txt"
    
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return None
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            proxies = [line.strip() for line in f if line.strip()]
            
        if not proxies:
            return None
            
        random_proxy = random.choice(proxies)
        parts = random_proxy.split(":")
        
        if len(parts) == 4:
            return {
                "scheme": "http",  # Используем HTTP. Смени на socks5, если прокси этого типа
                "hostname": parts[0],
                "port": int(parts[1]),
                "username": parts[2],
                "password": parts[3]
            }
    except Exception as e:
        logger.error(f"[Proxy] Ошибка при чтении или парсинге файла прокси: {e}")
        
    return None


# --- СОСТОЯНИЯ (FSM) ---
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()


class GroupStates(StatesGroup):
    waiting_for_links = State()


class TextStates(StatesGroup):
    waiting_for_text = State()


class SettingsStates(StatesGroup):
    waiting_for_min = State()
    waiting_for_max = State()
    waiting_for_waves = State()


# --- ИНТЕРФЕЙСНЫЕ КНОПКИ (КЛАВИАТУРЫ) ---
def get_main_menu(user_id: int):
    settings = get_user_settings(user_id)
    status = "🟢 ЗАПУЩЕНА" if settings["is_running"] else "🔴 ОСТАНОВЛЕНА"
    wave_limit = "Безлимит" if settings["max_waves"] == 0 else f"{settings['max_waves']}"
    
    text = (
        f"🤖 **ПАНЕЛЬ УПРАВЛЕНИЯ РАССЫЛКОЙ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус воркеров:  {status}\n"
        f"Текущая волна:   {settings['current_wave']} из {wave_limit}\n"
        f"Задержка волн:  {settings['min_delay']}-{settings['max_delay']} сек.\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Выберите нужное действие в меню ниже:"
    )
    
    buttons = [
        [
            InlineKeyboardButton(text="📱 Добавить аккаунт", callback_data="add_account"),
            InlineKeyboardButton(text="📝 Текст рассылки", callback_data="change_text")
        ],
        [
            InlineKeyboardButton(text="👥 Управление группами", callback_data="manage_groups"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="show_settings")
        ],
        [
            InlineKeyboardButton(text="🚀 Запустить", callback_data="start_mailing"),
            InlineKeyboardButton(text="🛑 Остановить", callback_data="stop_mailing")
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_menu(user_id: int):
    settings = get_user_settings(user_id)
    typing_status = "✅ Включена" if settings["enable_typing"] else "❌ Выключена"
    wave_limit = "Безлимит" if settings["max_waves"] == 0 else f"{settings['max_waves']} волн"
    
    text = (
        f"⚙️ **ГИБКИЕ НАСТРОЙКИ СПАМЕРА**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Мин. задержка: **{settings['min_delay']} сек.**\n"
        f"⏱ Макс. задержка: **{settings['max_delay']} сек.**\n"
        f"🔄 Лимит кругов: **{wave_limit}**\n"
        f"⌨️ Имитация ввода текста: **{typing_status}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Нажимайте на кнопки ниже, чтобы изменить параметры:"
    )
    
    buttons = [
        [
            InlineKeyboardButton(text="⏱ Изм. мин. задержку", callback_data="set_min_delay"),
            InlineKeyboardButton(text="⏱ Изм. макс. задержку", callback_data="set_max_delay")
        ],
        [
            InlineKeyboardButton(text="🔄 Поставить лимит волн", callback_data="set_wave_limit"),
            InlineKeyboardButton(text="⌨️ Переключить Typing", callback_data="toggle_typing")
        ],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_menu():
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить группами списком", callback_data="add_groups")],
        [InlineKeyboardButton(text="🗑 Очистить весь список чатов", callback_data="clear_groups")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_inline(to_settings=False):
    target = "show_settings" if to_settings else "back_to_menu"
    buttons = [[InlineKeyboardButton(text="⬅️ Отмена и назад", callback_data=target)]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text, markup = get_main_menu(message.from_user.id)
    await message.answer(text, parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_main_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


# --- ИЗМЕНЕНИЕ ТЕКСТА РАССЫЛКИ ---

@dp.callback_query(F.data == "change_text")
async def change_text_cmd(callback: types.CallbackQuery, state: FSMContext):
    settings = get_user_settings(callback.from_user.id)
    current_text = settings["text"]
    
    text = (
        f"📝 **РЕДАКТИРОВАНИЕ ТЕКСТА РАССЫЛКИ**\n\n"
        f"📌 **Текущий сохраненный текст:**\n"
        f"```\n{current_text}\n```\n"
        f"📥 Отправьте новое сообщение в чат, чтобы перезаписать его."
    )
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_inline())
    await state.set_state(TextStates.waiting_for_text)
    await state.update_data(menu_msg_id=callback.message.message_id)
    await callback.answer()


@dp.message(TextStates.waiting_for_text)
async def process_new_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    settings = get_user_settings(message.from_user.id)
    settings["text"] = message.text
    
    try: await message.delete()
    except Exception: pass
        
    text, markup = get_main_menu(message.from_user.id)
    if menu_msg_id:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Текст успешно обновлен!**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)
    await state.clear()


# --- УПРАВЛЕНИЕ ГРУППАМИ ---

@dp.callback_query(F.data == "manage_groups")
async def manage_groups_cmd(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    count = len(groups)

    if count == 0:
        list_str = "📂 **УПРАВЛЕНИЕ ГРУППАМИ**\n\n❌ Список групп в вашей базе данных полностью пуст."
    else:
        preview = groups[:15]
        list_str = f"📂 **УПРАВЛЕНИЕ ГРУППАМИ**\n\n📊 Всего чатов в вашей базе: **{count}**\n\n📌 **Превью списка:**\n"
        list_str += "\n".join(preview)
        if count > 15:
            list_str += "\n... и остальные чаты."

    await callback.message.edit_text(list_str, reply_markup=get_groups_menu())
    await callback.answer()


@dp.callback_query(F.data == "add_groups")
async def start_add_groups(callback: types.CallbackQuery, state: FSMContext):
    text = (
        "📥 **ИМПОРТ СПИСКА ГРУПП**\n\n"
        "Отправьте список ссылок. Каждая новая ссылка или юзернейм должны быть **с новой строки**."
    )
    await callback.message.edit_text(text, reply_markup=get_back_inline())
    await state.set_state(GroupStates.waiting_for_links)
    await state.update_data(menu_msg_id=callback.message.message_id)
    await callback.answer()


@dp.message(GroupStates.waiting_for_links)
async def process_groups_list(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    links = message.text.split("\n")
    added_count = 0

    for link in links:
        cleaned_link = link.strip()
        if cleaned_link:
            await db.add_group(message.from_user.id, cleaned_link)
            added_count += 1

    try: await message.delete()
    except Exception: pass

    text, markup = get_main_menu(message.from_user.id)
    if menu_msg_id:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Успешно импортировано групп: {added_count}**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)
    await state.clear()


@dp.callback_query(F.data == "clear_groups")
async def clear_groups_cmd(callback: types.CallbackQuery):
    await db.clear_groups(callback.from_user.id)
    text = "🗑 **УПРАВЛЕНИЕ ГРУППАМИ**\n\n🗑 Все ваши группы были успешно удалены из базы данных."
    await callback.message.edit_text(text, reply_markup=get_groups_menu())
    await callback.answer()


# --- ПОДМЕНЮ НАСТРОЕК ---

@dp.callback_query(F.data == "show_settings")
async def show_settings_cmd(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_settings_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "toggle_typing")
async def toggle_typing_handler(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    settings["enable_typing"] = not settings["enable_typing"]
    text, markup = get_settings_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.in_({"set_min_delay", "set_max_delay", "set_wave_limit"}))
async def set_numbers_fields(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=callback.message.message_id)
    
    if callback.data == "set_min_delay":
        await callback.message.edit_text("⏱ Введите **минимальную** задержку между волнами в секундах:", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
        await state.set_state(SettingsStates.waiting_for_min)
    elif callback.data == "set_max_delay":
        await callback.message.edit_text("⏱ Введите **максимальную** задержку между волнами в секундах:", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
        await state.set_state(SettingsStates.waiting_for_max)
    elif callback.data == "set_wave_limit":
        await callback.message.edit_text("🔄 Сколько кругов рассылки сделать? Введите число или `0` для безлимита:", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
        await state.set_state(SettingsStates.waiting_for_waves)
    await callback.answer()


@dp.message(SettingsStates.waiting_for_min)
@dp.message(SettingsStates.waiting_for_max)
@dp.message(SettingsStates.waiting_for_waves)
async def process_numeric_settings(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    val_text = message.text.strip()
    try: await message.delete()
    except Exception: pass
    
    if not val_text.isdigit():
        return
        
    val = int(val_text)
    settings = get_user_settings(message.from_user.id)
    
    if current_state == SettingsStates.waiting_for_min:
        settings["min_delay"] = val
    elif current_state == SettingsStates.waiting_for_max:
        if val < settings["min_delay"]:
            settings["max_delay"] = settings["min_delay"] + 10
        else:
            settings["max_delay"] = val
    elif current_state == SettingsStates.waiting_for_waves:
        settings["max_waves"] = val

    text, markup = get_settings_menu(message.from_user.id)
    if menu_msg_id:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=text, parse_mode="Markdown", reply_markup=markup)
    await state.clear()


# --- ПОДКЛЮЧЕНИЕ ЮЗЕРБОТОВ (АВТОРИЗАЦИЯ С РОТАЦИЕЙ ПРОКСИ) ---

@dp.callback_query(F.data == "add_account")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    text = "📱 **АВТОРИЗАЦИЯ ЮЗЕРБОТА**\n\nВведите номер телефона аккаунта (например, `+79991234567`):"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_inline())
    await state.set_state(AuthStates.waiting_for_phone)
    await state.update_data(menu_msg_id=callback.message.message_id)
    await callback.answer()


@dp.message(AuthStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    phone = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    
    try: await message.delete()
    except Exception: pass

    proxy_config = get_random_proxy_config()
    if proxy_config:
        logger.info(f"[Proxy] Для {phone} выбран IP: {proxy_config['hostname']}:{proxy_config['port']}")
    else:
        logger.warning(f"[Proxy] Файл proxies.txt пуст. Подключение напрямую.")

    client = Client(
        name=f"auth_{user_id}_{phone}", 
        api_id=config.API_ID, 
        api_hash=config.API_HASH, 
        in_memory=True,
        proxy=proxy_config
    )
    
    try:
        logger.info(f"[{phone}] Инициализация подключения...")
        await client.connect()
        logger.info(f"[{phone}] Запрос кода авторизации...")
        code_hash = await client.send_code(phone)
        logger.info(f"[{phone}] Код успешно запрошен на сервере Telegram.")
        
        await state.update_data(phone=phone, phone_code_hash=code_hash.phone_code_hash)
        active_signups[message.chat.id] = client

        text = f"📩 **КОД ПОДТВЕРЖДЕНИЯ**\n\nКод отправлен на `{phone}`.\nВведите полученный код:"
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=text, parse_mode="Markdown", reply_markup=get_back_inline())
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка на этапе отправки кода: {e}")
        text, markup = get_main_menu(user_id)
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"❌ **Ошибка отправки кода:** {e}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
        
        try: await client.disconnect()
        except Exception: pass
        await state.clear()


@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    menu_msg_id = data.get("menu_msg_id")
    user_id = message.from_user.id

    try: await message.delete()
    except Exception: pass

    client = active_signups.get(message.chat.id)
    text, markup = get_main_menu(user_id)
    
    if not client:
        await message.answer("⚠️ Сессия авторизации устарела. Попробуйте заново.")
        await state.clear()
        return

    try:
        await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
        string_session = await client.export_session_string()
        await db.add_account(user_id, phone, string_session)

        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Юзербот {phone} успешно привязан!**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
        active_signups.pop(message.chat.id, None)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка при входе: {e}")
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"❌ **Ошибка авторизации:** {e}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    finally:
        try: await client.disconnect()
        except Exception: pass
        await state.clear()


# --- УПРАВЛЕНИЕ РАССЫЛКОЙ ---

@dp.callback_query(F.data == "start_mailing")
async def start_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    
    if settings["is_running"]:
        await callback.answer("Ваша рассылка уже активна!", show_alert=True)
        return

    settings["is_running"] = True
    settings["current_wave"] = 0
    
    text, markup = get_main_menu(user_id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    asyncio.create_task(run_mailing_task(user_id, callback.message.chat.id, callback.message.message_id))
    await callback.answer()


@dp.callback_query(F.data == "stop_mailing")
async def stop_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    
    if not settings["is_running"]:
        await callback.answer("Ваша рассылка не была запущена.", show_alert=True)
        return
        
    settings["is_running"] = False
    text, markup = get_main_menu(user_id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


# --- ЯДРО РАССЫЛКИ С ИСПОЛЬЗОВАНИЕМ ПРОКСИ ---

async def run_mailing_task(user_id: int, chat_id: int, message_id: int):
    settings = get_user_settings(user_id)
    accounts = await db.get_accounts(user_id)
    
    if not accounts:
        settings["is_running"] = False
        try:
            text, markup = get_main_menu(user_id)
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⚠️ У вас нет подключенных аккаунтов!\n\n" + text, parse_mode="Markdown", reply_markup=markup)
        except Exception: pass
        return

    while settings["is_running"]:
        if settings["max_waves"] > 0 and settings["current_wave"] >= settings["max_waves"]:
            settings["is_running"] = False
            break

        settings["current_wave"] += 1
        
        try:
            text, markup = get_main_menu(user_id)
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown", reply_markup=markup)
        except Exception: pass

        groups = await db.get_groups(user_id)
        if not groups:
            await asyncio.sleep(10)
            continue

        workers = []
        for phone, session_str in accounts:
            workers.append(send_messages_from_account(user_id, phone, session_str, groups))

        await asyncio.gather(*workers)

        if not settings["is_running"]:
            break

        wave_delay = random.randint(settings["min_delay"], settings["max_delay"])
        for _ in range(wave_delay):
            if not settings["is_running"]:
                break
            await asyncio.sleep(1)

    settings["is_running"] = False
    try:
        text, markup = get_main_menu(user_id)
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown", reply_markup=markup)
    except Exception: pass


async def send_messages_from_account(user_id: int, phone: str, session_str: str, groups: list):
    proxy_config = get_random_proxy_config()
    
    app = Client(
        name=f"{user_id}_{phone}", 
        api_id=config.API_ID, 
        api_hash=config.API_HASH, 
        session_string=session_str, 
        in_memory=True,
        proxy=proxy_config
    )
    settings = get_user_settings(user_id)

    try: await app.start()
    except Exception as e:
        logger.error(f"[{phone}] Ошибка старта клиента во время спама: {e}")
        return

    tasks = []
    for group_url in groups:
        tasks.append(send_to_single_group(app, phone, group_url, settings["text"], settings["enable_typing"]))

    await asyncio.gather(*tasks)
    await app.stop()


async def send_to_single_group(app, phone: str, group_url: str, text: str, enable_typing: bool):
    try:
        chat_peer = group_url.replace("https://t.me/", "").replace("@", "")

        try: chat = await app.get_chat(chat_peer)
        except PeerIdInvalid:
            chat = await app.join_chat(chat_peer)
            await asyncio.sleep(3)

        if enable_typing:
            await app.send_chat_action(chat.id, ChatAction.TYPING)
            await asyncio.sleep(random.randint(2, 5))

        await app.send_message(chat.id, text)
        logger.info(f"[{phone}] Успешно отправлено в {chat_peer}")

    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
    except Exception as e:
        logger.error(f"[{phone}] Не удалось отправить сообщение в {group_url} -> {e}")


# --- СТАРТ СТРУКТУРЫ ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
