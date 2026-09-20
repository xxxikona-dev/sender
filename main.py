# main.py (без подписок)
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
from pyrogram.errors import FloodWait

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

# Глобальное хранилище индивидуальных настроек пользователей
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


def get_accounts_keyboard(user_id: int):
    accounts = asyncio.run(db.get_accounts(user_id))
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


# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    # Регистрируем пользователя
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
    await state.clear()
    text, markup = get_main_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_accounts_keyboard(callback.from_user.id)
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


# --- ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
