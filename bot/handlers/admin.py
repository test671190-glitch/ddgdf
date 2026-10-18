"""Команды администратора. Доступны только ID из ADMIN_IDS."""

from html import escape

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import texts
from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import CRYPTO_PREFIX, Database, KeyConflictError
from bot.filters.admin import IsAdmin
from bot.keyboards.inline import profile_keyboard
from bot.services.access import deliver_activation, revoke_notice, shown_key
from bot.utils import fmt_dt, notify_admins, safe_send

router = Router(name="admin")
router.message.filter(IsAdmin())

TARIFF_CODES = ", ".join(TARIFFS)

ADMIN_HELP = (
    "🛠 <b>Админка</b>\n\n"
    "/stats — пользователи, подписки, выручка\n"
    "/keys — остаток ключей в пуле\n"
    f"/addkeys <i>тариф</i> — добавить ключи (каждый с новой строки). Тарифы: {TARIFF_CODES}\n"
    "/givekey <i>user_id</i> <i>ключ</i> — выдать ключ вручную\n"
    "/grant <i>user_id</i> <i>тариф</i> [<i>дней</i>] — начислить подписку без оплаты\n"
    "/user <i>user_id</i> — информация о пользователе\n"
    "/refund <i>charge_id</i> — вернуть Stars и отнять дни (для крипты: crypto:номер)\n"
    "/revoke_list — ключи, которые надо деактивировать в панели провайдера\n"
    "/revoked <i>ключ</i> — отметить ключ деактивированным"
)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await message.answer(ADMIN_HELP)


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database) -> None:
    s = await db.stats()
    active = (
        "\n".join(
            f"  {TARIFFS[c].title if c in TARIFFS else c}: {n}" for c, n in s["active"].items()
        )
        or "  нет"
    )
    crypto_usd = sum(TARIFFS[c].price_usd * n for c, n in s["crypto"].items() if c in TARIFFS)
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {s['users']}\n"
        f"Активные подписки: {sum(s['active'].values())}\n{active}\n"
        f"Оплаты Stars: {s['payments']} на ⭐ {s['stars']}\n"
        f"Оплаты криптой: {sum(s['crypto'].values())} на ${crypto_usd}"
    )


@router.message(Command("keys"))
async def cmd_keys(message: Message, db: Database) -> None:
    stats = await db.key_stats()
    lines = ["🔑 <b>Пул ключей</b>", ""]
    for code, tariff in TARIFFS.items():
        st = stats.get(code, {})
        lines.append(
            f"<b>{tariff.title}</b>: свободно {st.get('free', 0)}, выдано {st.get('active', 0)}, "
            f"ждут отключения {st.get('revoke', 0)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("addkeys"))
async def cmd_addkeys(
    message: Message, command: CommandObject, bot: Bot, db: Database, settings: Settings
) -> None:
    parts = (command.args or "").split()
    if len(parts) < 2 or parts[0] not in TARIFFS:
        await message.answer(
            f"Формат:\n<code>/addkeys pro\nключ1\nключ2</code>\n\nТарифы: {TARIFF_CODES}"
        )
        return
    tariff, keys = parts[0], parts[1:]
    added = await db.add_keys(tariff, keys)
    # Кто оплатил, пока пул был пуст, получает ключ сразу, без ручного /givekey
    waiting = await db.assign_waiting(tariff)
    title = TARIFFS[tariff].title
    for user_id, upstream_key in waiting:
        visible = await shown_key(db, settings, user_id, upstream_key)
        await safe_send(
            bot,
            user_id,
            texts.key_issued(title, visible, settings),
            reply_markup=profile_keyboard(
                has_subscription=True, key=visible, can_rotate=settings.uses_gateway
            ),
        )
    skipped = len(keys) - added
    text = f"✅ Добавлено ключей для {title}: {added}"
    if skipped:
        text += f"\nПропущено дублей: {skipped}"
    if waiting:
        text += f"\nСразу выдано ожидавшим оплатившим: {len(waiting)}"
    await message.answer(text)
    # Сообщение содержит ключи — удаляем его из чата
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


@router.message(Command("givekey"))
async def cmd_givekey(
    message: Message, command: CommandObject, bot: Bot, db: Database, settings: Settings
) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdecimal():
        await message.answer("Формат: <code>/givekey user_id ключ</code>")
        return
    user_id, key = int(parts[0]), parts[1]
    try:
        result = await db.assign_key(user_id, key)
    except KeyConflictError as e:
        await message.answer(f"❌ {escape(str(e))}")
        return
    if result is None:
        await message.answer("❌ У пользователя нет активной подписки. Сначала /grant или оплата.")
        return
    sub, revoked = result
    tariff = TARIFFS.get(sub.tariff)
    title = tariff.title if tariff else sub.tariff
    visible = await shown_key(db, settings, user_id, key)
    delivered = await safe_send(
        bot,
        user_id,
        texts.key_issued(title, visible, settings),
        reply_markup=profile_keyboard(
            has_subscription=True, key=visible, can_rotate=settings.uses_gateway
        ),
    )
    await message.answer(
        f"✅ Ключ привязан к <code>{user_id}</code> ({title})."
        + ("" if delivered else "\n⚠️ Пользователю не удалось отправить сообщение.")
    )
    if revoked:
        await notify_admins(bot, settings, revoke_notice(user_id, revoked, "замена ключа"))


@router.message(Command("grant"))
async def cmd_grant(
    message: Message, command: CommandObject, bot: Bot, db: Database, settings: Settings
) -> None:
    parts = (command.args or "").split()
    if len(parts) not in (2, 3) or not parts[0].isdecimal() or parts[1] not in TARIFFS:
        await message.answer(
            f"Формат: <code>/grant user_id тариф [дней]</code>\nТарифы: {TARIFF_CODES}"
        )
        return
    # isdecimal, а не isdigit: «²» — digit, но int() его не разбирает
    if len(parts) == 3 and (not parts[2].isdecimal() or int(parts[2]) == 0):
        await message.answer("Количество дней должно быть положительным числом")
        return
    user_id, tariff = int(parts[0]), parts[1]
    days = int(parts[2]) if len(parts) == 3 else settings.subscription_days
    act = await db.grant(user_id, tariff, days)
    await deliver_activation(bot, db, settings, user_id, act, paid=False)
    await message.answer(
        f"✅ {TARIFFS[tariff].title} для <code>{user_id}</code> до {fmt_dt(act.expires_at)}"
        + ("" if act.key else "\n⚠️ Ключ не выдан — пул пуст.")
    )


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject, db: Database) -> None:
    arg = (command.args or "").strip()
    if not arg.isdecimal():
        await message.answer("Формат: <code>/user user_id</code>")
        return
    user_id = int(arg)
    user = await db.get_user(user_id)
    sub = await db.get_subscription(user_id)
    key = await db.get_active_key(user_id)
    lines = [f"👤 <code>{user_id}</code>"]
    if user:
        name = escape(user["full_name"] or "")
        uname = f" @{escape(user['username'])}" if user["username"] else ""
        lines.append(f"{name}{uname}, с {user['created_at'][:10]}")
    else:
        lines.append("В базе нет (не запускал бота)")
    if sub:
        status = "активна" if sub.is_active else "истекла"
        lines.append(f"Подписка: {sub.tariff}, {status}, до {fmt_dt(sub.expires_at)}")
    else:
        lines.append("Подписки нет")
    lines.append(f"Ключ провайдера: <code>{escape(key.key)}</code>" if key else "Ключа нет")
    client_key = await db.get_client_key(user_id)
    if client_key:
        lines.append(f"Ключ клиента: <code>{escape(client_key)}</code>")
    await message.answer("\n".join(lines))


