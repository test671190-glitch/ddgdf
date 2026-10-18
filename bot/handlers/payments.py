"""Тарифы и оплата: Telegram Stars и крипта через Crypto Pay (обе проверяются автоматически)."""

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot import texts
from bot.catalog import SHOP_NAME, TARIFFS, TARIFFS_ON_SALE, Tariff
from bot.config import Settings
from bot.db import Database
from bot.keyboards.inline import (
    BuyTariff,
    CryptoCheck,
    CryptoPay,
    Menu,
    TariffPage,
    crypto_pay_keyboard,
    renew_keyboard,
    tariff_keyboard,
    tariffs_keyboard,
)
from bot.services.access import deliver_activation
from bot.services.crypto import CryptoPayments
from bot.services.cryptopay import CryptoPayError
from bot.utils import notify_admins, safe_answer, show, user_label

logger = logging.getLogger(__name__)
router = Router(name="payments")

PAYLOAD_PREFIX = "tariff:"
CURRENCY = "XTR"  # Telegram Stars


def tariff_from_payload(payload: str) -> Tariff | None:
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    return TARIFFS_ON_SALE.get(payload.removeprefix(PAYLOAD_PREFIX))


@router.message(Command("tariffs"))
async def cmd_tariffs(message: Message, settings: Settings) -> None:
    await message.answer(texts.tariffs_overview(settings), reply_markup=tariffs_keyboard())


@router.callback_query(Menu.filter(F.section == "tariffs"))
async def cb_tariffs(callback: CallbackQuery, settings: Settings) -> None:
    await show(callback, texts.tariffs_overview(settings), tariffs_keyboard())


async def render_tariff(
    user_id: int,
    tariff: Tariff,
    db: Database,
    settings: Settings,
    crypto: CryptoPayments | None,
) -> tuple[str, InlineKeyboardMarkup]:
    current = await db.get_subscription(user_id)
    return (
        texts.tariff_details(tariff, settings, current),
        tariff_keyboard(tariff, tariff.stars(settings.stars_per_usd), crypto is not None),
    )


@router.callback_query(TariffPage.filter(F.code.in_(TARIFFS_ON_SALE)))
async def cb_tariff(
    callback: CallbackQuery,
    callback_data: TariffPage,
    db: Database,
    settings: Settings,
    crypto: CryptoPayments | None,
) -> None:
    text, markup = await render_tariff(
        callback.from_user.id, TARIFFS_ON_SALE[callback_data.code], db, settings, crypto
    )
    await show(callback, text, markup)


@router.callback_query(BuyTariff.filter(F.code.in_(TARIFFS_ON_SALE)))
async def cb_buy(
    callback: CallbackQuery, callback_data: BuyTariff, bot: Bot, settings: Settings
) -> None:
    tariff = TARIFFS_ON_SALE[callback_data.code]
    stars = tariff.stars(settings.stars_per_usd)
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{SHOP_NAME}: {tariff.title}",
        description=texts.invoice_description(tariff, settings),
        payload=PAYLOAD_PREFIX + tariff.code,
        currency=CURRENCY,
        prices=[
            LabeledPrice(label=f"{tariff.title}, {settings.subscription_days} дней", amount=stars)
        ],
    )
    await safe_answer(callback)


@router.callback_query(CryptoPay.filter(F.code.in_(TARIFFS_ON_SALE)))
async def cb_crypto_pay(
    callback: CallbackQuery, callback_data: CryptoPay, crypto: CryptoPayments | None
) -> None:
    tariff = TARIFFS_ON_SALE[callback_data.code]
    if crypto is None:
        await safe_answer(callback, "Оплата криптой сейчас недоступна", show_alert=True)
        return
    try:
        order = await crypto.get_or_create(callback.from_user.id, tariff)
    except CryptoPayError as e:
        logger.error("Не удалось создать счёт: %s", e)
        await safe_answer(
            callback, "Не получилось создать счёт. Попробуйте через минуту.", show_alert=True
        )
        return
    await show(
        callback,
        texts.crypto_invoice(tariff, order),
        crypto_pay_keyboard(tariff, order.id, order.pay_url),
    )


@router.callback_query(CryptoCheck.filter())
async def cb_crypto_check(
    callback: CallbackQuery,
    callback_data: CryptoCheck,
    db: Database,
    crypto: CryptoPayments | None,
) -> None:
    order = await db.get_crypto_order(callback_data.order_id)
    if order is None or order.user_id != callback.from_user.id or crypto is None:
        await safe_answer(callback, "Заказ не найден", show_alert=True)
        return
    try:
        status = await crypto.check(order)
    except CryptoPayError:
        await safe_answer(callback, "Crypto Pay не отвечает, попробуйте позже", show_alert=True)
        return
    if status == "approved":
        await safe_answer(callback, "Оплата получена, ключ уже в чате")
    elif status == "expired":
        tariff = TARIFFS[order.tariff]
        await show(callback, texts.crypto_expired(order), renew_keyboard(tariff.code))
    else:
        await safe_answer(
            callback, "Оплата пока не поступила. Бот проверяет её автоматически.", show_alert=True
        )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, settings: Settings) -> None:
    tariff = tariff_from_payload(query.invoice_payload)
    if (
        tariff is None
        or query.currency != CURRENCY
        or query.total_amount != tariff.stars(settings.stars_per_usd)
    ):
        await query.answer(
            ok=False,
            error_message="Цена или тариф изменились. Откройте /tariffs и оформите оплату заново.",
        )
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message, bot: Bot, db: Database, settings: Settings
) -> None:
    payment = message.successful_payment
    user = message.from_user
    tariff = tariff_from_payload(payment.invoice_payload)
    if tariff is None:
        logger.error("Неизвестный payload платежа: %s", payment.invoice_payload)
        await notify_admins(
            bot,
            settings,
            f"❗️ Платёж с неизвестным тарифом от <code>{user.id}</code>: "
            f"{escape(payment.invoice_payload)}, ⭐ {payment.total_amount}, "
            f"charge_id <code>{escape(payment.telegram_payment_charge_id)}</code>",
        )
        await message.answer(
            "Оплата получена, но тариф не распознан. Мы уже разбираемся — напишите в /paysupport."
        )
        return

    act = await db.activate_paid(
        user.id,
        tariff.code,
        settings.subscription_days,
        payment.total_amount,
        payment.telegram_payment_charge_id,
    )
    if act is None:
        logger.warning("Повторный платёж %s", payment.telegram_payment_charge_id)
        return

    await deliver_activation(bot, db, settings, user.id, act, paid=True)
    await notify_admins(
        bot,
        settings,
        f"💰 Оплата: {user_label(user)} (<code>{user.id}</code>), тариф <b>{tariff.title}</b>, "
        f"⭐ {payment.total_amount}\n"
        f"charge_id: <code>{escape(payment.telegram_payment_charge_id)}</code>",
    )
