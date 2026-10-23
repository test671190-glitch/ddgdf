"""Журнал: токены бота и Crypto Pay не должны попадать в journalctl."""

import io
import logging
import sys

from bot.logs import RedactingFormatter, setup_logging

# Ненастоящие токены в формате настоящих
BOT_TOKEN = "123456789:AAFakeBotTokenForTests-0123456789xyz"
OLD_BOT_TOKEN = "987654321:" + "Zq_-" * 8 + "Zq_"  # 35 символов, не с AA
CRYPTO_TOKEN = "12345:AAFakeCryptoPayTokenForTests012"


def test_tokens_are_masked_in_message_and_traceback():
    """Текст ошибки с адресом запроса к Bot API (в адресе — токен бота) уходил в журнал
    как есть, а журнал читают не только владельцы токена."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("test_logs")
    logger.addHandler(handler)
    try:
        try:
            raise ConnectionError(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates")
        except ConnectionError:
            logger.exception("Crypto Pay %s, старый бот %s", CRYPTO_TOKEN, OLD_BOT_TOKEN)
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    for token in (BOT_TOKEN, OLD_BOT_TOKEN, CRYPTO_TOKEN):
        assert token not in text
    assert "bot123456789:***/getUpdates" in text
    assert "Crypto Pay 12345:***, старый бот 987654321:***" in text


def test_main_logging_masks_tokens(monkeypatch):
    """main() настраивает журнал через setup_logging: проверяем его в том виде, что на сервере."""
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])  # basicConfig настраивает только пустой журнал
    level = root.level
    try:
        setup_logging("INFO")
        logging.getLogger("aiogram.dispatcher").warning("Запрос к bot%s не прошёл", BOT_TOKEN)
    finally:
        root.setLevel(level)
    assert BOT_TOKEN not in stream.getvalue()
    assert "bot123456789:***" in stream.getvalue()
