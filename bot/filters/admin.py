from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject, User

from bot.config import Settings


class IsAdmin(BaseFilter):
    """Работает и для сообщений, и для нажатий кнопок."""

    async def __call__(
        self, event: TelegramObject, settings: Settings, event_from_user: User | None = None
    ) -> bool:
        return event_from_user is not None and event_from_user.id in settings.admin_ids
