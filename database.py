import aiosqlite
from config import DB_NAME


async def init_db():
    """Инициализация таблиц базы данных при старте"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица для хранения авторизованных юзерботов
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                phone TEXT PRIMARY KEY,
                string_session TEXT NOT NULL
            )
        """
        )
        # Таблица для хранения списка групп для рассылки
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_url TEXT UNIQUE
            )
        """
        )
        await db.commit()


async def add_account(phone: str, string_session: str):
    """Сохранение новой String-сессии в базу"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO accounts (phone, string_session) VALUES (?, ?)",
            (phone, string_session),
        )
        await db.commit()


async def get_accounts():
    """Получение всех сохраненных аккаунтов"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT phone, string_session FROM accounts") as cursor:
            return await cursor.fetchall()


async def add_group(url: str):
    """Добавление одной группы в базу (дубликаты игнорируются)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (group_url) VALUES (?)", (url,)
        )
        await db.commit()


async def get_groups():
    """Получение полного списка групп для рассылки"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT group_url FROM groups") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def clear_groups():
    """Полное удаление всех групп из базы данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM groups")
        await db.commit()
