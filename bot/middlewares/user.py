import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import TelegramObject

from bot.db import Database

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Сохраняет/обновляет пользователя в базе при каждом апдейте."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        db: Database | None = data.get("db")
        chat = data.get("event_chat")
        # Бот работает только в личке: участники групп не клиенты и не должны попадать в /stats
        private = chat is None or chat.type == ChatType.PRIVATE
        if user is not None and db is not None and not user.is_bot and private:
            try:
                await db.upsert_user(user.id, user.username, user.full_name)
            except Exception:
                # Имя пользователя — не главное. Сбой записи не должен выбрасывать апдейт:
                # среди них оплаты, и Telegram второй раз их не пришлёт
                logger.exception("Не удалось сохранить пользователя %s", user.id)
        return await handler(event, data)
