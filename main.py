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


def get_back_inline(to_settings=False, to_accounts=False, to_subscription=False):
    if to_settings:
        target = "show_settings"
    elif to_accounts:
        target = "manage_accounts"
    elif to_subscription:
        target = "subscription_menu"
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


# --- ПОДПИСКИ ---

@dp.callback_query(F.data == "subscription_menu")
async def subscription_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, markup = get_subscription_menu()
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_"))
async def select_plan_handler(callback: types.CallbackQuery, state: FSMContext):
    plan_type = callback.data.replace("sub_", "")
    
    if plan_type == "xrocket":
        # Показываем меню выбора плана для XROCKET
        text = (
            "🚀 **ОПЛАТА ЧЕРЕЗ XROCKET**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Выберите тарифный план для оплаты в XROCKET:\n\n"
        )
        
        for key, plan in config.SUBSCRIPTION_PLANS.items():
            text += f"\n{plan['color']} **{plan['name']}** - {plan['price_xrocket']} XROCKET"
        
        buttons = [
            [InlineKeyboardButton(text="🟢 Дневная - 5 XROCKET", callback_data="pay_xrocket_day")],
            [InlineKeyboardButton(text="🔵 Недельная - 15 XROCKET", callback_data="pay_xrocket_week")],
            [InlineKeyboardButton(text="🟣 Месячная - 45 XROCKET", callback_data="pay_xrocket_month")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="subscription_menu")]
        ]
        
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()
        return
    
    # Обычные планы в USD
    text, markup = get_payment_methods_menu(plan_type)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto_handler(callback: types.CallbackQuery, state: FSMContext):
    plan_type = callback.data.replace("pay_crypto_", "")
    
    # Создаем платеж
    try:
        payment_data = await payment_manager.create_payment(callback.from_user.id, plan_type, "crypto")
        await db.add_payment_record(
            callback.from_user.id,
            payment_data["payment_id"],
            plan_type,
            payment_data["amount"],
            payment_data["currency"]
        )
        
        text, markup = get_payment_confirmation_menu(payment_data)
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        logger.error(f"Payment error: {e}")
        await callback.answer(f"Ошибка создания платежа: {e}", show_alert=True)
    
    await callback.answer()


@dp.callback_query(F.data.startswith("pay_xrocket_"))
async def pay_xrocket_handler(callback: types.CallbackQuery, state: FSMContext):
    plan_type = callback.data.replace("pay_xrocket_", "")
    
    try:
        payment_data = await payment_manager.create_payment(callback.from_user.id, plan_type, "xrocket")
        await db.add_payment_record(
            callback.from_user.id,
            payment_data["payment_id"],
            plan_type,
            payment_data["amount"],
            payment_data["currency"]
        )
        
        text, markup = get_payment_confirmation_menu(payment_data)
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        logger.error(f"xRocket payment error: {e}")
        await callback.answer(f"Ошибка создания платежа: {e}", show_alert=True)
    
    await callback.answer()


@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment_handler(callback: types.CallbackQuery, state: FSMContext):
    payment_id = callback.data.replace("check_payment_", "")
    
    # Проверяем статус платежа
    payment_info = await db.get_payment_status(payment_id)
    if not payment_info:
        await callback.answer("Платеж не найден", show_alert=True)
        return
    
    # В реальном проекте здесь будет проверка через платежный шлюз
    # Сейчас симулируем успешную оплату
    
    # Активируем подписку
    await SubscriptionManager.activate_subscription(
        payment_info["user_id"],
        payment_info["plan_type"]
    )
    
    await db.confirm_payment(payment_id)
    
    await callback.answer("✅ Платеж подтвержден! Подписка активирована.", show_alert=True)
    
    text, markup = get_main_menu(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@dp.callback_query(F.data == "payment_history")
async def payment_history_handler(callback: types.CallbackQuery):
    payments = await db.get_user_payments(callback.from_user.id, 10)
    
    if not payments:
        text = "📋 **ИСТОРИЯ ПЛАТЕЖЕЙ**\n\n❌ Платежей не найдено."
    else:
        text = "📋 **ИСТОРИЯ ПЛАТЕЖЕЙ**\n\n"
        for payment in payments:
            payment_id, plan_type, amount, currency, status, created_at, confirmed_at = payment
            status_emoji = "✅" if status == "completed" else "⏳"
            plan_names = {"day": "Дневная", "week": "Недельная", "month": "Месячная"}
            plan_name = plan_names.get(plan_type, plan_type)
            text += f"{status_emoji} {plan_name} - {amount} {currency}\n"
    
    buttons = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="subscription_menu")]]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- ОСТАЛЬНЫЕ ХЕНДЛЕРЫ (аккаунты, группы, настройки, статистика) ---

@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery, state: FSMContext):
    # Проверяем подписку
    sub = await SubscriptionManager.check_subscription(callback.from_user.id)
    if not sub["is_active"]:
        await callback.answer("❌ Требуется активная подписка!", show_alert=True)
        text, markup = get_subscription_menu()
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
        return
    
    await state.clear()
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer()


# Остальные хендлеры (аккаунты, группы, настройки, статистика, запуск)
# ... (код из предыдущей версии с небольшими изменениями)

# Добавляем проверку подписки в критические функции

@dp.callback_query(F.data == "start_mailing")
async def start_mailing_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Проверяем подписку
    sub = await SubscriptionManager.check_subscription(user_id)
    if not sub["is_active"]:
        await callback.answer("❌ Требуется активная подписка!", show_alert=True)
        text, markup = get_subscription_menu()
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
        return
    
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
