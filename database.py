# database.py
import aiosqlite
import time
import logging

DB_NAME = "bot_data.db"
logger = logging.getLogger("Database Module")


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                registered_at INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER,
                phone TEXT PRIMARY KEY,
                session_string TEXT,
                spamblock_status TEXT DEFAULT 'Не проверялся',
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                user_id INTEGER,
                group_url TEXT,
                PRIMARY KEY (user_id, group_url)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS statistics (
                user_id INTEGER,
                phone TEXT,
                group_url TEXT,
                timestamp INTEGER
            )
        """)
        await db.commit()
        logger.info("БД инициализирована.")


async def register_or_update_user(user_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, registered_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT registered_at FROM users WHERE user_id = ?), ?))
        """, (user_id, username, first_name, last_name, user_id, int(time.time())))
        await db.commit()


async def add_account(user_id: int, phone: str, session_string: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO accounts (user_id, phone, session_string, is_active) VALUES (?, ?, ?, 1)",
            (user_id, phone, session_string)
        )
        await db.commit()


async def get_accounts(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT phone, session_string, spamblock_status, is_active FROM accounts WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()


async def remove_account(phone: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
        await db.commit()


async def update_spamblock(phone: str, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE accounts SET spamblock_status = ? WHERE phone = ?",
            (status, phone)
        )
        await db.commit()


async def toggle_account_status(phone: str, current_status: int) -> int:
    new_status = 0 if current_status == 1 else 1
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE accounts SET is_active = ? WHERE phone = ?",
            (new_status, phone)
        )
        await db.commit()
    return new_status


async def add_group(user_id: int, group_url: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (user_id, group_url) VALUES (?, ?)",
            (user_id, group_url)
        )
        await db.commit()


async def get_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT group_url FROM groups WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def remove_group(user_id: int, group_url: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM groups WHERE user_id = ? AND group_url = ?",
            (user_id, group_url)
        )
        await db.commit()


async def clear_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM groups WHERE user_id = ?", (user_id,))
        await db.commit()


async def log_delivery(user_id: int, phone: str, group_url: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO statistics (user_id, phone, group_url, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, phone, group_url, int(time.time()))
        )
        await db.commit()


async def get_stats(user_id: int) -> dict:
    now = int(time.time())
    one_hour = now - 3600
    one_day = now - 86400
    one_week = now - 604800
    one_month = now - 2592000

    async with aiosqlite.connect(DB_NAME) as db:
        stats = {}
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