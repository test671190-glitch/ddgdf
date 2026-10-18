"""Оплата криптой: создание счетов без дублей."""

import asyncio

import pytest

from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import Database
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
