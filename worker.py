# worker.py
import asyncio
import logging
import random
from pyrogram import Client
from pyrogram.enums import ChatAction
from pyrogram.errors import FloodWait, PeerIdInvalid
import database as db
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Worker Engine")


async def check_account_spamblock(phone: str, session_str: str):
    """
    Фоновый запуск аккаунта для считывания вердикта от официального @Spambot.
    """
    app = Client(
        name=f"check_{phone}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_str,
        in_memory=True
    )
    try:
        await app.start()
        logger.info(f"[{phone}] Проверка спам-блока: Отправка команды в @Spambot...")

        await app.send_message("Spambot", "/start")
        await asyncio.sleep(2.5)

        status_text = "⚠️ Ошибка парсинга"

        async for message in app.get_chat_history("Spambot", limit=1):
            if message.text:
                text = message.text.lower()
                if "good news" in text or "no limits" in text or "свободен от ограничений" in text:
                    status_text = "✅ Ограничений нет"
                elif "ограничения" in text or "ограничены" in text or "limited" in text:
                    status_text = "❌ СПАМ-БЛОК"
                else:
                    status_text = "ℹ️ Измененный статус"
            else:
                status_text = "❌ Нет ответа бота"

        await db.update_spamblock(phone, status_text)
        logger.info(f"[{phone}] Результат сканирования сохранен: {status_text}")

    except Exception as e:
        logger.error(f"[{phone}] Ошибка сканирования спам-блока: {e}")
        await db.update_spamblock(phone, "⚠️ Ошибка проверки")
    finally:
        try:
            await app.stop()
        except Exception:
            pass


async def terminate_session(phone: str, session_str: str):
    """
    Полная деавторизация аккаунта на серверах Telegram (Log Out).
    """
    app = Client(
        name=f"kill_{phone}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_str,
        in_memory=True
    )
    try:
        await app.start()
        logger.info(f"[{phone}] Посылка сигнала логаута на сервера Telegram...")
        await app.log_out()
        await db.remove_account(phone)
        logger.info(f"[{phone}] Сессия успешно аннулирована и стерта из базы.")
    except Exception as e:
        logger.error(f"[{phone}] Серверный логаут не удался ({e}). Принудительное локальное удаление.")
        await db.remove_account(phone)


async def send_to_group(app: Client, user_id: int, phone: str, group_url: str, text: str):
    """
    Ядро отправки сообщения в конкретный чат.
    """
    try:
        chat_peer = group_url.replace("https://t.me/", "").replace("@", "").strip()

        try:
            chat = await app.get_chat(chat_peer)
        except PeerIdInvalid:
            chat = await app.join_chat(chat_peer)
            await asyncio.sleep(3)

        try:
            await app.send_chat_action(chat.id, ChatAction.TYPING)
            await asyncio.sleep(random.randint(2, 4))
        except Exception:
            pass

        await app.send_message(chat.id, text)

        await db.log_delivery(user_id, phone, group_url)
        logger.info(f"[{phone}] Доставлено в чат -> {chat_peer}")

    except FloodWait as e:
        logger.warning(f"[{phone}] FloodWait на {e.value} сек.")
        await asyncio.sleep(e.value + 2)
    except OSError:
        logger.error(f"[{phone}] Разрыв сокета при работе с узлом {group_url}")
    except Exception as e:
        logger.error(f"[{phone}] Ошибка отправки в чат {group_url} -> {e}")
