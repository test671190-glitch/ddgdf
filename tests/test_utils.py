"""Отправка сообщений: лимит частоты Telegram не должен терять напоминания и ключи."""

import logging

from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage

import bot.utils as utils
from bot.config import Settings
from bot.services.access import revoke_notice
from bot.utils import mask_key, notify_admins, safe_send

METHOD = SendMessage(chat_id=7, text="x")


class FloodBot:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.sent: list[int] = []

    async def send_message(self, chat_id, text, **_):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(chat_id)


async def no_sleep(waits: list[float]):
    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    return fake_sleep


async def test_flood_limit_waits_and_retries(monkeypatch):
    """При массовой рассылке Telegram отвечает «подождите N секунд». Раньше сообщение
    просто терялось, а напоминание отмечалось отправленным."""
    waits: list[float] = []
    monkeypatch.setattr(utils.asyncio, "sleep", await no_sleep(waits))
    bot = FloodBot([TelegramRetryAfter(method=METHOD, message="Too Many Requests", retry_after=3)])
    assert await safe_send(bot, 7, "напоминание")
    assert bot.sent == [7] and waits == [3]


async def test_flood_limit_gives_up_after_one_retry(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(utils.asyncio, "sleep", await no_sleep(waits))
    flood = [TelegramRetryAfter(method=METHOD, message="flood", retry_after=1) for _ in range(2)]
    bot = FloodBot(flood)
    assert not await safe_send(bot, 7, "x")
    assert waits == [1]


async def test_too_long_wait_is_not_awaited(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(utils.asyncio, "sleep", await no_sleep(waits))
    bot = FloodBot([TelegramRetryAfter(method=METHOD, message="flood", retry_after=3600)])
    assert not await safe_send(bot, 7, "x")
    assert waits == []


async def test_network_glitch_is_retried(monkeypatch):
    """Сбой связи с Telegram в момент оплаты криптой: сообщение с ключом терялось."""
    waits: list[float] = []
    monkeypatch.setattr(utils.asyncio, "sleep", await no_sleep(waits))
    bot = FloodBot(
        [
            TelegramNetworkError(method=METHOD, message="timeout"),
        ]
    )
    assert await safe_send(bot, 7, "ваш ключ")
    assert bot.sent == [7] and waits == [utils.NETWORK_RETRY_DELAY]

    bot = FloodBot([TelegramServerError(method=METHOD, message="Bad Gateway") for _ in range(2)])
    assert not await safe_send(bot, 7, "ваш ключ")


async def test_blocked_user_is_not_retried(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(utils.asyncio, "sleep", await no_sleep(waits))
    bot = FloodBot([TelegramForbiddenError(method=METHOD, message="bot was blocked by the user")])
    assert not await safe_send(bot, 7, "x")
    assert waits == [] and bot.sent == []


async def test_lost_admin_notice_does_not_log_keys(caplog):
    """Без ADMIN_IDS текст уведомления уходил в журнал целиком, а в уведомлениях бывают
    ключи провайдера — например, «деактивируйте ключ …»."""
    notice = revoke_notice(42, "up-provider-secret-key", "смена тарифа")
    with caplog.at_level(logging.WARNING):
        await notify_admins(FloodBot([]), Settings(_env_file=None, bot_token="1:x"), notice)
    assert "ADMIN_IDS" in caplog.text
    assert "up-provider-secret-key" not in caplog.text


def test_mask_key_keeps_only_tail():
    assert mask_key("sk-us-0123456789abcdef") == "…abcdef"
    assert mask_key("short") == "…"  # у короткого ключа хвост — почти весь ключ
