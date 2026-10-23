"""Выдача доступа после оплаты или ручного начисления."""

from html import escape

from aiogram import Bot

from bot import texts
from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import Activation, Database
from bot.keyboards.inline import profile_keyboard
from bot.utils import notify_admins, safe_send


def revoke_notice(user_id: int, key: str, reason: str) -> str:
    return (
        f"🔒 Деактивируйте ключ в панели провайдера ({reason}), "
        f"пользователь <code>{user_id}</code>:\n"
        f"<code>{escape(key)}</code>\n"
        f"После этого: <code>/revoked {escape(key)}</code>"
    )


async def shown_key(
    db: Database, settings: Settings, user_id: int, upstream_key: str | None
) -> str | None:
    """Ключ, который видит клиент.

    С собственным эндпоинтом это наш ключ sk-us-..., ключ провайдера остаётся скрытым.
    Пока ключ провайдера не выдан (пул пуст), показывать нечего — доступ ещё готовится.
    """
    if upstream_key is None:
        return None
    if settings.uses_gateway:
        return await db.get_or_create_client_key(user_id)
    return upstream_key


async def deliver_activation(
    bot: Bot, db: Database, settings: Settings, user_id: int, act: Activation, *, paid: bool
) -> bool:
    """Сообщает клиенту о доступе. False — сообщение не доставлено (бот заблокирован и т. п.)."""
    tariff = TARIFFS[act.tariff]
    key = await shown_key(db, settings, user_id, act.key)
    previous = None
    if act.key is None:
        # смена тарифа при пустом пуле: ключ прежнего тарифа продолжает работать
        current = await db.get_active_key(user_id)
        previous = await shown_key(db, settings, user_id, current.key if current else None)
    visible = key or previous
    delivered = await safe_send(
        bot,
        user_id,
        texts.activation(tariff, act, settings, paid=paid, key=key, previous_key=previous),
        reply_markup=profile_keyboard(
            has_subscription=True,
            key=visible,
            can_rotate=settings.uses_gateway and visible is not None,
        ),
    )
    if act.key is None:
        await notify_admins(
            bot,
            settings,
            f"⚠️ Нет свободных ключей для тарифа <b>{tariff.title}</b>.\n"
            f"Пользователь <code>{user_id}</code> ждёт ключ. Пополните пул:\n"
            f"<code>/addkeys {tariff.code}</code> + ключи с новой строки — ключ выдастся "
            f"автоматически, <code>/givekey</code> после этого не нужен.",
        )
    if act.revoked_key:
        await notify_admins(bot, settings, revoke_notice(user_id, act.revoked_key, "смена тарифа"))
    return delivered


async def deliver_waiting(bot: Bot, db: Database, settings: Settings, tariff: str) -> int:
    """Отдаёт свободные ключи тарифа тем, кто оплатил его при пустом пуле, и шлёт им ключ.

    Ключи освобождаются при пополнении пула, окончании подписок и смене тарифа.
    Возвращает, скольким клиентам выдан ключ.
    """
    waiting = await db.assign_waiting(tariff)
    title = TARIFFS[tariff].title if tariff in TARIFFS else tariff
    for user_id, upstream_key, revoked in waiting:
        visible = await shown_key(db, settings, user_id, upstream_key)
        await safe_send(
            bot,
            user_id,
            texts.key_issued(title, visible, settings),
            reply_markup=profile_keyboard(
                has_subscription=True, key=visible, can_rotate=settings.uses_gateway
            ),
        )
        if revoked:
            await notify_admins(bot, settings, revoke_notice(user_id, revoked, "смена тарифа"))
    return len(waiting)
