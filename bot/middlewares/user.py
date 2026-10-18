from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db import Database


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
        if user is not None and db is not None and not user.is_bot:
            await db.upsert_user(user.id, user.username, user.full_name)
        return await handler(event, data)
