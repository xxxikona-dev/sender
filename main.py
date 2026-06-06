import asyncio
import logging
import os
import io
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

import config
import database as db
import worker # Импортируем наш исполнительный воркер

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- КЛАВИАТУРЫ ---

def get_main_menu():
    text = "💼 **WORKSPACE CONTROL PANEL v3.0**\n\nВыберите целевой блок управления:"
    buttons = [
        [InlineKeyboardButton(text="📱 Управление РМ (Сессии)", callback_data="manage_accounts")],
        [InlineKeyboardButton(text="👥 База чатов (.txt)", callback_data="manage_groups")],
        [InlineKeyboardButton(text="📊 Сквозная статистика", callback_data="view_statistics")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_accounts_keyboard(user_id: int):
    accounts = await db.get_accounts(user_id)
    buttons = []
    
    if not accounts:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\n❌ Нет подключенных аккаунтов."
    else:
        text = "📱 **УПРАВЛЕНИЕ РАБОЧИМИ МЕСТАМИ**\n\nСписок ваших активных шлюзов и их статусы спамблока:"
        for phone, _, status in accounts:
            text += f"\n• `{phone}` — *{status}*"
            # Кнопка для индивидуального управления аккаунтом
            buttons.append([InlineKeyboardButton(text=f"⚙️ Управление {phone}", callback_data=f"act_{phone}")])
            
    buttons.append([InlineKeyboardButton(text="🛡 Проверить СПАМ-БЛОК", callback_data="check_all_spam")])
    if accounts:
        buttons.append([InlineKeyboardButton(text="💥 Завершить ВСЕ сессии", callback_data="kill_all_sessions")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])
    
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ХЕНДЛЕРЫ МЕНЮ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text, markup = get_main_menu()
    await message.answer(text, parse_mode="Markdown", reply_markup=markup)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    text, markup = get_main_menu()
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)

# --- БЛОК УПРАВЛЕНИЯ СЕССИЯМИ ---

@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts_cmd(callback: types.CallbackQuery):
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)

@dp.callback_query(F.data.startswith("act_"))
async def individual_account_manage(callback: types.CallbackQuery):
    phone = callback.data.replace("act_", "")
    text = f"⚙️ **Управление аккаунтом** `{phone}`\n\nВы можете принудительно деавторизовать данную сессию. Она сотрется из бота и закроется в Telegram."
    buttons = [
        [InlineKeyboardButton(text="🛑 Завершить эту сессию", callback_data=f"kill_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="manage_accounts")]
    ]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("kill_"))
async def kill_single_session_handler(callback: types.CallbackQuery):
    phone = callback.data.replace("kill_", "")
    accounts = await db.get_accounts(callback.from_user.id)
    
    # Ищем нужную сессию
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
        
    await callback.message.edit_text("⏳ Глобальный сброс. Уничтожаем все подключенные сессии... Это может занять время.", parse_mode="Markdown")
    
    for phone, session_str, _ in accounts:
        await worker.terminate_session(phone, session_str)
        
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.answer("💥 Все сессии были успешно аннулированы.", parse_mode="Markdown")
    await callback.message.delete()
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)

# --- БЛОК СПАМ-БЛОКА ---

@dp.callback_query(F.data == "check_all_spam")
async def check_all_spam_handler(callback: types.CallbackQuery):
    accounts = await db.get_accounts(callback.from_user.id)
    if not accounts:
        await callback.answer("Локальная база сессий пуста.")
        return
        
    await callback.message.edit_text("🛡 **Сканирование инфраструктуры...**\n\nВоркер поочередно опрашивает @Spambot. Пожалуйста, подождите.", parse_mode="Markdown")
    
    for phone, session_str, _ in accounts:
        await worker.check_account_spamblock(phone, session_str)
        await asyncio.sleep(1) # Защитный интервал
        
    text, markup = await get_accounts_keyboard(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer("✅ Проверка всей сети завершена!", parse_mode="Markdown")
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)

# --- БЛОК СТАТИСТИКИ ---

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
    buttons = [[InlineKeyboardButton(text="⬅️ Вернуться", callback_data="back_to_menu")]]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# --- БЛОК ВЫГРУЗКИ ЧАТОВ (.TXT) ---

@dp.callback_query(F.data == "manage_groups")
async def manage_groups_menu(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    text = f"👥 **УПРАВЛЕНИЕ БАЗОЙ АДРЕСАТОВ**\n\nСейчас в вашей базе находится чатов/каналов: **{len(groups)}** шт.\n\nВы можете выгрузить всю базу в виде файла `.txt`."
    buttons = [
        [InlineKeyboardButton(text="📥 Скачать базу .txt", callback_data="download_chats")],
        [InlineKeyboardButton(text="⬅️ Вернуться", callback_data="back_to_menu")]
    ]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data == "download_chats")
async def download_chats_handler(callback: types.CallbackQuery):
    groups = await db.get_groups(callback.from_user.id)
    if not groups:
        await callback.answer("Ваша база чатов пуста, нечего выгружать.", show_alert=True)
        return
        
    await callback.answer("Формирую файл...")
    
    # Собираем текстовый файл в буфере памяти, не трогая жесткий диск
    text_data = "\n".join(groups)
    file_buffer = io.BytesIO(text_data.encode('utf-8'))
    
    document = BufferedInputFile(file_buffer.read(), filename="database_chats.txt")
    
    await bot.send_document(
        chat_id=callback.message.chat.id,
        document=document,
        caption=f"📂 Полная резервная копия базы данных чатов.\nВсего записей: {len(groups)}"
    )

# --- ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ---
async def main():
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
