import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.ratelimit import RateLimiter

# Человеку хватает с запасом, а флуд нажатиями через скрипт не грузит базу, Crypto Pay
# и Telegram API за счёт остальных клиентов
EVENTS_PER_SECOND = 3
BURST = 10
ALBUM_SECONDS = 60  # сколько помним пропущенный альбом


class ThrottleMiddleware(BaseMiddleware):
    """Ограничение частоты сообщений и нажатий от одного пользователя.

    Не трогает оплату и админов. Альбом считается одним событием: он приходит пачкой до
    10 апдейтов, и вместе с нажатием «Написать в поддержку» последний снимок отбрасывался.
    """

    def __init__(self, rate: float = EVENTS_PER_SECOND, burst: float = BURST) -> None:
        self.limiter = RateLimiter(rate, burst)
        self._albums: dict[str, float] = {}  # media_group_id -> когда пропустили первый снимок

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.successful_payment is not None:
            return await handler(event, data)  # оплату обрабатываем всегда
        user = data.get("event_from_user")
        settings = data.get("settings")
        if user is None or (settings is not None and user.id in settings.admin_ids):
            return await handler(event, data)
        album = event.media_group_id if isinstance(event, Message) else None
        if album is not None and album in self._albums:
            return await handler(event, data)
        if self.limiter.allow(str(user.id)):
            if album is not None:
                self._remember(album)
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            # гасим «часики» на кнопке, но ничего не делаем
            try:
                await event.answer()
            except Exception:  # noqa: BLE001 — при флуде ответ не важен
                pass
        return None

    def _remember(self, album: str) -> None:
        now = time.monotonic()
        if len(self._albums) > 1000:
            self._albums = {k: t for k, t in self._albums.items() if now - t < ALBUM_SECONDS}
        self._albums[album] = now
