# main.py
import asyncio
import logging
import random
import os
import io
import time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PhoneNumberInvalid,
    PasswordHashInvalid,
    FloodWait,
    AuthKeyUnregistered,
    UserDeactivated,
    SessionRevoked,
)

import config
import database as db
import worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)
logging.getLogger("pyrogram.session").setLevel(logging.CRITICAL)
logging.getLogger("pyrogram.connection").setLevel(logging.CRITICAL)
logging.getLogger("pyrogram.dispatcher").setLevel(logging.CRITICAL)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Переменная окружения 'BOT_TOKEN' не найдена!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

users_mailing_configs: dict[int, dict] = {}
pending_auth_clients: dict[int, dict] = {}
active_mailing_tasks: dict[int, asyncio.Task] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БЕЗОПАСНЫЙ EDIT ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def safe_edit(message, text, parse_mode="Markdown", reply_markup=None):
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- НАСТРОЙКИ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_settings(user_id: int) -> dict:
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
                "scheme": "http",
                "hostname": parts[0],
                "port": int(parts[1]),
                "username": parts[2],
                "password": parts[3]
            }
    except Exception as e:
        logger.error(f"[Proxy] Ошибка: {e}")
    return None


def render_text(template: str, chat_title: str = "", username: str = "") -> str:
    return (
        template
        .replace("{chat}", chat_title)
        .replace("{username}", username)
        .replace("{name}", username)
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- FSM ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()


class GroupStates(StatesGroup):
    waiting_for_links = State()


class TextStates(StatesGroup):
    waiting_for_text = State()


class SettingsStates(StatesGroup):
    waiting_for_min = State()
    waiting_for_max = State()
    waiting_for_waves = State()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- КЛАВИАТУРЫ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_main_menu(user_id: int):
    settings = get_user_settings(user_id)
    status = "🟢 АКТИВЕН" if settings["is_running"] else "🔴 ПРИОСТАНОВЛЕН"
    wave_limit = "Авто" if settings["max_waves"] == 0 else f"{settings['max_waves']}"

    text = (
        f"💼 **WORKSPACE MANAGER v4.0**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Статус процессов: {status}\n"
        f"Текущий цикл задач: {settings['current_wave']} из {wave_limit}\n"
        f"Задержка интервалов: {settings['min_delay']}-{settings['max_delay']} сек.\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Выберите необходимый модуль для настройки конфигурации:"
    )

    buttons = [
        [
            InlineKeyboardButton(text="📱 РМ (Сессии)", callback_data="manage_accounts"),
            InlineKeyboardButton(text="📝 Скрипт задачи", callback_data="change_text")
        ],
        [
            InlineKeyboardButton(text="👥 База адресатов", callback_data="manage_groups"),
            InlineKeyboardButton(text="⚙️ Конфигурация", callback_data="show_settings")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="view_statistics"),
            InlineKeyboardButton(text="⚡ Синхронизировать", callback_data="start_mailing")
        ],
        [
            InlineKeyboardButton(text="🛑 Прервать сессию", callback_data="stop_mailing")
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_accounts_keyboard(user_id: int):
    accounts = await db.get_accounts(user_id)
    buttons = []

    if not accounts:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\n❌ Нет подключенных аккаунтов шлюзов."
    else:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\nСписок ваших активных шлюзов и их статусы спамблока:\n"
        for phone, _, status, is_active in accounts:
            active_icon = "🟢" if is_active == 1 else "💤"
            text += f"\n{active_icon} `{phone}` — *{status}*"
            buttons.append([InlineKeyboardButton(text=f"⚙️ Управление {phone}", callback_data=f"act_{phone}")])

    buttons.append([InlineKeyboardButton(text="📱 Подключить новое РМ", callback_data="add_account")])
    buttons.append([InlineKeyboardButton(text="🛡 Проверить СПАМ-БЛОК (все)", callback_data="check_all_spam")])
    if accounts:
        buttons.append([InlineKeyboardButton(text="💥 Завершить ВСЕ сессии", callback_data="kill_all_sessions")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_account_actions_keyboard(phone: str, is_active: int):
    toggle_text = "💤 Деактивировать" if is_active == 1 else "🟢 Активировать"
    buttons = [
        [InlineKeyboardButton(text="🛡 Проверить СПАМ-БЛОК", callback_data=f"spam_{phone}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_{phone}")],
        [InlineKeyboardButton(text="💥 Завершить сессию", callback_data=f"kill_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_accounts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_menu():
    buttons = [
        [InlineKeyboardButton(text="📥 Импортировать список ID/Узлов", callback_data="add_groups")],
        [InlineKeyboardButton(text="📥 Скачать базу .txt", callback_data="download_chats")],
        [InlineKeyboardButton(text="🗑 Сбросить текущую базу", callback_data="clear_groups")],
        [InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


def get_text_menu(user_id: int):
    settings = get_user_settings(user_id)
    preview = settings["text"]
    if len(preview) > 300:
        preview = preview[:300] + "..."

    text = (
        f"📝 **СКРИПТ ЗАДАЧИ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Текущий текст рассылки:\n\n"
        f"```\n{preview}\n```\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Вы можете изменить текст, загрузить из файла или сбросить к стандартному."
    )

    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="edit_text")],
        [InlineKeyboardButton(text="📎 Загрузить из .txt", callback_data="upload_text")],
        [InlineKeyboardButton(text="🗑 Сбросить текст", callback_data="reset_text")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_inline(to_settings=False, to_accounts=False, to_text=False, to_groups=False):
    if to_settings:
        target = "show_settings"
    elif to_accounts:
        target = "manage_accounts"
    elif to_text:
        target = "change_text"
    elif to_groups:
        target = "manage_groups"
    else:
        target = "back_to_menu"
    buttons = [[InlineKeyboardButton(text="⬅️ Отменить операцию", callback_data=target)]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- АВТОРИЗАЦИЯ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def cleanup_pending_auth(user_id: int):
    auth = pending_auth_clients.pop(user_id, None)
    if auth:
        try:
            await auth["client"].disconnect()
        except Exception:
            pass


async def finalize_auth(user_id: int, message: types.Message, state: FSMContext):
    auth = pending_auth_clients.get(user_id)
    if not auth:
        await message.answer("❌ Сессия потеряна.")
        await state.clear()
        return

    client: Client = auth["client"]
    phone = auth["phone"]

    try:
        session_string = await client.export_session_string()
    except Exception as e:
        logger.error(f"[Auth] Ошибка экспорта сессии: {e}")
        await message.answer(f"❌ Ошибка экспорта сессии: `{e}`", parse_mode="Markdown")
        await cleanup_pending_auth(user_id)
        await state.clear()
        return

    await db.add_account(user_id, phone, session_string)
    await cleanup_pending_auth(user_id)
    await state.clear()

    text, markup = await get_accounts_keyboard(user_id)
    await message.answer(
        f"✅ Аккаунт `{phone}` успешно подключён!\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БАЗОВЫЕ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await cleanup_pending_auth(message.from_user.id)
    await state.clear()

    await db.register_or_update_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        message.from_user.last_name or ""
    )

    text, markup = get_main_menu(message.from_user.id)
    await message.answer(text, parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await cleanup_pending_auth(callback.from_user.id)
    await state.clear()
    text, markup = get_main_menu(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- АККАУНТЫ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery, state: FSMContext):
    await cleanup_pending_auth(callback.from_user.id)
    await state.clear()
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "add_account")
async def add_account_start(callback: types.CallbackQuery, state: FSMContext):
    await cleanup_pending_auth(callback.from_user.id)
    await state.clear()
    await state.set_state(AuthStates.waiting_for_phone)

    text = (
        "📱 **ПОДКЛЮЧЕНИЕ НОВОГО РМ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте номер телефона в международном формате:\n"
        "Пример: `+79991234567`\n\n"
        "⚠️ На этот номер придёт код подтверждения от Telegram."
    )
    await safe_edit(callback.message, text, reply_markup=get_back_inline(to_accounts=True))
    await callback.answer()


@dp.message(AuthStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "").replace("-", "")

    if not phone.startswith("+") or not phone[1:].isdigit():
        await message.answer(
            "❌ Неверный формат. Отправьте номер в формате `+79991234567`.",
            parse_mode="Markdown"
        )
        return

    await message.answer("⏳ Отправляем запрос к Telegram...")

    try:
        client = Client(
            name=f"auth_{message.from_user.id}_{int(time.time())}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            in_memory=True,
            phone_number=phone
        )
        await client.connect()
        sent = await client.send_code(phone)

        pending_auth_clients[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash
        }

        await state.set_state(AuthStates.waiting_for_code)
        await message.answer(
            f"📩 Код отправлен на `{phone}`.\n\n"
            f"Отправьте код **с пробелами или без**, например: `1 2 3 4 5` или `12345`.",
            parse_mode="Markdown",
            reply_markup=get_back_inline(to_accounts=True)
        )
    except PhoneNumberInvalid:
        await message.answer("❌ Telegram отклонил номер. Проверьте формат.")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()
    except FloodWait as e:
        await message.answer(f"⏳ FloodWait: подождите {e.value} сек.")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()
    except Exception as e:
        logger.error(f"[Auth] Ошибка отправки кода: {e}")
        await message.answer(f"❌ Ошибка: `{e}`", parse_mode="Markdown")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()


@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    auth = pending_auth_clients.get(message.from_user.id)
    if not auth:
        await message.answer("❌ Сессия авторизации потеряна. Начните заново.")
        await state.clear()
        return

    code = message.text.strip().replace(" ", "")

    try:
        await auth["client"].sign_in(
            phone_number=auth["phone"],
            phone_code_hash=auth["phone_code_hash"],
            phone_code=code
        )
    except SessionPasswordNeeded:
        await state.set_state(AuthStates.waiting_for_password)
        await message.answer(
            "🔐 На аккаунте включена двухфакторная аутентификация.\n"
            "Отправьте пароль (2FA):",
            reply_markup=get_back_inline(to_accounts=True)
        )
        return
    except PhoneCodeInvalid:
        await message.answer("❌ Неверный код. Попробуйте снова.")
        return
    except PhoneCodeExpired:
        await message.answer("❌ Код истёк. Начните заново.")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()
        return
    except Exception as e:
        logger.error(f"[Auth] Ошибка sign_in: {e}")
        await message.answer(f"❌ Ошибка: `{e}`", parse_mode="Markdown")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()
        return

    await finalize_auth(message.from_user.id, message, state)


@dp.message(AuthStates.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    auth = pending_auth_clients.get(message.from_user.id)
    if not auth:
        await message.answer("❌ Сессия авторизации потеряна. Начните заново.")
        await state.clear()
        return

    password = message.text.strip()

    try:
        await auth["client"].check_password(password)
    except PasswordHashInvalid:
        await message.answer("❌ Неверный пароль. Попробуйте снова.")
        return
    except Exception as e:
        logger.error(f"[Auth] Ошибка 2FA: {e}")
        await message.answer(f"❌ Ошибка: `{e}`", parse_mode="Markdown")
        await cleanup_pending_auth(message.from_user.id)
        await state.clear()
        return

    await finalize_auth(message.from_user.id, message, state)


@dp.callback_query(F.data.startswith("act_"))
async def act_account_handler(callback: types.CallbackQuery):
    phone = callback.data.replace("act_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)

    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    _, _, status, is_active = account
    text = (
        f"⚙️ **УПРАВЛЕНИЕ АККАУНТОМ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер: `{phone}`\n"
        f"🛡 СПАМ-БЛОК: *{status}*\n"
        f"🔄 Статус: {'🟢 Активен' if is_active == 1 else '💤 Отключен'}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(
        callback.message, text,
        reply_markup=get_account_actions_keyboard(phone, is_active)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_account_handler(callback: types.CallbackQuery):
    phone = callback.data.replace("toggle_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)

    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    new_status = await db.toggle_account_status(phone, account[3])
    await callback.answer(
        "🟢 Активирован" if new_status == 1 else "💤 Деактивирован",
        show_alert=True
    )

    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)
    _, _, status, is_active = account

    text = (
        f"⚙️ **УПРАВЛЕНИЕ АККАУНТОМ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер: `{phone}`\n"
        f"🛡 СПАМ-БЛОК: *{status}*\n"
        f"🔄 Статус: {'🟢 Активен' if is_active == 1 else '💤 Отключен'}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(
        callback.message, text,
        reply_markup=get_account_actions_keyboard(phone, is_active)
    )


@dp.callback_query(F.data.startswith("spam_"))
async def check_spam_single(callback: types.CallbackQuery):
    phone = callback.data.replace("spam_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)

    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    _, session, _, _ = account
    await callback.answer("🛡 Проверяем...", show_alert=False)

    try:
        await worker.check_account_spamblock(phone, session)
    except Exception as e:
        logger.error(f"[Spam] Ошибка: {e}")

    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)
    if not account:
        text, markup = await get_accounts_keyboard(callback.from_user.id)
        await safe_edit(callback.message, text, reply_markup=markup)
        return

    _, _, status, is_active = account
    text = (
        f"⚙️ **УПРАВЛЕНИЕ АККАУНТОМ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📱 Номер: `{phone}`\n"
        f"🛡 СПАМ-БЛОК: *{status}*\n"
        f"🔄 Статус: {'🟢 Активен' if is_active == 1 else '💤 Отключен'}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(
        callback.message, text,
        reply_markup=get_account_actions_keyboard(phone, is_active)
    )


@dp.callback_query(F.data == "check_all_spam")
async def check_all_spam_handler(callback: types.CallbackQuery):
    accounts = await db.get_accounts(callback.from_user.id)
    if not accounts:
        await callback.answer("Нет аккаунтов.", show_alert=True)
        return

    await callback.answer("🛡 Проверяем все аккаунты...", show_alert=True)

    for phone, session, _, _ in accounts:
        try:
            await worker.check_account_spamblock(phone, session)
        except Exception as e:
            logger.error(f"[Spam] {phone}: {e}")

    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)


@dp.callback_query(F.data.startswith("kill_"))
async def kill_single_account(callback: types.CallbackQuery):
    phone = callback.data.replace("kill_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    account = next((a for a in accounts if a[0] == phone), None)

    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    _, session, _, _ = account
    await callback.answer("💥 Завершаем сессию...", show_alert=False)

    try:
        await worker.terminate_session(phone, session)
    except Exception as e:
        logger.error(f"[Kill] Ошибка: {e}")
        await db.remove_account(phone)

    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)


@dp.callback_query(F.data == "kill_all_sessions")
async def kill_all_sessions_handler(callback: types.CallbackQuery):
    accounts = await db.get_accounts(callback.from_user.id)
    if not accounts:
        await callback.answer("Нет аккаунтов.", show_alert=True)
        return

    await callback.answer("💥 Завершаем все сессии...", show_alert=True)

    for phone, session, _, _ in accounts:
        try:
            await worker.terminate_session(phone, session)
        except Exception as e:
            logger.error(f"[Kill] {phone}: {e}")
            await db.remove_account(phone)

    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БАЗА АДРЕСАТОВ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "manage_groups")
async def manage_groups_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    groups = await db.get_groups(callback.from_user.id)
    text = (
        f"👥 **БАЗА АДРЕСАТОВ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Сохранено узлов: **{len(groups)}**\n\n"
        f"Импортируйте список `@username`, `https://t.me/...` или invite-ссылку одним сообщением (по одному на строку)."
    )
    await safe_edit(callback.message, text, reply_markup=get_groups_menu())
    await callback.answer()


@dp.callback_query(F.data == "add_groups")
async def add_groups_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GroupStates.waiting_for_links)
    await safe_edit(
        callback.message,
        "📥 **ИМПОРТ БАЗЫ УЗЛОВ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте список одним сообщением.\n"
        "Каждая строка — один узел: `@username`, `https://t.me/username` или `https://t.me/+inviteHash`.\n\n"
        "Пример:\n"
        "```\n@chat1\nhttps://t.me/chat2\nhttps://t.me/+AbCdEfGhIjKlMnOp\n```",
        reply_markup=get_back_inline(to_groups=True)
    )
    await callback.answer()


@dp.message(GroupStates.waiting_for_links, F.text)
async def process_groups_input(message: types.Message, state: FSMContext):
    lines = [line.strip() for line in message.text.splitlines() if line.strip()]
    if not lines:
        await message.answer("❌ Пустой список. Отправьте хотя бы один узел.")
        return

    added = 0
    for raw in lines:
        normalized = raw.strip()
        # Убираем https://t.me/ и http://t.me/, но сохраняем ведущий +
        if normalized.startswith("https://t.me/"):
            normalized = normalized[len("https://t.me/"):]
        elif normalized.startswith("http://t.me/"):
            normalized = normalized[len("http://t.me/"):]
        elif normalized.startswith("t.me/"):
            normalized = normalized[len("t.me/"):]

        # Убираем @ только у username, но не у invite
        if normalized.startswith("@"):
            normalized = normalized[1:]

        normalized = normalized.strip()
        if not normalized:
            continue

        await db.add_group(message.from_user.id, normalized)
        added += 1

    await state.clear()
    groups = await db.get_groups(message.from_user.id)

    await message.answer(
        f"✅ Импортировано узлов: **{added}**\n"
        f"📦 Всего в базе: **{len(groups)}**",
        parse_mode="Markdown",
        reply_markup=get_groups_menu()
    )


@dp.callback_query(F.data == "download_chats")
async def download_chats_handler(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    if not groups:
        await callback.answer("База пуста.", show_alert=True)
        return

    data = "\n".join(groups).encode("utf-8")
    file = BufferedInputFile(data, filename="groups.txt")
    await callback.message.answer_document(file, caption=f"📥 База узлов ({len(groups)} шт.)")
    await callback.answer()


@dp.callback_query(F.data == "clear_groups")
async def clear_groups_handler(callback: types.CallbackQuery):
    await db.clear_groups(callback.from_user.id)
    await callback.answer("🗑 База узлов очищена.", show_alert=True)

    groups = await db.get_groups(callback.from_user.id)
    text = (
        f"👥 **БАЗА АДРЕСАТОВ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Сохранено узлов: **{len(groups)}**"
    )
    await safe_edit(callback.message, text, reply_markup=get_groups_menu())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- СКРИПТ ЗАДАЧИ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "change_text")
async def change_text_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_text_menu(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "edit_text")
async def edit_text_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TextStates.waiting_for_text)
    await safe_edit(
        callback.message,
        "✏️ **ВВЕДИТЕ НОВЫЙ ТЕКСТ РАССЫЛКИ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте текст одним сообщением.\n"
        "Можно использовать `{name}`, `{username}`, `{chat}` — они будут заменены при отправке.",
        reply_markup=get_back_inline(to_text=True)
    )
    await callback.answer()


@dp.callback_query(F.data == "upload_text")
async def upload_text_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TextStates.waiting_for_text)
    await safe_edit(
        callback.message,
        "📎 **ЗАГРУЗКА ТЕКСТА ИЗ ФАЙЛА**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте `.txt` файл с текстом рассылки.\n"
        "Кодировка: UTF-8. Максимум 1 МБ.",
        reply_markup=get_back_inline(to_text=True)
    )
    await callback.answer()


@dp.callback_query(F.data == "reset_text")
async def reset_text_handler(callback: types.CallbackQuery, state: FSMContext):
    settings = get_user_settings(callback.from_user.id)
    settings["text"] = "Привет! Это стандартный текст рассылки. Измените его в меню."
    await callback.answer("✅ Текст сброшен к стандартному.", show_alert=True)
    text, markup = get_text_menu(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)


@dp.message(TextStates.waiting_for_text, F.text)
async def process_text_input(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("❌ Текст пустой. Отправьте что-нибудь осмысленное.")
        return

    settings = get_user_settings(message.from_user.id)
    settings["text"] = new_text
    await state.clear()

    text, markup = get_text_menu(message.from_user.id)
    await message.answer(
        "✅ Текст рассылки обновлён!\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.message(TextStates.waiting_for_text, F.document)
async def process_text_document(message: types.Message, state: FSMContext):
    document = message.document

    if not document.file_name.lower().endswith(".txt"):
        await message.answer("❌ Поддерживаются только `.txt` файлы. Попробуйте снова.")
        return

    if document.file_size > 1024 * 1024:
        await message.answer("❌ Файл слишком большой (макс. 1 МБ).")
        return

    try:
        file = await bot.get_file(document.file_id)
        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        content = buffer.getvalue().decode("utf-8", errors="replace").strip()
    except Exception as e:
        logger.error(f"[Text] Ошибка чтения файла: {e}")
        await message.answer(f"❌ Не удалось прочитать файл: `{e}`", parse_mode="Markdown")
        return

    if not content:
        await message.answer("❌ Файл пустой.")
        return

    settings = get_user_settings(message.from_user.id)
    settings["text"] = content
    await state.clear()

    text, markup = get_text_menu(message.from_user.id)
    await message.answer(
        f"✅ Текст загружен из файла ({len(content)} символов).\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- КОНФИГУРАЦИЯ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "show_settings")
async def show_settings_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_settings_menu(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "set_min_delay")
async def set_min_delay_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_for_min)
    await safe_edit(
        callback.message,
        "⏱ **ВВЕДИТЕ НИЖНИЙ ПОРОГ ЗАДЕРЖКИ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте число в секундах (например, `30`).",
        reply_markup=get_back_inline(to_settings=True)
    )
    await callback.answer()


@dp.message(SettingsStates.waiting_for_min, F.text)
async def process_min_delay(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число секунд.")
        return

    value = int(message.text.strip())
    if value < 1:
        await message.answer("❌ Минимум 1 секунда.")
        return

    settings = get_user_settings(message.from_user.id)
    settings["min_delay"] = value
    if settings["max_delay"] < value:
        settings["max_delay"] = value

    await state.clear()
    text, markup = get_settings_menu(message.from_user.id)
    await message.answer(
        f"✅ Нижний порог: **{value}** сек.\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.callback_query(F.data == "set_max_delay")
async def set_max_delay_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_for_max)
    await safe_edit(
        callback.message,
        "⏱ **ВВЕДИТЕ ВЕРХНИЙ ПОРОГ ЗАДЕРЖКИ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте число в секундах (например, `120`).",
        reply_markup=get_back_inline(to_settings=True)
    )
    await callback.answer()


@dp.message(SettingsStates.waiting_for_max, F.text)
async def process_max_delay(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число секунд.")
        return

    value = int(message.text.strip())
    settings = get_user_settings(message.from_user.id)

    if value < settings["min_delay"]:
        await message.answer(f"❌ Верхний порог не может быть меньше нижнего ({settings['min_delay']}).")
        return

    settings["max_delay"] = value
    await state.clear()
    text, markup = get_settings_menu(message.from_user.id)
    await message.answer(
        f"✅ Верхний порог: **{value}** сек.\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.callback_query(F.data == "set_wave_limit")
async def set_wave_limit_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_for_waves)
    await safe_edit(
        callback.message,
        "🔄 **ЛИМИТ ИТЕРАЦИЙ**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте число циклов или `0` для снятия ограничения.",
        reply_markup=get_back_inline(to_settings=True)
    )
    await callback.answer()


@dp.message(SettingsStates.waiting_for_waves, F.text)
async def process_wave_limit(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число (0 = без ограничений).")
        return

    value = int(message.text.strip())
    settings = get_user_settings(message.from_user.id)
    settings["max_waves"] = value

    await state.clear()
    text, markup = get_settings_menu(message.from_user.id)
    await message.answer(
        f"✅ Лимит итераций: **{'без ограничений' if value == 0 else value}**.\n\n" + text,
        parse_mode="Markdown",
        reply_markup=markup
    )


@dp.callback_query(F.data == "toggle_typing")
async def toggle_typing_handler(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    settings["enable_typing"] = not settings["enable_typing"]

    text, markup = get_settings_menu(callback.from_user.id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer("✅ Переключено.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- СТАТИСТИКА ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "view_statistics")
async def view_statistics_handler(callback: types.CallbackQuery):
    stats = await db.get_stats(callback.from_user.id)
    text = (
        f"📊 **СТАТИСТИКА ОТПРАВОК**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱ За час: **{stats['hour']}**\n"
        f"📅 За сутки: **{stats['day']}**\n"
        f"🗓 За неделю: **{stats['week']}**\n"
        f"📆 За месяц: **{stats['month']}**\n"
        f"♾ Всего: **{stats['all']}**\n"
    )
    buttons = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]]
    await safe_edit(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- СТАРТ/СТОП РАССЫЛКИ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dp.callback_query(F.data == "start_mailing")
async def start_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)

    if settings["is_running"]:
        await callback.answer("Сессия уже активна!", show_alert=True)
        return

    accounts = await db.get_accounts(user_id)
    active_accounts = [a for a in accounts if a[3] == 1]
    if not active_accounts:
        await callback.answer("❌ Нет активных аккаунтов.", show_alert=True)
        return

    groups = await db.get_groups(user_id)
    if not groups:
        await callback.answer("❌ База узлов пуста.", show_alert=True)
        return

    settings["is_running"] = True
    settings["current_wave"] = 0

    text, markup = get_main_menu(user_id)
    await safe_edit(callback.message, text, reply_markup=markup)

    task = asyncio.create_task(run_mailing_task(user_id))
    active_mailing_tasks[user_id] = task

    await callback.answer("⚡ Синхронизация запущена.")


@dp.callback_query(F.data == "stop_mailing")
async def stop_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    settings["is_running"] = False

    task = active_mailing_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()

    text, markup = get_main_menu(user_id)
    await safe_edit(callback.message, text, reply_markup=markup)
    await callback.answer("🛑 Сессия остановлена.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- ФОНОВАЯ РАССЫЛКА ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run_mailing_task(user_id: int):
    settings = get_user_settings(user_id)

    try:
        while settings["is_running"]:
            settings["current_wave"] += 1
            logger.info(f"[Mailing] Юзер {user_id}, волна {settings['current_wave']}")

            accounts = await db.get_accounts(user_id)
            active_accounts = [a for a in accounts if a[3] == 1]
            groups = await db.get_groups(user_id)

            logger.info(
                f"[Mailing] Активных аккаунтов: {len(active_accounts)}, "
                f"групп: {len(groups)}"
            )

            if not active_accounts:
                logger.warning("[Mailing] Нет активных аккаунтов — стоп.")
                settings["is_running"] = False
                break

            if not groups:
                logger.warning("[Mailing] Нет групп — стоп.")
                settings["is_running"] = False
                break

            for phone, session, _, _ in active_accounts:
                if not settings["is_running"]:
                    break

                proxy = get_random_proxy_config()
                app = Client(
                    name=f"mail_{phone}",
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=session,
                    in_memory=True,
                    proxy=proxy
                )

                try:
                    await app.start()
                    logger.info(f"[Mailing] Аккаунт {phone} запущен.")
                except (AuthKeyUnregistered, UserDeactivated, SessionRevoked) as e:
                    logger.warning(f"[Mailing] Сессия {phone} недействительна ({type(e).__name__}) — удаляем.")
                    await db.remove_account(phone)
                    continue
                except Exception as e:
                    logger.error(f"[Mailing] Не удалось запустить {phone}: {e}")
                    continue

                try:
                    for group_url in list(groups):
                        if not settings["is_running"]:
                            break

                        rendered = render_text(
                            settings["text"],
                            chat_title=group_url,
                            username=""
                        )

                        try:
                            ok = await worker.send_to_group(
                                app, user_id, phone, group_url, rendered
                            )
                        except Exception as e:
                            logger.error(f"[Mailing] send_to_group упал на {group_url}: {e}")
                            ok = False

                        delay = random.randint(settings["min_delay"], settings["max_delay"])
                        logger.info(f"[Mailing] Пауза {delay} сек. (успех={ok})")
                        await asyncio.sleep(delay)

                        groups = await db.get_groups(user_id)

                finally:
                    try:
                        await app.stop()
                    except Exception:
                        pass

            if settings["max_waves"] and settings["current_wave"] >= settings["max_waves"]:
                logger.info(f"[Mailing] Юзер {user_id}: лимит волн достигнут.")
                settings["is_running"] = False
                break

    except asyncio.CancelledError:
        logger.info(f"[Mailing] Задача юзера {user_id} отменена.")
    except Exception as e:
        logger.exception(f"[Mailing] Критическая ошибка: {e}")
    finally:
        settings["is_running"] = False
        active_mailing_tasks.pop(user_id, None)
        logger.info(f"[Mailing] Юзер {user_id}: задача завершена.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- СТАРТ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())