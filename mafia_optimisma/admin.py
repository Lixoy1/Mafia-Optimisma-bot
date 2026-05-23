from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

ADMIN_STATUSES = {"creator", "administrator"}


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if chat_id > 0:
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return getattr(member, "status", None) in ADMIN_STATUSES
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        return False


async def require_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    return await is_chat_admin(bot, chat_id, user_id)