@router.message(Command("refund"))
async def cmd_refund(
    message: Message, command: CommandObject, bot: Bot, db: Database, settings: Settings
) -> None:
    charge_id = (command.args or "").strip()
    if not charge_id:
        await message.answer(
            "Формат: <code>/refund charge_id</code> (есть в уведомлении об оплате)"
        )
        return
    payment = await db.get_payment(charge_id)
    if payment is None:
        await message.answer("❌ Платёж не найден")
        return
    if payment.refunded:
        await message.answer("Этот платёж уже возвращён")
        return
    is_crypto = charge_id.startswith(CRYPTO_PREFIX)
    if not is_crypto:
        try:
            await bot.refund_star_payment(
                user_id=payment.user_id, telegram_payment_charge_id=charge_id
            )
        except TelegramBadRequest as e:
            await message.answer(f"❌ Telegram отказал в возврате: {escape(e.message)}")
            return
    refund = await db.refund(charge_id, settings.subscription_days)
    amount = f"${TARIFFS[payment.tariff].price_usd}" if is_crypto else f"⭐ {payment.stars}"
    await message.answer(
        f"✅ Возврат {amount} пользователю {payment.user_id} оформлен, дни списаны."
        + ("\n⚠️ Крипту верните вручную через @CryptoBot." if is_crypto else "")
    )
    await safe_send(
        bot, payment.user_id, f"↩️ Платёж возвращён: {amount}. Подписка скорректирована."
    )
    if refund and refund.revoked_key:
        await notify_admins(
            bot, settings, revoke_notice(payment.user_id, refund.revoked_key, "возврат")
        )


@router.message(Command("revoke_list"))
async def cmd_revoke_list(message: Message, db: Database) -> None:
    keys = await db.keys_to_revoke()
    if not keys:
        await message.answer("Нет ключей для отключения 👌")
        return
    lines = ["🔒 <b>Деактивируйте в панели провайдера</b>, затем /revoked ключ:", ""]
    lines += [f"<code>{escape(k.key)}</code> — {k.tariff}, user {k.user_id}" for k in keys[:50]]
    if len(keys) > 50:
        lines.append(f"…и ещё {len(keys) - 50}")
    await message.answer("\n".join(lines))


@router.message(Command("revoked"))
async def cmd_revoked(message: Message, command: CommandObject, db: Database) -> None:
    key = (command.args or "").strip()
    if not key:
        await message.answer("Формат: <code>/revoked ключ</code>")
        return
    ok = await db.mark_revoked_done(key)
    await message.answer("✅ Отмечено" if ok else "❌ Ключ не найден в списке на отключение")
