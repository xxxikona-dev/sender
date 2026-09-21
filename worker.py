# worker.py
import asyncio
import logging
import random
import re
from pyrogram import Client
from pyrogram.enums import ChatAction
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
    UsernameNotOccupied,
    UsernameInvalid,
    ChatWriteForbidden,
    UserBannedInChannel,
    ChannelPrivate,
    ChatAdminRequired,
    SlowmodeWait,
    ChatForbidden,
    UserAlreadyParticipant,
    InviteHashExpired,
    InviteHashInvalid,
    AuthKeyUnregistered,
    UserDeactivated,
    SessionRevoked,
    RPCError,
)
import database as db
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Worker Engine")

logging.getLogger("pyrogram").setLevel(logging.CRITICAL)
logging.getLogger("pyrogram.session").setLevel(logging.CRITICAL)
logging.getLogger("pyrogram.connection").setLevel(logging.CRITICAL)


DEAD_CHAT_ERRORS = (
    UsernameNotOccupied,
    UsernameInvalid,
    ChannelPrivate,
    ChatForbidden,
    UserBannedInChannel,
    ChatAdminRequired,
    InviteHashExpired,
    InviteHashInvalid,
)

DEAD_ACCOUNT_ERRORS = (
    AuthKeyUnregistered,
    UserDeactivated,
    SessionRevoked,
)


async def check_account_spamblock(phone: str, session_str: str):
    app = Client(
        name=f"check_{phone}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_str,
        in_memory=True
    )
    try:
        await app.start()
        logger.info(f"[{phone}] Проверка спам-блока: отправка в @Spambot...")
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
        logger.info(f"[{phone}] СПАМ-БЛОК: {status_text}")

    except DEAD_ACCOUNT_ERRORS as e:
        logger.warning(f"[{phone}] Аккаунт мёртв ({type(e).__name__}). Удаляем.")
        await db.remove_account(phone)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка проверки спам-блока: {e}")
        await db.update_spamblock(phone, "⚠️ Ошибка проверки")
    finally:
        try:
            await app.stop()
        except Exception:
            pass


async def terminate_session(phone: str, session_str: str):
    app = Client(
        name=f"kill_{phone}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_str,
        in_memory=True
    )
    try:
        await app.start()
        await app.log_out()
        await db.remove_account(phone)
        logger.info(f"[{phone}] Сессия аннулирована.")
    except DEAD_ACCOUNT_ERRORS:
        await db.remove_account(phone)
    except Exception as e:
        logger.error(f"[{phone}] Ошибка логаута ({e}). Удаляем локально.")
        await db.remove_account(phone)
    finally:
        try:
            await app.stop()
        except Exception:
            pass


def _normalize_peer(group_url: str) -> str:
    """
    Возвращает 'username', '+invite_hash' или 'id'.
    Сохраняет ведущий '+' для invite-хэшей.
    """
    s = group_url.strip()
    s = s.replace("https://t.me/", "").replace("http://t.me/", "")
    s = s.replace("t.me/", "")
    s = s.replace("joinchat/", "")

    # invite-хэш с плюсом
    if s.startswith("+"):
        return s

    # просто хэш без плюса — оставляем как есть
    # (join_chat сам разберётся)
    s = s.lstrip("@").rstrip("/")
    return s


