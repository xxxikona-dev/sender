import asyncio
import logging
import random
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

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Словарь для временного удержания объектов авторизации Pyrogram {chat_id: client_instance}
active_signups = {}

# Глобальные динамические настройки рассылки
mailing_settings = {
    "text": "Привет! Это стандартный текст рассылки. Измените его в меню.",
    "min_delay": 30,  # Минимальная задержка между группами в сек.
    "max_delay": 120,  # Максимальная задержка между группами в сек.
    "is_running": False,
}


# --- СОСТОЯНИЯ (FSM) ---
class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()


class GroupStates(StatesGroup):
    waiting_for_links = State()


class TextStates(StatesGroup):
    waiting_for_text = State()


# --- ИНТЕРФЕЙСНЫЕ КНОПКИ (КЛАВИАТУРЫ) ---
def get_main_menu():
    buttons = [
        [
            InlineKeyboardButton(
                text="📱 Добавить аккаунт", callback_data="add_account"
            )
        ],
        [
            InlineKeyboardButton(
                text="📝 Изменить текст рассылки", callback_data="change_text"
            )
        ],
        [
            InlineKeyboardButton(
                text="👥 Управление группами", callback_data="manage_groups"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Посмотреть настройки", callback_data="show_settings"
            )
        ],
        [
            InlineKeyboardButton(
                text="🚀 Запустить рассылку", callback_data="start_mailing"
            ),
            InlineKeyboardButton(
                text="🛑 Остановить", callback_data="stop_mailing"
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_groups_menu():
    buttons = [
        [
            InlineKeyboardButton(
                text="➕ Добавить группы (списком)", callback_data="add_groups"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Очистить весь список", callback_data="clear_groups"
            )
        ],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ ---


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🤖 Главное меню панели управления рассылками:",
        reply_markup=get_main_menu(),
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤖 Главное меню панели управления рассылками:",
        reply_markup=get_main_menu(),
    )
    await callback.answer()


# --- ИЗМЕНЕНИЕ ТЕКСТА РАССЫЛКИ ---


@dp.callback_query(F.data == "change_text")
async def change_text_cmd(callback: types.CallbackQuery, state: FSMContext):
    current_text = mailing_settings["text"]
    await callback.message.answer(
        f"📋 **Текущий текст рассылки:**\n`{current_text}`\n\n"
        f"Отправьте мне новый текст, который будут рассылать юзерботы:",
        parse_mode="Markdown",
    )
    await state.set_state(TextStates.waiting_for_text)
    await callback.answer()


@dp.message(TextStates.waiting_for_text)
async def process_new_text(message: types.Message, state: FSMContext):
    new_text = message.text
    mailing_settings["text"] = new_text
    await message.answer(
        f"✅ Текст рассылки успешно обновлен!\n\n**Новый текст:**\n{new_text}",
        reply_markup=get_main_menu(),
    )
    await state.clear()


# --- УПРАВЛЕНИЕ ГРУППАМИ ---


@dp.callback_query(F.data == "manage_groups")
async def manage_groups_cmd(callback: types.CallbackQuery):
    groups = await db.get_groups()
    count = len(groups)

    if count == 0:
        list_str = "Список групп в базе пуст."
    else:
        preview = groups[:15]
        list_str = (
            f"📊 Всего групп в базе: **{count}**\n\n**Превью списка чатов:**\n"
            + "\n".join(preview)
        )
        if count > 15:
            list_str += "\n... и остальные чаты."

    await callback.message.edit_text(
        list_str, parse_mode="Markdown", reply_markup=get_groups_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "add_groups")
async def start_add_groups(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📥 Отправьте список ссылок на группы. Каждая ссылка или юзернейм "
        "должны быть **с новой строки**:\n\n"
        "Пример формата:\n"
        "https://t.me/chat_one\n"
        "@chat_two"
    )
    await state.set_state(GroupStates.waiting_for_links)
    await callback.answer()


@dp.message(GroupStates.waiting_for_links)
async def process_groups_list(message: types.Message, state: FSMContext):
    links = message.text.split("\n")
    added_count = 0

    for link in links:
        cleaned_link = link.strip()
        if cleaned_link:
            await db.add_group(cleaned_link)
            added_count += 1

    await message.answer(
        f"✅ База данных обновлена! Успешно внесено групп: {added_count}",
        reply_markup=get_main_menu(),
    )
    await state.clear()


@dp.callback_query(F.data == "clear_groups")
async def clear_groups_cmd(callback: types.CallbackQuery):
    await db.clear_groups()
    await callback.message.edit_text(
        "🗑 Все группы были успешно удалены из базы данных.",
        reply_markup=get_groups_menu(),
    )
    await callback.answer()


# --- ПОДКЛЮЧЕНИЕ ЮЗЕРБОТОВ (АВТОРИЗАЦИЯ) ---


@dp.callback_query(F.data == "add_account")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Введите номер телефона аккаунта для авторизации (например, +79991234567):"
    )
    await state.set_state(AuthStates.waiting_for_phone)
    await callback.answer()


@dp.message(AuthStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    client = Client(
        name=phone,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
    )
    await client.connect()

    try:
        code_hash = await client.send_code(phone)
        await state.update_data(
            phone=phone, phone_code_hash=code_hash.phone_code_hash
        )
        active_signups[message.chat.id] = client

        await message.answer(
            f"Код отправлен на {phone}. Введите код подтверждения из чата Telegram:"
        )
        await state.set_state(AuthStates.waiting_for_code)
    except Exception as e:
        logger.error(f"Ошибка при инициализации кода: {e}")
        await message.answer(f"❌ Ошибка: {e}. Начните процедуру заново.")
        await client.disconnect()
        await state.clear()


@dp.message(AuthStates.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]

    client = active_signups.get(message.chat.id)
    if not client:
        await message.answer("Критическая ошибка сессии. Авторизуйтесь заново.")
        await state.clear()
        return

    try:
        await client.sign_in(
            phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code
        )
        string_session = await client.export_session_string()
        await db.add_account(phone, string_session)

        await message.answer(
            f"✅ Юзербот {phone} успешно привязан и сохранен в БД!",
            reply_markup=get_main_menu(),
        )
        await client.disconnect()
        active_signups.pop(message.chat.id, None)
    except Exception as e:
        await message.answer(f"❌ Не удалось авторизоваться: {e}")
        await client.disconnect()
    finally:
        await state.clear()


# --- МОНИТОРИНГ НАСТРОЕК ---


@dp.callback_query(F.data == "show_settings")
async def show_settings_cmd(callback: types.CallbackQuery):
    groups = await db.get_groups()
    accounts = await db.get_accounts()
    text = (
        f"⚙️ **Текущее состояние системы:**\n\n"
        f"👤 Подключено аккаунтов: **{len(accounts)}**\n"
        f"👥 Групп в списке: **{len(groups)}**\n"
        f"⏱ Рандомные задержки: **{mailing_settings['min_delay']}-{mailing_settings['max_delay']} сек.**\n\n"
        f"📝 **Текст рассылки:**\n`{mailing_settings['text']}`"
    )
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


# --- УПРАВЛЕНИЕ РАССЫЛКОЙ ---


@dp.callback_query(F.data == "start_mailing")
async def start_mailing_handler(callback: types.CallbackQuery):
    if mailing_settings["is_running"]:
        await callback.answer("Рассылка уже запущена!", show_alert=True)
        return

    mailing_settings["is_running"] = True
    await callback.message.answer("🚀 Запуск фоновой отправки для всех аккаунтов...")
    asyncio.create_task(run_mailing_task())
    await callback.answer()


@dp.callback_query(F.data == "stop_mailing")
async def stop_mailing_handler(callback: types.CallbackQuery):
    mailing_settings["is_running"] = False
    await callback.message.answer("🛑 Сигнал остановки передан воркерам.")
    await callback.answer()


# --- ЯДРО ЮЗЕРБОТА И ЦИКЛ ОТПРАВКИ ---


async def run_mailing_task():
    accounts = await db.get_accounts()
    groups = await db.get_groups()

    if not accounts or not groups:
        logger.warning("Рассылка невозможна: база аккаунтов или групп пуста.")
        mailing_settings["is_running"] = False
        return

    workers = []
    for phone, session_str in accounts:
        workers.append(send_messages_from_account(phone, session_str, groups))

    await asyncio.gather(*workers)
    mailing_settings["is_running"] = False


async def send_messages_from_account(phone, session_str, groups):
    app = Client(
        name=phone,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_str,
    )

    try:
        await app.start()
    except (UserDeactivated, AuthKeyUnregistered):
        logger.error(f"[{phone}] Сессия недействительна (аккаунт забанен).")
        return
    except Exception as e:
        logger.error(f"[{phone}] Ошибка старта клиента: {e}")
        return

    logger.info(f"[{phone}] Юзербот вышел на линию.")

    for group_url in groups:
        if not mailing_settings["is_running"]:
            break

        try:
            chat_peer = group_url.replace("https://t.me/", "").replace("@", "")

            try:
                chat = await app.get_chat(chat_peer)
            except PeerIdInvalid:
                chat = await app.join_chat(chat_peer)
                logger.info(f"[{phone}] Успешное вступление в чат: {chat_peer}")
                await asyncio.sleep(5)

            await app.send_chat_action(chat.id, ChatAction.TYPING)
            await asyncio.sleep(random.randint(3, 6))

            # Отправляем статичный текст напрямую без парсинга
            await app.send_message(chat.id, mailing_settings["text"])
            logger.info(f"[{phone}] Отправлено в {chat_peer}")

            delay = random.randint(
                mailing_settings["min_delay"], mailing_settings["max_delay"]
            )
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"[{phone}] FloodWait: спим {e.value} сек.")
            await asyncio.sleep(e.value + 5)
        except Exception as e:
            logger.error(f"[{phone}] Пропуск чата {group_url} из-за ошибки: {e}")
            continue

    await app.stop()
    logger.info(f"[{phone}] Завершил цикл обхода групп.")


# --- СТАРТ СТРУКТУРЫ ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
