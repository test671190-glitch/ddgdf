"""Обработчики бота на входных данных, которые пользователь может ввести руками."""

import asyncio
import html
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.filters import CommandObject
from aiogram.methods import RefundStarPayment

from bot.config import Settings
from bot.db import Database, _iso, utcnow
from bot.handlers.admin import (
    cmd_addkeys,
    cmd_givekey,
    cmd_grant,
    cmd_refund,
    cmd_revoke_list,
    cmd_user,
)
from bot.handlers.payments import on_successful_payment
from bot.handlers.sections import cmd_start
from bot.keyboards.inline import WebLogin
from bot.services.expiry import check_subscriptions


class FakeMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=42, first_name="Neo")
        self.answers: list[tuple[str, dict]] = []
        self.deleted = False

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))

    async def delete(self) -> None:
        self.deleted = True


class FakeBot:
    def __init__(self, refund_errors=()) -> None:
        self.sent: list[tuple[int, str]] = []
        self.refund_errors = list(refund_errors)

    async def send_message(self, chat_id, text, **_):
        self.sent.append((chat_id, text))

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        if self.refund_errors:
            raise self.refund_errors.pop(0)
        return True


SETTINGS = Settings(bot_token="1:x", admin_ids=[1000])


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


async def test_addkeys_delivers_keys_to_waiting_payers(db):
    """Клиент оплатил при пустом пуле. После /addkeys он сразу получает ключ."""
    db.hide_keys = True
    await db.activate_paid(7, "pro", 30, 2541, "c1")  # пул пуст
    bot = FakeBot()
    message = FakeMessage()
    settings = Settings(bot_token="1:x", public_api_url="https://x.sslip.io/v1")
    await cmd_addkeys(message, admin_command("addkeys", "pro\nup-1\nup-2"), bot, db, settings)

    client_key = await db.get_client_key(7)
    assert client_key and client_key.startswith("sk-us-")
    assert [chat for chat, _ in bot.sent] == [7]
    assert client_key in bot.sent[0][1] and "up-1" not in bot.sent[0][1]
    assert "ожидавшим оплатившим: 1" in message.answers[0][0]
    assert message.deleted


@pytest.mark.parametrize("token", ["x" * 100, "a:b", ""])
async def test_start_with_broken_login_token_shows_menu(db, token):
    """Команду /start можно набрать руками: неверный токен не должен ронять обработчик."""
    message = FakeMessage()
    await cmd_start(message, start("login_" + token), db, Settings(bot_token="1:x"), None)
    assert len(message.answers) == 1
    assert "Привет" in message.answers[0][0]


async def test_refund_after_lost_telegram_answer_still_closes_subscription(db):
    """Telegram вернул Stars, но ответ не дошёл (таймаут). Повторный /refund получает
    CHARGE_ALREADY_REFUNDED: подписку и ключ всё равно надо закрыть, а не оставить клиенту
    и доступ, и деньги."""
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(7, "pro", 30, 2541, "ch-1")
    method = RefundStarPayment(user_id=7, telegram_payment_charge_id="ch-1")
    bot = FakeBot(
        refund_errors=[
            TelegramNetworkError(method=method, message="Request timeout error"),
            TelegramBadRequest(method=method, message="Bad Request: CHARGE_ALREADY_REFUNDED"),
        ]
    )
    first = FakeMessage()
    await cmd_refund(first, admin_command("refund", "ch-1"), bot, db, SETTINGS)
    assert "повторите" in first.answers[0][0]
    assert not (await db.get_payment("ch-1")).refunded

    retry = FakeMessage()
    await cmd_refund(retry, admin_command("refund", "ch-1"), bot, db, SETTINGS)
    assert retry.answers[0][0].startswith("✅")
    assert (await db.get_payment("ch-1")).refunded
    assert not (await db.get_subscription(7)).is_active


async def test_crypto_refund_sent_twice_is_reported_once(db):
    """Две одновременные /refund crypto:N: админ не должен дважды получить «верните крипту»,
    а клиент — два сообщения о возврате."""
    order = await db.create_crypto_order(7, "pro")
    await db.set_crypto_invoice(order.id, 1, "https://t.me/x", utcnow() + timedelta(hours=1))
    await db.approve_crypto_order(order.id, 30)
    bot = FakeBot()
    first, second = FakeMessage(), FakeMessage()
    command = admin_command("refund", f"crypto:{order.id}")
    await asyncio.gather(
        cmd_refund(first, command, bot, db, SETTINGS),
        cmd_refund(second, command, bot, db, SETTINGS),
    )
    replies = [text for text, _ in first.answers + second.answers]
    assert sum(text.startswith("✅") for text in replies) == 1
    assert [chat for chat, _ in bot.sent] == [7]


