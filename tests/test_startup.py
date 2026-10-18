"""Запуск: сбой Telegram не должен останавливать процесс вместе с эндпоинтом."""

from types import SimpleNamespace

from aiogram.exceptions import TelegramNetworkError, TelegramServerError
from aiogram.methods import GetMe

import bot.__main__ as main_module


class FlakyBot:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.webhook_deleted = False

    async def get_me(self):
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(username="UnknownAiShopBot")

    async def set_my_commands(self, commands) -> None:
        pass

    async def delete_webhook(self, drop_pending_updates: bool) -> None:
        assert drop_pending_updates is False  # среди апдейтов могут быть оплаты
        self.webhook_deleted = True


async def test_telegram_outage_is_retried_not_fatal(monkeypatch):
    """Раньше при недоступном api.telegram.org main() завершался раньше, чем поднимался
    эндпоинт: оплатившие клиенты получали 502 всё время сбоя."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    method = GetMe()
    bot = FlakyBot(
        [
            TelegramNetworkError(method=method, message="timeout"),
            TelegramServerError(method=method, message="Bad Gateway"),
        ]
    )
    me = await main_module.connect_telegram(bot)
    assert me.username == "UnknownAiShopBot"
    assert bot.webhook_deleted
    assert waits == [5, 10]
