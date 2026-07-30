# database.py (расширенный)
import aiosqlite
import time
import logging
from datetime import datetime, timedelta

DB_NAME = "bot_data.db"
logger = logging.getLogger("Database Module")

async def init_db():
    """Инициализация базы данных и создание всех необходимых таблиц при старте бота"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей с подписками
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                subscription_type TEXT DEFAULT 'none',
                subscription_expires INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                registered_at INTEGER DEFAULT 0
            )
        """)
        
        # Таблица подключенных аккаунтов (РМ) с поддержкой флага активности is_active
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER,
                phone TEXT PRIMARY KEY,
                session_string TEXT,
                spamblock_status TEXT DEFAULT 'Не проверялся',
                is_active INTEGER DEFAULT 1
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
        
        # Таблица истории платежей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                payment_id TEXT UNIQUE,
                plan_type TEXT,
                amount REAL,
                currency TEXT,
                status TEXT DEFAULT 'pending',
                created_at INTEGER,
                confirmed_at INTEGER DEFAULT 0
            )
        """)
        
        await db.commit()
        logger.info("Структура базы данных SQLite успешно проверена/инициализирована.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- БЛОК РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ И ПОДПИСКАМИ ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def register_or_update_user(user_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    """Регистрирует или обновляет информацию о пользователе"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, registered_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT registered_at FROM users WHERE user_id = ?), ?))
        """, (user_id, username, first_name, last_name, user_id, int(time.time())))
        await db.commit()


async def get_user_subscription(user_id: int):
    """Возвращает информацию о подписке пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT subscription_type, subscription_expires FROM users WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "type": row[0],
                    "expires": row[1],
                    "is_active": row[1] > int(time.time()) if row[1] else False
                }
            return {"type": "none", "expires": 0, "is_active": False}


async def update_user_subscription(user_id: int, plan_type: str, duration_days: int):
    """Обновляет подписку пользователя"""
    current_expires = int(time.time())
    
    # Если у пользователя уже есть активная подписка, продлеваем её
    sub = await get_user_subscription(user_id)
    if sub["is_active"]:
        current_expires = max(current_expires, sub["expires"])
    
    new_expires = current_expires + duration_days * 86400
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE users 
            SET subscription_type = ?, subscription_expires = ?
            WHERE user_id = ?
        """, (plan_type, new_expires, user_id))
        await db.commit()
    
    return new_expires


async def add_payment_record(user_id: int, payment_id: str, plan_type: str, amount: float, currency: str):
    """Добавляет запись о платеже"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO payments (user_id, payment_id, plan_type, amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, payment_id, plan_type, amount, currency, int(time.time())))
        await db.commit()


async def confirm_payment(payment_id: str):
    """Подтверждает оплату"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE payments 
            SET status = 'completed', confirmed_at = ?
            WHERE payment_id = ?
        """, (int(time.time()), payment_id))
        await db.commit()


async def get_payment_status(payment_id: str):
    """Получает статус платежа"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT status, user_id, plan_type FROM payments WHERE payment_id = ?", 
            (payment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"status": row[0], "user_id": row[1], "plan_type": row[2]}
            return None


async def get_user_payments(user_id: int, limit: int = 10):
    """Получает историю платежей пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT payment_id, plan_type, amount, currency, status, created_at, confirmed_at
            FROM payments 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (user_id, limit)) as cursor:
            return await cursor.fetchall()


async def get_user_total_spent(user_id: int) -> float:
    """Получает общую сумму, потраченную пользователем"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT SUM(amount) FROM payments WHERE user_id = ? AND status = 'completed'", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row[0] else 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# --- ОСТАЛЬНЫЕ ФУНКЦИИ (АККАУНТЫ, ГРУППЫ, СТАТИСТИКА) ---
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def add_account(user_id: int, phone: str, session_string: str):
    """Добавляет новый аккаунт или обновляет сессию существующего (по умолчанию активен)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO accounts (user_id, phone, session_string, is_active) VALUES (?, ?, ?, 1)",
            (user_id, phone, session_string)
        )
        await db.commit()


async def get_accounts(user_id: int):
    """Возвращает список всех аккаунтов пользователя со статусами спам-блока и активности"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT phone, session_string, spamblock_status, is_active FROM accounts WHERE user_id = ?", 
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


async def toggle_account_status(phone: str, current_status: int) -> int:
    """Переключает статус активности аккаунта"""
    new_status = 0 if current_status == 1 else 1
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE accounts SET is_active = ? WHERE phone = ?", 
            (new_status, phone)
        )
        await db.commit()
    return new_status


async def add_group(user_id: int, group_url: str):
    """Добавляет ссылку на чат в базу данных"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (user_id, group_url) VALUES (?, ?)", 
            (user_id, group_url)
        )
        await db.commit()


async def get_groups(user_id: int):
    """Возвращает список всех сохраненных ссылок на группы для пользователя"""
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


async def log_delivery(user_id: int, phone: str, group_url: str):
    """Фиксирует факт успешной отправки сообщения"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO statistics (user_id, phone, group_url, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, phone, group_url, int(time.time()))
        )
        await db.commit()


async def get_stats(user_id: int) -> dict:
    """Вычисляет количество отправленных сообщений за разные промежутки времени"""
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
