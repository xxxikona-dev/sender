import aiosqlite
import time
import logging

DB_NAME = "bot_data.db"
logger = logging.getLogger("Database Module")

async def init_db():
    """Инициализация базы данных и создание всех необходимых таблиц при старте бота"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица подключенных аккаунтов (РМ)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER,
                phone TEXT PRIMARY KEY,
                session_string TEXT,
                spamblock_status TEXT DEFAULT 'Не проверялся'
            )
        """)
        
        # Таблица базы данных чатов/узлов для рассылки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                user_id INTEGER,
                group_url TEXT,
                PRIMARY KEY (user_id, group_url)
            )
        """)
        
        # Таблица логов отправки для ведения сквозной статистики
        await db.execute("""
            CREATE TABLE IF NOT EXISTS statistics (
                user_id INTEGER,
                phone TEXT,
                group_url TEXT,
                timestamp INTEGER
            )
        """)
        await db.commit()
        logger.info("Структура базы данных SQLite успешно проверена/инициализирована.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БЛОК РАБОТЫ С АKКАУНТАМИ (СЕССИЯМИ) ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def add_account(user_id: int, phone: str, session_string: str):
    """Добавляет новый аккаунт или обновляет сессию существующего"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO accounts (user_id, phone, session_string) VALUES (?, ?, ?)",
            (user_id, phone, session_string)
        )
        await db.commit()


async def get_accounts(user_id: int):
    """Возвращает список всех аккаунтов пользователя со статусами спам-блока"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT phone, session_string, spamblock_status FROM accounts WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()


async def remove_account(phone: str):
    """Принудительно удаляет аккаунт из локальной базы данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
        await db.commit()


async def update_spamblock(phone: str, status: str):
    """Обновляет статус проверки на спам-блок для конкретного номера телефона"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE accounts SET spamblock_status = ? WHERE phone = ?", 
            (status, phone)
        )
        await db.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БЛОК РАБОТЫ С БАЗОЙ ЧАТОВ (ГРУПП) ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def add_group(user_id: int, group_url: str):
    """Добавляет ссылку на чат в базу данных (дубликаты игнорируются)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (user_id, group_url) VALUES (?, ?)", 
            (user_id, group_url)
        )
        await db.commit()


async def get_groups(user_id: int):
    """Возвращает чистый список всех сохраненных ссылок на группы для пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT group_url FROM groups WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def clear_groups(user_id: int):
    """Полностью очищает базу данных чатов для конкретного пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM groups WHERE user_id = ?", (user_id,))
        await db.commit()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БЛОК СБОРА И РАСЧЕТА СКВОЗНОЙ СТАТИСТИКИ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def log_delivery(user_id: int, phone: str, group_url: str):
    """Фиксирует факт успешной отправки сообщения (заносит Unix-время события)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO statistics (user_id, phone, group_url, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, phone, group_url, int(time.time()))
        )
        await db.commit()


async def get_stats(user_id: int) -> dict:
    """
    Вычисляет количество отправленных сообщений за разные промежутки времени.
    Возвращает словарь с готовыми данными для вывода в интерфейс.
    """
    now = int(time.time())
    
    # Временные метки-смещения относительно текущего момента (в секундах)
    one_hour = now - 3600
    one_day = now - 86400
    one_week = now - 604800
    one_month = now - 2592000

    async with aiosqlite.connect(DB_NAME) as db:
        stats = {}
        
        # Набор SQL-запросов под каждую кнопку статистики
        queries = {
            "hour": ("SELECT COUNT(*) FROM statistics WHERE user_id = ? AND timestamp >= ?", one_hour),
            "day": ("SELECT COUNT(*) FROM statistics WHERE user_id = ? AND timestamp >= ?", one_day),
            "week": ("SELECT COUNT(*) FROM statistics WHERE user_id = ? AND timestamp >= ?", one_week),
            "month": ("SELECT COUNT(*) FROM statistics WHERE user_id = ? AND timestamp >= ?", one_month),
            "all": ("SELECT COUNT(*) FROM statistics WHERE user_id = ?", None)
        }
        
        for key, (sql, param) in queries.items():
            if param is not None:
                async with db.execute(sql, (user_id, param)) as cursor:
                    row = await cursor.fetchone()
                    stats[key] = row[0]
            else:
                async with db.execute(sql, (user_id,)) as cursor:
                    row = await cursor.fetchone()
                    stats[key] = row[0]
                    
        return stats
