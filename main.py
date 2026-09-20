# main.py (полный, без подписок, с рабочим добавлением аккаунтов)
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

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PhoneNumberInvalid,
    PasswordHashInvalid,
    FloodWait
)

import config
import database as db
import worker

# Настройка вывода логов в консоль
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Переменная окружения 'BOT_TOKEN' не найдена на хостинге!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Словари для временного хранения
active_signups = {}
users_mailing_configs = {}

# Хранилище активных Pyrogram-клиентов во время авторизации
# { user_id: {"client": Client, "phone": str, "phone_code_hash": str} }
pending_auth_clients: dict[int, dict] = {}


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
    """Читает файл proxies.txt и возвращает случайный прокси"""
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
        logger.error(f"[Proxy] Ошибка при чтении или парсинге файла прокси: {e}")

    return None


# --- СОСТОЯНИЯ (FSM) ---
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


# --- ИНТЕРФЕЙСНЫЕ КНОПКИ ---
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
    buttons.append([InlineKeyboardButton(text="🛡 Проверить СПАМ-БЛОК", callback_data="check_all_spam")])
    if accounts:
        buttons.append([InlineKeyboardButton(text="💥 Завершить ВСЕ сессии", callback_data="kill_all_sessions")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_menu(count: int):
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


def get_back_inline(to_settings=False, to_accounts=False):
    if to_settings:
        target = "show_settings"
    elif to_accounts:
        target = "manage_accounts"
    else:
        target = "back_to_menu"
    buttons = [[InlineKeyboardButton(text="⬅️ Отменить операцию", callback_data=target)]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ АВТОРИЗАЦИИ ---

async def cleanup_pending_auth(user_id: int):
    """Корректно закрывает временный Pyrogram-клиент"""
    auth = pending_auth_clients.pop(user_id, None)
    if auth:
        try:
            await auth["client"].disconnect()
        except Exception:
            pass


async def finalize_auth(user_id: int, message: types.Message, state: FSMContext):
    """Сохраняет сессию в БД и завершает авторизацию"""
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
        logger.error(f"[Auth] Не удалось экспортировать сессию: {e}")
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


# --- ХЕНДЛЕРЫ ---

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
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery, state: FSMContext):
    await cleanup_pending_auth(callback.from_user.id)
    await state.clear()
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


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


# --- ДОБАВЛЕНИЕ АККАУНТА (АВТОРИЗАЦИЯ) ---

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
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=get_back_inline(to_accounts=True)
    )
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


# --- ЗАГЛУШКИ ДЛЯ ОСТАЛЬНЫХ КНОПОК ---

@dp.callback_query(F.data == "change_text")
async def change_text_stub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Модуль 'Скрипт задачи' пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "manage_groups")
async def manage_groups_stub(callback: types.CallbackQuery, state: FSMContext):
    groups = await db.get_groups(callback.from_user.id)
    text = (
        f"👥 **БАЗА АДРЕСАТОВ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Сохранено узлов: **{len(groups)}**\n\n"
        f"Модуль импорта/экспорта будет добавлен позже."
    )
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=get_groups_menu(len(groups))
    )
    await callback.answer()


@dp.callback_query(F.data == "show_settings")
async def show_settings_stub(callback: types.CallbackQuery, state: FSMContext):
    text, markup = get_settings_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "view_statistics")
async def view_statistics_stub(callback: types.CallbackQuery):
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
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data == "stop_mailing")
async def stop_mailing_stub(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    settings["is_running"] = False
    text, markup = get_main_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer("Сессия остановлена.")


@dp.callback_query(F.data == "check_all_spam")
async def check_all_spam_stub(callback: types.CallbackQuery):
    await callback.answer("Модуль проверки СПАМ-БЛОКА пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "kill_all_sessions")
async def kill_all_sessions_stub(callback: types.CallbackQuery):
    await callback.answer("Модуль завершения всех сессий пока в разработке.", show_alert=True)


@dp.callback_query(F.data.startswith("act_"))
async def act_account_stub(callback: types.CallbackQuery):
    phone = callback.data.replace("act_", "")
    await callback.answer(f"Управление {phone} пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "add_groups")
async def add_groups_stub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Модуль импорта узлов пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "download_chats")
async def download_chats_stub(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    if not groups:
        await callback.answer("База пуста.", show_alert=True)
        return
    data = "\n".join(groups).encode("utf-8")
    file = BufferedInputFile(data, filename="groups.txt")
    await callback.message.answer_document(file, caption=f"📥 База узлов ({len(groups)} шт.)")
    await callback.answer()


@dp.callback_query(F.data == "clear_groups")
async def clear_groups_stub(callback: types.CallbackQuery):
    await db.clear_groups(callback.from_user.id)
    await callback.answer("База узлов очищена.", show_alert=True)
    groups = await db.get_groups(callback.from_user.id)
    text = f"👥 **БАЗА АДРЕСАТОВ**\n\nСохранено узлов: **{len(groups)}**"
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=get_groups_menu(len(groups))
    )


@dp.callback_query(F.data == "set_min_delay")
async def set_min_delay_stub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Изменение min задержки пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "set_max_delay")
async def set_max_delay_stub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Изменение max задержки пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "set_wave_limit")
async def set_wave_limit_stub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Изменение лимита итераций пока в разработке.", show_alert=True)


@dp.callback_query(F.data == "toggle_typing")
async def toggle_typing_stub(callback: types.CallbackQuery):
    settings = get_user_settings(callback.from_user.id)
    settings["enable_typing"] = not settings["enable_typing"]
    text, markup = get_settings_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer("Переключено.")


# --- ФОНОВАЯ ЗАДАЧА РАССЫЛКИ (заглушка) ---
async def run_mailing_task(user_id: int, chat_id: int, message_id: int):
    try:
        settings = get_user_settings(user_id)
        while settings["is_running"]:
            settings["current_wave"] += 1
            # TODO: здесь вызвать worker.send_to_group(...) по аккаунтам и группам
            await asyncio.sleep(random.randint(settings["min_delay"], settings["max_delay"]))

            if settings["max_waves"] and settings["current_wave"] >= settings["max_waves"]:
                settings["is_running"] = False
                break
    except Exception as e:
        logger.error(f"[Mailing] Ошибка в фоновой задаче: {e}")
        settings["is_running"] = False


# --- ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
