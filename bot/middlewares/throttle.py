from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.ratelimit import RateLimiter

# Человеку хватает с запасом (альбом из 10 фото — один всплеск), а флуд нажатиями через
# скрипт не грузит базу, Crypto Pay и Telegram API за счёт остальных клиентов
EVENTS_PER_SECOND = 3
BURST = 10


class ThrottleMiddleware(BaseMiddleware):
    """Ограничение частоты сообщений и нажатий от одного пользователя. Платежи не трогает."""

    def __init__(self, rate: float = EVENTS_PER_SECOND, burst: float = BURST) -> None:
        self.limiter = RateLimiter(rate, burst)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.successful_payment is not None:
            return await handler(event, data)  # оплату обрабатываем всегда
        user = data.get("event_from_user")
        if user is None or self.limiter.allow(str(user.id)):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            # гасим «часики» на кнопке, но ничего не делаем
            try:
                await event.answer()
            except Exception:  # noqa: BLE001 — при флуде ответ не важен
                pass
        return None