@pytest.mark.parametrize(
    ("handler", "command", "args", "reply"),
    [
        (cmd_grant, "grant", "7 pro 99999999", "не больше"),
        (cmd_grant, "grant", "1" * 20 + " pro", "Формат"),
        (cmd_givekey, "givekey", "1" * 20 + " key", "Формат"),
        (cmd_user, "user", "1" * 20, "Формат"),
        (cmd_user, "user", "9223372036854775808", "Формат"),
    ],
)
async def test_admin_commands_reject_huge_numbers(db, handler, command, args, reply):
    """Огромное число падало в SQLite/datetime с OverflowError, и админ не получал ответа."""
    message = FakeMessage()
    kwargs = {"message": message, "command": admin_command(command, args), "db": db}
    if handler is not cmd_user:
        kwargs |= {"bot": FakeBot(), "settings": SETTINGS}
    await handler(**kwargs)
    assert reply in message.answers[0][0]


async def test_revoke_list_fits_telegram_limit(db):
    """60 ключей длиной как у OpenAI: сообщение длиннее 4096 символов Telegram не примет."""
    keys = [f"sk-{i:048d}" for i in range(60)]
    await db.add_keys("unlimited", keys)
    for i in range(60):
        await db.activate_paid(7_000_000_000 + i, "unlimited", 30, 1, f"c{i}")
    await db.conn.execute("UPDATE subscriptions SET expires_at=?", (_iso(utcnow()),))
    await db.conn.commit()
    await db.expire_due()
    message = FakeMessage()
    await cmd_revoke_list(message, db)
    visible = html.unescape(re.sub(r"<[^>]+>", "", message.answers[0][0]))
    assert len(visible.encode("utf-16-le")) // 2 <= 4096
    assert "…и ещё" in visible


async def test_addkeys_with_typo_removes_keys_from_chat(db):
    message = FakeMessage()
    await cmd_addkeys(message, admin_command("addkeys", "Pro\nsk-1\nsk-2"), FakeBot(), db, SETTINGS)
    assert "Формат" in message.answers[0][0]
    assert message.deleted


async def test_givekey_removes_key_from_chat(db):
    await db.grant(7, "pro", 30)
    message = FakeMessage()
    await cmd_givekey(message, admin_command("givekey", "7 sk-secret"), FakeBot(), db, SETTINGS)
    assert message.answers[0][0].startswith("✅")
    assert message.deleted


async def test_freed_key_goes_to_waiting_payer():
    """Ключ освободился (подписка кончилась) — его сразу получает тот, кто оплатил при
    пустом пуле, а не ждёт ручной /givekey."""
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 1, "c1")
    await db.activate_paid(2, "pro", 30, 1, "c2")  # пул пуст — ждёт
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    await db.conn.commit()
    bot = FakeBot()
    settings = Settings(bot_token="1:x", public_api_url="https://x.sslip.io/v1")
    await check_subscriptions(bot, db, settings)
    assert (await db.get_active_key(2)).key == "k1"
    client_key = await db.get_client_key(2)
    assert any(chat == 2 and client_key in text for chat, text in bot.sent)
    await db.close()


def stars_payment(payload: str, charge_id: str = "ch-1") -> SimpleNamespace:
    message = FakeMessage()
    message.from_user = SimpleNamespace(id=7, username="neo", full_name="Neo")
    message.successful_payment = SimpleNamespace(
        invoice_payload=payload, total_amount=2541, telegram_payment_charge_id=charge_id
    )
    return message


async def test_payment_for_tariff_removed_from_sale_is_credited(db, monkeypatch):
    """Тариф сняли с продажи между pre_checkout и successful_payment: деньги уже списаны,
    подписку надо начислить, а не отвечать «тариф не распознан»."""
    import bot.handlers.payments as payments

    monkeypatch.setattr(payments, "TARIFFS_ON_SALE", {})
    message = stars_payment("tariff:pro")
    await on_successful_payment(message, FakeBot(), db, SETTINGS)
    assert (await db.get_subscription(7)).tariff == "pro"


async def test_payment_db_error_is_reported_to_admin(db):
    """Сбой базы при записи оплаты: Telegram этот апдейт больше не пришлёт, поэтому
    админ должен получить charge_id, а клиент — ответ."""
    await db.close()  # любая запись теперь падает
    bot = FakeBot()
    message = stars_payment("tariff:pro", "ch-lost")
    await on_successful_payment(message, bot, db, SETTINGS)
    assert any(chat == 1000 and "ch-lost" in text for chat, text in bot.sent)
    assert "Оплата получена" in message.answers[0][0]
    await db.connect()  # фикстура закроет базу ещё раз


async def test_refund_telegram_5xx_answers_admin(db):
    """Ошибку 5xx Telegram при возврате никто не ловил: админ оставался без ответа,
    хотя Stars могли уже вернуться."""
    await db.activate_paid(7, "pro", 30, 2541, "ch-1")
    method = RefundStarPayment(user_id=7, telegram_payment_charge_id="ch-1")
    bot = FakeBot(refund_errors=[TelegramServerError(method=method, message="Bad Gateway")])
    message = FakeMessage()
    await cmd_refund(message, admin_command("refund", "ch-1"), bot, db, SETTINGS)
    assert "повторите" in message.answers[0][0]
    assert not (await db.get_payment("ch-1")).refunded


async def test_addkeys_single_key_without_tariff_is_deleted(db):
    message = FakeMessage()
    await cmd_addkeys(message, admin_command("addkeys", "sk-secret"), FakeBot(), db, SETTINGS)
    assert "Формат" in message.answers[0][0]
    assert message.deleted
