"""Журнал без секретов: токены бота и Crypto Pay не должны попадать в journalctl."""

import logging
import re

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Токен бота 123456789:AA… — 35 символов после двоеточия; в адресах Bot API он идёт
# после «bot». Токен Crypto Pay: 12345:AA…. Номер до двоеточия не секрет, его оставляем
TOKEN = re.compile(r"(\d+):(?:AA[\w-]+|[\w-]{35,})")


def redact(text: str) -> str:
    return TOKEN.sub(r"\1:***", text)


class RedactingFormatter(logging.Formatter):
    """Маскирует токены во всём тексте записи, вместе с трейсбеком исключения."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(FORMAT))
    logging.basicConfig(level=level, handlers=[handler])
