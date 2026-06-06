import asyncio
import logging
import random
import os
import io
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

from pyrogram import Client
from pyrogram.enums import ChatAction
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
)

import config
import database as db
import worker  # Наш фоновый движок для тяжелых задач

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
    """Читает файл proxies.txt и возвращает случайный прокси в формате для Pyrogram"""
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
                "scheme": "http",  # Смени на socks5, если прокси Socks5 типа
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
    status = "🟢 АКТИВЕН" if settings["is_running"] else "🔴 ПРИОСТАНОВЛЕН"
    wave_limit = "Авто" if settings["max_waves"] == 0 else f"{settings['max_waves']}"
    
    text = (
        f"💼 **WORKSPACE MANAGER v3.5**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус процессов:  {status}\n"
        f"Текущий цикл задач:   {settings['current_wave']} из {wave_limit}\n"
        f"Задержка интервалов:  {settings['min_delay']}-{settings['max_delay']} сек.\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Выберите необходимый модуль для настройки конфигурации:"
    )
    
    buttons = [
        [
            InlineKeyboardButton(text="📱 Управление РМ (Сессии)", callback_data="manage_accounts"),
            InlineKeyboardButton(text="📝 Скрипт задачи", callback_data="change_text")
        ],
        [
            InlineKeyboardButton(text="👥 База адресатов", callback_data="manage_groups"),
            InlineKeyboardButton(text="⚙️ Конфигурация", callback_data="show_settings")
        ],
        [
            InlineKeyboardButton(text="📊 Сквозная статистика", callback_data="view_statistics")
        ],
        [
            InlineKeyboardButton(text="⚡ Синхронизировать", callback_data="start_mailing"),
            InlineKeyboardButton(text="🛑 Прервать сессию", callback_data="stop_mailing")
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_menu(user_id: int):
    settings = get_user_settings(user_id)
    typing_status = "✅ Активно" if settings["enable_typing"] else "❌ Отключено"
    wave_limit = "Без ограничений" if settings["max_waves"] == 0 else f"{settings['max_waves']} циклов"
    
    text = (
        f"⚙️ **ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ СЕССИИ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Нижний порог тайминга: **{settings['min_delay']} сек.**\n"
        f"⏱ Верхний порог тайминга: **{settings['max_delay']} сек.**\n"
        f"🔄 Ограничение по итерациям: **{wave_limit}**\n"
        f"⌨️ Предварительная задержка потока: **{typing_status}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Используйте элементы управления для изменения переменных:"
    )
    
    buttons = [
        [
            InlineKeyboardButton(text="⏱ Изм. min задержку", callback_data="set_min_delay"),
            InlineKeyboardButton(text="⏱ Изм. max задержку", callback_data="set_max_delay")
        ],
        [
            InlineKeyboardButton(text="🔄 Лимит итераций", callback_data="set_wave_limit"),
            InlineKeyboardButton(text="⌨️ Переключить задержку", callback_data="toggle_typing")
        ],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back_to_menu")]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_menu(count: int):
    buttons = [
        [InlineKeyboardButton(text="📥 Импортировать список ID/Узлов", callback_data="add_groups")],
        [InlineKeyboardButton(text="📥 Скачать базу .txt", callback_data="download_chats")],
        [InlineKeyboardButton(text="🗑 Сбросить текущую базу", callback_data="clear_groups")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_accounts_keyboard(user_id: int):
    accounts = await db.get_accounts(user_id)
    buttons = []
    
    if not accounts:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\n❌ Нет подключенных аккаунтов шлюзов."
    else:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\nСписок ваших активных шлюзов и их статусы спамблока:"
        for phone, _, status in accounts:
            text += f"\n• `{phone}` — *{status}*"
            buttons.append([InlineKeyboardButton(text=f"⚙️ Управление {phone}", callback_data=f"act_{phone}")])
            
    buttons.append([InlineKeyboardButton(text="📱 Подключить новое РМ", callback_data="add_account")])
    buttons.append([InlineKeyboardButton(text="🛡 Проверить СПАМ-БЛОК", callback_data="check_all_spam")])
    if accounts:
        buttons.append([InlineKeyboardButton(text="💥 Завершить ВСЕ сессии", callback_data="kill_all_sessions")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])
    
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_inline(to_settings=False, to_accounts=False):
    if to_settings:
        target = "show_settings"
    elif to_accounts:
        target = "manage_accounts"
    else:
        target = "back_to_menu"
    buttons = [[InlineKeyboardButton(text="⬅️ Отменить операцию", callback_data=target)]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ХЕНДЛЕРЫ НАВИГАЦИИ И ГЛАВНОГО МЕНЮ ---

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


# --- УПРАВЛЕНИЕ АККАУНТАМИ И СЕССИЯМИ ---

@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("act_"))
async def individual_account_manage(callback: types.CallbackQuery):
    phone = callback.data.replace("act_", "")
    text = f"⚙️ **Управление аккаунтом** `{phone}`\n\nВы можете принудительно деавторизовать данную сессию. Она сотрется из бота и полностью закроется в Telegram."
    buttons = [
        [InlineKeyboardButton(text="🛑 Завершить эту сессию", callback_data=f"kill_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="manage_accounts")]
    ]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("kill_"))
async def kill_single_session_handler(callback: types.CallbackQuery):
    phone = callback.data.replace("kill_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    
    session_str = next((s for p, s, _ in accounts if p == phone), None)
    if session_str:
        await callback.message.edit_text(f"⏳ Разрываем соединение и уничтожаем сессию `{phone}`...", parse_mode="Markdown")
        await worker.terminate_session(phone, session_str)
        
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.answer("✅ Сессия успешно закрыта.", parse_mode="Markdown")
    await callback.message.delete()
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "kill_all_sessions")
async def kill_all_sessions_handler(callback: types.CallbackQuery):
    accounts = await db.get_accounts(callback.from_user.id)
    if not accounts:
        await callback.answer("У вас нет активных сессий.")
        return
        
    await callback.message.edit_text("⏳ Глобальный сброс. Уничтожаем все подключенные сессии...", parse_mode="Markdown")
    for phone, session_str, _ in accounts:
        await worker.terminate_session(phone, session_str)
        
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.answer("💥 Все сессии были успешно аннулированы.", parse_mode="Markdown")
    await callback.message.delete()
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)


# --- СКАНЕР СПАМ-БЛОКА ---

@dp.callback_query(F.data == "check_all_spam")
async def check_all_spam_handler(callback: types.CallbackQuery):
    accounts = await db.get_accounts(callback.from_user.id)
    if not accounts:
        await callback.answer("База подключенных сессий пуста.", show_alert=True)
        return
        
    await callback.message.edit_text("🛡 **Сканирование инфраструктуры...**\n\nВоркер поочередно заходит к @Spambot. Пожалуйста, подождите.", parse_mode="Markdown")
    for phone, session_str, _ in accounts:
        await worker.check_account_spamblock(phone, session_str)
        await asyncio.sleep(1) 
        
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer("✅ Проверка всей сети завершена!", parse_mode="Markdown")
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)


# --- ПОДКЛЮЧЕНИЕ НОВЫХ ШЛЮЗОВ ---

@dp.callback_query(F.data == "add_account")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    text = "📱 **АВТОРИЗАЦИЯ УДАЛЕННОГО РАБОЧЕГО МЕСТА (РМ)**\n\nУкажите телефонный номер шлюза (например, `+79991234567`):"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_back_inline(to_accounts=True))
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
    client = Client(
        name=f"auth_{user_id}_{phone}", 
        api_id=config.API_ID, 
        api_hash=config.API_HASH, 
        in_memory=True,
        proxy=proxy_config
    )
    
    try:
        await client.connect()
        code_hash = await client.send_code(phone)
        await state.update_data(phone=phone, phone_code_hash=code_hash.phone_code_hash)
        active_signups[message.chat.id] = client

        text = f"📩 **ВЕРИФИКАЦИОННЫЙ СЕРТИФИКАТ**\n\nЗапрос направлен на `{phone}`.\nВведите полученный код подтверждения:"
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=text, parse_mode="Markdown", reply_markup=get_back_inline(to_accounts=True))
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        logger.error(f"[{phone}] Исключение авторизации: {e}")
        text, markup = await get_accounts_keyboard(user_id)
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"❌ **Ошибка инициализации шлюза:** {e}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
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
    text, markup = await get_accounts_keyboard(user_id)
    
    if not client:
        await message.answer("⚠️ Сессия авторизации устарела. Попробуйте заново.")
        await state.clear()
        return

    try:
        await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
        string_session = await client.export_session_string()
        await db.add_account(user_id, phone, string_session)

        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Рабочее место {phone} синхронизировано!**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
        active_signups.pop(message.chat.id, None)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка валидации токена: {e}")
        if menu_msg_id:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"❌ **Ошибка кода авторизации:** {e}\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    finally:
        try: await client.disconnect()
        except Exception: pass
        await state.clear()


# --- ИЗМЕНЕНИЕ ШАБЛОНА ТЕКСТА ---

@dp.callback_query(F.data == "change_text")
async def change_text_cmd(callback: types.CallbackQuery, state: FSMContext):
    settings = get_user_settings(callback.from_user.id)
    current_text = settings["text"]
    
    text = (
        f"📝 **КОНФИГУРАЦИЯ ТЕКСТОВОГО СКРИПТА**\n\n"
        f"📌 **Текущий шаблон текста:**\n"
        f"```\n{current_text}\n```\n"
        f"📥 Направьте новое сообщение в диалог для его замены."
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
        await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Скрипт задачи обновлен!**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)
    await state.clear()


# --- УПРАВЛЕНИЕ БАЗОЙ ГРУПП (ВЫГРУЗКА .TXT ВКЛЮЧЕНА) ---

@dp.callback_query(F.data == "manage_groups")
async def manage_groups_cmd(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    count = len(groups)

    if count == 0:
        list_str = "📂 **БАЗА ДАННЫХ АДРЕСАТОВ**\n\n❌ Активные записи в текущей конфигурации отсутствуют."
    else:
        preview = groups[:15]
        list_str = f"📂 **БАЗА ДАННЫХ АДРЕСАТОВ**\n\n📊 Загружено уникальных узлов: **{count}**\n\n📌 **Список элементов (превью):**\n"
        list_str += "\n".join(preview)
        if count > 15:
            list_str += "\n... и остальные элементы."

    await callback.message.edit_text(list_str, reply_markup=get_groups_menu(count))
    await callback.answer()


@dp.callback_query(F.data == "add_groups")
async def start_add_groups(callback: types.CallbackQuery, state: FSMContext):
    text = (
        "📥 **ИМПОРТ НОВЫХ УЗЛОВ ИДЕНТИФИКАЦИИ**\n\n"
        "Отправьте список ссылок на группы. Каждая новая ссылка должна начинаться **с новой строки**."
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
        await bot.edit_message_text(chat_id=message.chat.id, message_id=menu_msg_id, text=f"✅ **Успешно импортировано записей: {added_count}**\n\n" + text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)
    await state.clear()


@dp.callback_query(F.data == "download_chats")
async def download_chats_handler(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    if not groups:
        await callback.answer("Ваша база чатов пуста, нечего выгружать.", show_alert=True)
        return
        
    await callback.answer("Формирую выгрузку...")
    text_data = "\n".join(groups)
    file_buffer = io.BytesIO(text_data.encode('utf-8'))
    document = BufferedInputFile(file_buffer.read(), filename="database_chats.txt")
    
    await bot.send_document(
        chat_id=callback.message.chat.id,
        document=document,
        caption=f"📂 Полная копия базы данных чатов.\nВсего записей: {len(groups)}"
    )


@dp.callback_query(F.data == "clear_groups")
async def clear_groups_cmd(callback: types.CallbackQuery):
    await db.clear_groups(callback.from_user.id)
    text = "🗑 **БАЗА ДАННЫХ АДРЕСАТОВ**\n\n🗑 Таблицы адресов были успешно очищены."
    await callback.message.edit_text(text, reply_markup=get_groups_menu(0))
    await callback.answer()


# --- ПОДМЕНЮ НАСТРОЕК ТАЙМИНГОВ ---

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
        await callback.message.edit_text("⏱ Укажите **минимальный** интервал задержки потока (сек):", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
        await state.set_state(SettingsStates.waiting_for_min)
    elif callback.data == "set_max_delay":
        await callback.message.edit_text("⏱ Укажите **максимальный** интервал задержки потока (сек):", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
        await state.set_state(SettingsStates.waiting_for_max)
    elif callback.data == "set_wave_limit":
        await callback.message.edit_text("🔄 Укажите число необходимых итераций (или `0` для бесконечного режима):", parse_mode="Markdown", reply_markup=get_back_inline(to_settings=True))
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


# --- ВЫВОД СКВОЗНОЙ СТАТИСТИКИ (ЧАС / ДЕНЬ / НЕДЕЛЯ / МЕСЯЦ) ---

@dp.callback_query(F.data == "view_statistics")
async def view_stats_handler(callback: types.CallbackQuery):
    stats = await db.get_stats(callback.from_user.id)
    
    text = (
        f"📊 **СКВОЗНАЯ СТАТИСТИКА ДОСТАВКИ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Последний час: **{stats['hour']}** сообщ.\n"
        f"📅 Текущие сутки: **{stats['day']}** сообщ.\n"
        f"🗓 За 7 дней: **{stats['week']}** сообщ.\n"
        f"📈 За 30 дней: **{stats['month']}** сообщ.\n"
        f"🏆 За все время: **{stats['all']}** сообщ.\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    buttons = [[InlineKeyboardButton(text="⬅️ Вернуться в меню", callback_data="back_to_menu")]]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- ЗАПУСК И ОСТАНОВКА ПРОЦЕССА РАССЫЛКИ ---

@dp.callback_query(F.data == "start_mailing")
async def start_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    
    if settings["is_running"]:
        await callback.answer("Сессия процессов уже активна!", show_alert=True)
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
        await callback.answer("Активные процессы рассылки не обнаружены.", show_alert=True)
        return
        
    settings["is_running"] = False
    text, markup = get_main_menu(user_id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


# --- ЯДРО АВТОМАТИЗАЦИИ РАССЫЛКИ ---

async def run_mailing_task(user_id: int, chat_id: int, message_id: int):
    settings = get_user_settings(user_id)
    accounts = await db.get_accounts(user_id)
    
    if not accounts:
        settings["is_running"] = False
        try:
            text, markup = get_main_menu(user_id)
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⚠️ Подключенные шлюзы (РМ) отсутствуют!\n\n" + text, parse_mode="Markdown", reply_markup=markup)
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

        workers_tasks = []
        for phone, session_str, _ in accounts:
            workers_tasks.append(send_messages_from_account(user_id, phone, session_str, groups))
            await asyncio.sleep(1.5)  # Плавный запуск сокетов

        if workers_tasks:
            await asyncio.gather(*workers_tasks)

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

    try: 
        await app.start()
        tasks = []
        for group_url in groups:
            tasks.append(worker.send_to_group(app, user_id, phone, group_url, settings["text"]))

        await asyncio.gather(*tasks)
    except OSError:
        logger.error(f"[{phone}] Сетевое исключение: Соединение разорвано.")
    except Exception as e:
        logger.error(f"[{phone}] Критическая ошибка воркера: {e}")
    finally:
        try: await app.stop()
        except Exception: pass


# --- ЗАПУСК ПОЛЛИНГА ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
