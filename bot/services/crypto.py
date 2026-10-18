"""Оплата криптой: счёт Crypto Pay на каждый заказ и автоматическая проверка оплаты.

Используется и ботом, и сайтом. Начисление защищено от дублей на уровне базы
(approve_crypto_order меняет статус атомарно), поэтому проверять заказ можно
из нескольких мест одновременно.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from aiogram import Bot

from bot.catalog import SHOP_NAME, TARIFFS, Tariff
from bot.config import Settings
from bot.db import CryptoOrder, Database, utcnow
from bot.services.access import deliver_activation
from bot.services.cryptopay import CryptoPayClient, CryptoPayError
from bot.utils import notify_admins

logger = logging.getLogger(__name__)


class CryptoPayments:
    def __init__(self, client: CryptoPayClient, db: Database, bot: Bot, settings: Settings) -> None:
        self.client = client
        self.db = db
        self.bot = bot
        self.settings = settings
        # Двойное нажатие «Оплатить криптой» шлёт два запроса подряд: без блокировки оба
        # не находят открытого счёта и выставляют два
        self._locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_create(self, user_id: int, tariff: Tariff) -> CryptoOrder:
        """Возвращает действующий счёт пользователя на этот тариф или создаёт новый."""
        async with self._locks[(user_id, tariff.code)]:
            return await self._get_or_create(user_id, tariff)

    async def _get_or_create(self, user_id: int, tariff: Tariff) -> CryptoOrder:
        order = await self.db.open_crypto_order(user_id, tariff.code)
        if order:
            return order
        order = await self.db.create_crypto_order(user_id, tariff.code)
        minutes = self.settings.crypto_invoice_minutes
        try:
            invoice = await self.client.create_invoice(
                amount_usd=tariff.price_usd,
                description=(
                    f"{SHOP_NAME}: тариф {tariff.title} на {self.settings.subscription_days} дней"
                ),
                payload=f"order:{order.id}",
                expires_in=minutes * 60,
            )
        except CryptoPayError:
            await self.db.expire_crypto_order(order.id)
            raise
        return await self.db.set_crypto_invoice(
            order.id, invoice.invoice_id, invoice.pay_url, utcnow() + timedelta(minutes=minutes)
        )

    async def check(self, order: CryptoOrder) -> str:
        """Сверяет один заказ с Crypto Pay и возвращает его актуальный статус."""
        if order.status == "pending" and order.invoice_id:
            await self.sync([order])
        fresh = await self.db.get_crypto_order(order.id)
        return fresh.status if fresh else "expired"

    async def sync(self, orders: list[CryptoOrder] | None = None) -> None:
        if orders is None:
            orders = await self.db.pending_crypto_orders()
        by_invoice = {o.invoice_id: o for o in orders if o.invoice_id}
        if not by_invoice:
            return
        for invoice in await self.client.get_invoices(list(by_invoice)):
            order = by_invoice.get(invoice.invoice_id)
            if order is None:
                continue
            if invoice.status == "paid":
                await self._complete(order)
            elif invoice.status == "expired":
                await self.db.expire_crypto_order(order.id)

    async def _complete(self, order: CryptoOrder) -> None:
        result = await self.db.approve_crypto_order(order.id, self.settings.subscription_days)
        if result is None:
            return
        order, act = result
        await deliver_activation(self.bot, self.db, self.settings, order.user_id, act, paid=True)
        tariff = TARIFFS[order.tariff]
        await notify_admins(
            self.bot,
            self.settings,
            f"💰 Оплата криптой: пользователь <code>{order.user_id}</code>, "
            f"тариф <b>{tariff.title}</b>, ${tariff.price_usd}\n"
            f"Заказ #{order.id}, счёт Crypto Pay {order.invoice_id}\n"
            f"Возврат: <code>/refund crypto:{order.id}</code>",
        )


async def crypto_loop(payments: CryptoPayments, interval: int = 15) -> None:
    while True:
        try:
            await payments.sync()
        except CryptoPayError as e:
            logger.warning("Crypto Pay: %s", e)
        except Exception:
            logger.exception("Ошибка проверки крипто-платежей")
        await asyncio.sleep(interval)
