"""Фоновая задача: напоминания о продлении и закрытие истёкших подписок."""

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot

from bot import texts
from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import Database
from bot.keyboards.inline import renew_keyboard
from bot.services.access import deliver_waiting, revoke_notice
from bot.utils import notify_admins, safe_send

logger = logging.getLogger(__name__)


async def check_subscriptions(bot: Bot, db: Database, settings: Settings) -> None:
    for sub in await db.due_reminders(timedelta(days=settings.remind_days_before)):
        await safe_send(
            bot, sub.user_id, texts.reminder(sub), reply_markup=renew_keyboard(sub.tariff)
        )
        await db.mark_reminded(sub.user_id, sub.expires_at)

    for sub, key in await db.expire_due():
        await safe_send(
            bot, sub.user_id, texts.expired(sub), reply_markup=renew_keyboard(sub.tariff)
        )
        if key:
            await notify_admins(bot, settings, revoke_notice(sub.user_id, key, "подписка истекла"))

    # Ключи, вернувшиеся в пул (окончание подписки, смена тарифа), сразу получают
    # те, кто оплатил при пустом пуле
    for code, tariff in TARIFFS.items():
        delivered = await deliver_waiting(bot, db, settings, code)
        if delivered:
            # админу пришёл алерт «пул пуст» — сообщаем, что вручную выдавать уже не нужно
            await notify_admins(
                bot,
                settings,
                f"✅ Освободившиеся ключи {tariff.title} выданы ожидавшим: {delivered}. "
                "/givekey не нужен.",
            )


async def expiry_loop(bot: Bot, db: Database, settings: Settings, interval: int = 600) -> None:
    while True:
        try:
            await check_subscriptions(bot, db, settings)
        except Exception:
            logger.exception("Ошибка при проверке подписок")
        await asyncio.sleep(interval)
