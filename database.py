import aiosqlite

DB_NAME = "bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Создаем таблицу аккаунтов с привязкой к user_id
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER,
                phone TEXT,
                session_string TEXT,
                PRIMARY KEY (user_id, phone)
            )
        """)
        # Создаем таблицу групп с привязкой к user_id
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                user_id INTEGER,
                group_url TEXT,
                PRIMARY KEY (user_id, group_url)
            )
        """)
        await db.commit()

async def add_account(user_id: int, phone: str, session_string: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO accounts (user_id, phone, session_string) VALUES (?, ?, ?)",
            (user_id, phone, session_string)
        )
        await db.commit()

async def get_accounts(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT phone, session_string FROM accounts WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchall()

async def add_group(user_id: int, group_url: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (user_id, group_url) VALUES (?, ?)",
            (user_id, group_url)
        )
        await db.commit()

async def get_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT group_url FROM groups WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def clear_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM groups WHERE user_id = ?", (user_id,))
        await db.commit()
