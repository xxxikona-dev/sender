# main.py (обновленный с подписками)
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
from payment import PaymentManager
from subscription import SubscriptionManager, SubscriptionMiddleware

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
payment_manager = PaymentManager()

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


class PaymentStates(StatesGroup):
    waiting_for_plan = State()
    waiting_for_payment_method = State()
    waiting_for_payment_confirmation = State()


# --- ИНТЕРФЕЙСНЫЕ КНОПКИ ---
def get_main_menu(user_id: int):
    settings = get_user_settings(user_id)
    status = "🟢 АКТИВЕН" if settings["is_running"] else "🔴 ПРИОСТАНОВЛЕН"
    wave_limit = "Авто" if settings["max_waves"] == 0 else f"{settings['max_waves']}"
    
    # Получаем информацию о подписке
    sub_info = None
    try:
        import asyncio
        sub_info = asyncio.run(SubscriptionManager.check_subscription(user_id))
    except:
        pass
    
    sub_status = ""
    if sub_info and sub_info["is_active"]:
        days_left = SubscriptionManager.get_remaining_days(sub_info["expires"])
        plan_names = {"day": "Дневная", "week": "Недельная", "month": "Месячная"}
        plan_name = plan_names.get(sub_info["type"], sub_info["type"])
        sub_status = f"✅ {plan_name} (осталось {days_left} дн.)"
    else:
        sub_status = "❌ Нет активной подписки"
    
    text = (
        f"💼 **WORKSPACE MANAGER v4.0**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 Подписка: {sub_status}\n"
        f"Статус процессов: {status}\n"
        f"Текущий цикл задач: {settings['current_wave']} из {wave_limit}\n"
        f"Задержка интервалов: {settings['min_delay']}-{settings['max_delay']} сек.\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Выберите необходимый модуль для настройки конфигурации:"
    )
    
    buttons = [
        [
            InlineKeyboardButton(text="🎫 Подписка", callback_data="subscription_menu"),
            InlineKeyboardButton(text="📱 РМ (Сессии)", callback_data="manage_accounts")
        ],
        [
            InlineKeyboardButton(text="📝 Скрипт задачи", callback_data="change_text"),
            InlineKeyboardButton(text="👥 База адресатов", callback_data="manage_groups")
        ],
        [
            InlineKeyboardButton(text="⚙️ Конфигурация", callback_data="show_settings"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="view_statistics")
        ],
        [
            InlineKeyboardButton(text="⚡ Синхронизировать", callback_data="start_mailing"),
            InlineKeyboardButton(text="🛑 Прервать сессию", callback_data="stop_mailing")
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_subscription_menu():
    """Меню подписок"""
    plans = SubscriptionManager.get_plans()
    
    text = (
        "🎫 **ПОДПИСКА НА СЕРВИС**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Выберите тарифный план:\n\n"
    )
    
    for key, plan in plans.items():
        text += f"\n{SubscriptionManager.format_plan_info(key)}\n"
    
    text += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        "💡 Каждый следующий план выгоднее предыдущего!\n"
        "💰 Оплата принимается в $ через крипто-бот или XROCKET"
    )
    
    buttons = [
        [InlineKeyboardButton(text="🟢 Дневная - $2.99", callback_data="sub_day")],
        [InlineKeyboardButton(text="🔵 Недельная - $9.99", callback_data="sub_week")],
        [InlineKeyboardButton(text="🟣 Месячная - $29.99", callback_data="sub_month")],
        [InlineKeyboardButton(text="💰 Оплатить XROCKET", callback_data="sub_xrocket")],
        [InlineKeyboardButton(text="📋 История платежей", callback_data="payment_history")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ]
    
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_methods_menu(plan_type: str):
    """Меню выбора способа оплаты"""
    plan = config.SUBSCRIPTION_PLANS.get(plan_type, {})
    
    text = (
        f"💳 **СПОСОБ ОПЛАТЫ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"План: {plan.get('name', 'Неизвестный')}\n"
        f"Сумма: ${plan.get('price_usd', 0):.2f}\n\n"
        f"Выберите способ оплаты:"
    )
    
    buttons = [
        [InlineKeyboardButton(text="💰 Крипто-бот (USDT)", callback_data=f"pay_crypto_{plan_type}")],
        [InlineKeyboardButton(text="🚀 XROCKET", callback_data=f"pay_xrocket_{plan_type}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="subscription_menu")]
    ]
    
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_confirmation_menu(payment_data: dict):
    """Меню подтверждения оплаты"""
    text = (
        f"💳 **ИНСТРУКЦИЯ ПО ОПЛАТЕ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Сумма: {payment_data['amount']} {payment_data['currency']}\n"
        f"📝 ID платежа: `{payment_data['payment_id']}`\n"
        f"\n📤 Отправьте указанную сумму на адрес:\n"
        f"`{payment_data['address']}`\n"
        f"\n📌 В комментарии (memo) укажите:\n"
        f"`{payment_data.get('memo', payment_data['payment_id'])}`\n"
        f"\n⏰ Платеж ожидается в течение 1 часа\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"После отправки нажмите кнопку подтверждения"
    )
    
    buttons = [
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_payment_{payment_data['payment_id']}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="subscription_menu")]
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
    buttons.append([InlineKeyboardButton(text="🛡 Провери