async def send_to_group(app: Client, user_id: int, phone: str, group_url: str, text: str) -> bool:
    chat_peer = _normalize_peer(group_url)
    if not chat_peer:
        return False

    chat = None
    is_invite = chat_peer.startswith("+")

    # --- 1. Получаем чат ---
    if is_invite:
        try:
            chat = await app.join_chat(chat_peer)
            await asyncio.sleep(3)
        except UserAlreadyParticipant:
            try:
                chat = await app.get_chat(chat_peer)
            except Exception as e:
                logger.warning(f"[{phone}] get_chat после UserAlreadyParticipant: {e}")
                chat = chat_peer
        except (InviteHashExpired, InviteHashInvalid) as e:
            logger.info(f"[{phone}] Инвайт недействителен: {group_url} ({type(e).__name__})")
            await db.remove_group(user_id, group_url)
            return False
        except FloodWait as e:
            logger.warning(f"[{phone}] FloodWait при join: {e.value} сек.")
            await asyncio.sleep(e.value + 2)
            return False
        except Exception as e:
            logger.warning(f"[{phone}] Не удалось войти в {group_url}: {e}")
            return False
    else:
        try:
            chat = await app.get_chat(chat_peer)
        except PeerIdInvalid:
            try:
                chat = await app.join_chat(chat_peer)
                await asyncio.sleep(3)
            except UserAlreadyParticipant:
                try:
                    chat = await app.get_chat(chat_peer)
                except Exception as e:
                    logger.warning(f"[{phone}] get_chat после UserAlreadyParticipant: {e}")
                    chat = chat_peer
            except FloodWait as e:
                logger.warning(f"[{phone}] FloodWait при join: {e.value} сек.")
                await asyncio.sleep(e.value + 2)
                return False
            except Exception as e:
                logger.warning(f"[{phone}] Не удалось войти в {group_url}: {e}")
                return False
        except UsernameNotOccupied:
            logger.info(f"[{phone}] Username не существует: {group_url} — удаляем.")
            await db.remove_group(user_id, group_url)
            return False
        except DEAD_CHAT_ERRORS as e:
            logger.info(f"[{phone}] Чат недоступен ({type(e).__name__}): {group_url} — удаляем.")
            await db.remove_group(user_id, group_url)
            return False
        except FloodWait as e:
            logger.warning(f"[{phone}] FloodWait get_chat: {e.value} сек.")
            await asyncio.sleep(e.value + 2)
            return False
        except Exception as e:
            logger.warning(f"[{phone}] get_chat {group_url}: {e}")
            return False

    if chat is None:
        return False

    # --- 2. typing ---
    try:
        chat_id = chat.id if hasattr(chat, "id") else chat
        await app.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(random.randint(2, 4))
    except Exception:
        pass

    # --- 3. Отправка ---
    try:
        target = chat.id if hasattr(chat, "id") else chat
        await app.send_message(target, text)
        await db.log_delivery(user_id, phone, group_url)
        logger.info(f"[{phone}] ✅ Доставлено -> {chat_peer}")
        return True

    except ChatWriteForbidden:
        logger.info(f"[{phone}] 🚫 Нет прав писать в {group_url} — пропускаем.")
        return False

    except UserBannedInChannel:
        logger.info(f"[{phone}] 🚫 Забанен в {group_url} — удаляем из базы.")
        await db.remove_group(user_id, group_url)
        return False

    except SlowmodeWait as e:
        if e.value <= 60:
            logger.info(f"[{phone}] ⏳ Slowmode {e.value} сек в {group_url}, ждём...")
            await asyncio.sleep(e.value + 2)
            try:
                target = chat.id if hasattr(chat, "id") else chat
                await app.send_message(target, text)
                await db.log_delivery(user_id, phone, group_url)
                logger.info(f"[{phone}] ✅ Доставлено (после slowmode) -> {chat_peer}")
                return True
            except Exception as e2:
                logger.warning(f"[{phone}] Повтор после slowmode не удался: {e2}")
                return False
        else:
            logger.info(f"[{phone}] ⏳ Slowmode {e.value} сек — пропускаем {group_url}.")
            return False

    except FloodWait as e:
        logger.warning(f"[{phone}] FloodWait {e.value} сек — пропускаем чат.")
        await asyncio.sleep(e.value + 2)
        return False

    except UsernameNotOccupied:
        logger.info(f"[{phone}] Username исчез: {group_url} — удаляем.")
        await db.remove_group(user_id, group_url)
        return False

    except DEAD_CHAT_ERRORS as e:
        logger.info(f"[{phone}] Чат мёртв ({type(e).__name__}): {group_url} — удаляем.")
        await db.remove_group(user_id, group_url)
        return False

    except OSError as e:
        logger.warning(f"[{phone}] Разрыв сокета {group_url}: {e}")
        return False

    except RPCError as e:
        logger.warning(f"[{phone}] RPC-ошибка {group_url}: {e}")
        return False

    except Exception as e:
        logger.warning(f"[{phone}] Ошибка {group_url}: {e}")
        return False