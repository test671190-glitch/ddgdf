"""Запуск: сбой Telegram не должен останавливать процесс вместе с эндпоинтом."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
from aiogram.methods import GetMe
from aiogram.utils.token import TokenValidationError

import bot.__main__ as main_module
from bot.config import Settings


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


def bad_token_settings(monkeypatch, tmp_path, **kwargs) -> Path:
    """main() получит настройки с токеном неверного формата: на создании бота он упадёт
    раньше, чем полезет в сеть. Возвращает путь к базе."""
    db_path = tmp_path / "data" / "bot.db"
    settings = Settings(_env_file=None, bot_token="not-a-token", db_path=str(db_path), **kwargs)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return db_path


async def test_bad_token_format_exits_without_opening_db(monkeypatch, tmp_path):
    """Неверный формат BOT_TOKEN падал уже после открытия базы. Её поток не фоновый —
    процесс не завершался, и systemd считал бота работающим."""
    db_path = bad_token_settings(monkeypatch, tmp_path, public_api_url="https://x.sslip.io/v1")
    with pytest.raises(TokenValidationError):
        await main_module.main()
    assert not db_path.exists()


async def test_direct_mode_needs_explicit_permission(monkeypatch, tmp_path, caplog):
    """Без PUBLIC_API_URL бот раздавал клиентам ключи провайдера — так случилось бы, если
    строка пропала из .env при правке. Теперь прямой режим включается только явно, а без
    него бот завершается сразу: до Telegram, базы и ключей дело не доходит."""
    db_path = bad_token_settings(monkeypatch, tmp_path)
    with caplog.at_level(logging.CRITICAL):
        await main_module.main()  # старый код упал бы тут на токене
    assert "ALLOW_DIRECT_KEYS=true" in caplog.text
    assert not db_path.exists()

    bad_token_settings(monkeypatch, tmp_path, allow_direct_keys=True)
    with pytest.raises(TokenValidationError):  # с разрешением запуск идёт дальше
        await main_module.main()
