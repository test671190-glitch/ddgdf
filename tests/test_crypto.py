"""Оплата криптой: создание счетов без дублей и проверка оплаты."""

import asyncio
from datetime import timedelta

import pytest

import bot.services.crypto as crypto_service
from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import Database, utcnow
from bot.services.crypto import CryptoPayments
from bot.services.cryptopay import Invoice


class SlowCryptoClient:
    """Crypto Pay отвечает не мгновенно — как в жизни."""

    def __init__(self) -> None:
        self.created = 0

    async def create_invoice(self, amount_usd, description, payload, expires_in) -> Invoice:
        self.created += 1
        await asyncio.sleep(0.05)
        return Invoice(
            100 + self.created, "active", f"https://t.me/CryptoBot?start=IV{self.created}", payload
        )

    async def get_invoices(self, invoice_ids):
        return []


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def test_double_click_creates_one_invoice(db):
    """Двойное нажатие «Оплатить криптой»: два одновременных запроса не должны выставить
    два счёта — иначе клиент может оплатить оба."""
    client = SlowCryptoClient()
    payments = CryptoPayments(client, db, bot=None, settings=Settings(bot_token="1:x"))
    first, second = await asyncio.gather(
        payments.get_or_create(1, TARIFFS["pro"]), payments.get_or_create(1, TARIFFS["pro"])
    )
    assert first.id == second.id
    assert client.created == 1


async def test_different_users_are_not_serialized_into_one_invoice(db):
    client = SlowCryptoClient()
    payments = CryptoPayments(client, db, bot=None, settings=Settings(bot_token="1:x"))
    a, b = await asyncio.gather(
        payments.get_or_create(1, TARIFFS["pro"]), payments.get_or_create(2, TARIFFS["pro"])
    )
    assert a.id != b.id
    assert client.created == 2


class InvoicesClient:
    """Crypto Pay отдаёт счета с заданным статусом и payload и считает запросы."""

    def __init__(self, status: str = "active", payload: str | None = None) -> None:
        self.status = status
        self.payload = payload
        self.calls = 0

    async def get_invoices(self, invoice_ids):
        self.calls += 1
        return [
            Invoice(i, self.status, "", self.payload or f"order:{i - 500}") for i in invoice_ids
        ]


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_) -> None:
        self.sent.append((chat_id, text))


async def pending_order(db: Database):
    """Заказ с выставленным счётом: номер счёта = 500 + номер заказа."""
    order = await db.create_crypto_order(1, "pro")
    return await db.set_crypto_invoice(
        order.id, 500 + order.id, "https://t.me/CryptoBot", utcnow() + timedelta(minutes=30)
    )


async def test_check_button_flood_asks_crypto_pay_once(db):
    """Каждое нажатие «Проверить оплату» шло запросом в Crypto Pay: флуд кнопкой упирал
    токен в лимит, и вставала проверка оплат у всех клиентов."""
    client = InvoicesClient()
    payments = CryptoPayments(
        client, db, bot=None, settings=Settings(_env_file=None, bot_token="1:x")
    )
    order = await pending_order(db)
    for _ in range(5):
        assert await payments.check(order) == "pending"
    assert client.calls == 1
    payments._checked[order.id] -= crypto_service.CHECK_INTERVAL  # прошёл интервал
    await payments.check(order)
    assert client.calls == 2


async def test_paid_invoice_of_other_order_is_not_counted(db):
    """Оплаченный счёт засчитывается только заказу, для которого выставлен (payload order:N)."""
    bot = FakeBot()
    client = InvoicesClient(status="paid", payload="order:999")
    payments = CryptoPayments(client, db, bot, settings=Settings(_env_file=None, bot_token="1:x"))
    order = await pending_order(db)
    await payments.sync([order])
    assert (await db.get_crypto_order(order.id)).status == "pending"
    assert await db.get_subscription(1) is None
    assert bot.sent == []

    client.payload = None  # payload совпадает с заказом
    await payments.sync([order])
    assert (await db.get_crypto_order(order.id)).status == "approved"
