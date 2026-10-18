"""Обработчики бота на входных данных, которые пользователь может ввести руками."""

from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject

from bot.config import Settings
from bot.db import Database
from bot.handlers.admin import cmd_givekey, cmd_grant, cmd_user
from bot.handlers.sections import cmd_start
from bot.keyboards.inline import WebLogin


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=42, first_name="Neo")
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


def start(args: str) -> CommandObject:
    return CommandObject(prefix="/", command="start", args=args)


async def test_start_with_login_token_asks_confirmation(db):
    message = FakeMessage()
    await cmd_start(message, start("login_abc-DEF_123"), db, Settings(bot_token="1:x"), None)
    markup = message.answers[0][1]["reply_markup"]
    data = markup.inline_keyboard[0][0].callback_data
    assert WebLogin.unpack(data).token == "abc-DEF_123"


def admin_command(command: str, args: str) -> CommandObject:
    return CommandObject(prefix="/", command=command, args=args)


@pytest.mark.parametrize(
    ("handler", "command", "args", "reply"),
    [
        (cmd_grant, "grant", "42 pro ²", "положительным числом"),
        (cmd_grant, "grant", "²2 pro", "Формат"),
        (cmd_givekey, "givekey", "² key", "Формат"),
        (cmd_user, "user", "²", "Формат"),
    ],
)
async def test_admin_commands_reject_non_decimal_digits(db, handler, command, args, reply):
    """«²» проходит str.isdigit(), но int("²") падает: админ не получал никакого ответа."""
    message = FakeMessage()
    kwargs = {"message": message, "command": admin_command(command, args), "db": db}
    if handler is not cmd_user:
        kwargs |= {"bot": None, "settings": Settings(bot_token="1:x")}
    await handler(**kwargs)
    assert reply in message.answers[0][0]


@pytest.mark.parametrize("token", ["x" * 100, "a:b", ""])
async def test_start_with_broken_login_token_shows_menu(db, token):
    """Команду /start можно набрать руками: неверный токен не должен ронять обработчик."""
    message = FakeMessage()
    await cmd_start(message, start("login_" + token), db, Settings(bot_token="1:x"), None)
    assert len(message.answers) == 1
    assert "Привет" in message.answers[0][0]
