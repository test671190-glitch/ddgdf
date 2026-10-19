"""Команды администратора. Доступны только ID из ADMIN_IDS."""

from html import escape

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import texts
from bot.catalog import TARIFFS
from bot.config import Settings
from bot.db import Database, KeyConflictError
from bot.filters.admin import IsAdmin
from bot.keyboards.inline import profile_keyboard
from bot.services.access import deliver_activation, deliver_waiting, revoke_notice, shown_key
from bot.utils import fmt_dt, mask_key, notify_admins, safe_send

router = Router(name="admin")
router.message.filter(IsAdmin())

TARIFF_CODES = ", ".join(TARIFFS)
MAX_GRANT_DAYS = 3650
REVOKE_LIST_CHARS = 3800  # с запасом до лимита Telegram 4096 на строку «…и ещё N»

ADMIN_HELP = (
    "🛠 <b>Админка</b>\n\n"
    "/stats — пользователи, подписки, выручка\n"
    "/keys — остаток ключей в пуле\n"
    f"/addkeys <i>тариф</i> — добавить ключи (каждый с новой строки). Тарифы: {TARIFF_CODES}\n"
    "/givekey <i>user_id</i> <i>ключ</i> — выдать ключ вручную\n"
    "/grant <i>user_id</i> <i>тариф</i> [<i>дней</i>] — начислить подписку без оплаты "
    "(другой тариф поверх действующего — только с <i>force</i> в конце)\n"
    "/user <i>user_id</i> — информация о пользователе\n"
    "/revoke_list — ключи, которые надо деактивировать в панели провайдера\n"
    "/revoked <i>ключ</i> — отметить ключ деактивированным"
)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await message.answer(ADMIN_HELP)


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database, settings: Settings) -> None:
    s = await db.stats(exclude_users=settings.admin_ids)
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
    if parts:
        # Убираем ключи из чата сразу, а не после выдачи ожидающим: она может занять время,
        # а при опечатке в тарифе (или ключе без тарифа) ответа «готово» не будет вовсе
        await _delete_secret(message)
    if len(parts) < 2 or parts[0] not in TARIFFS:
        await message.answer(
            f"Формат:\n<code>/addkeys pro\nключ1\nключ2</code>\n\nТарифы: {TARIFF_CODES}"
        )
        return
    tariff, keys = parts[0], parts[1:]
    added = await db.add_keys(tariff, keys)
    # Кто оплатил, пока пул был пуст, получает ключ сразу, без ручного /givekey
    delivered = await deliver_waiting(bot, db, settings, tariff)
    title = TARIFFS[tariff].title
    skipped = len(keys) - added
    text = f"✅ Добавлено ключей для {title}: {added}"
    if skipped:
        text += f"\nПропущено дублей: {skipped}"
    if delivered:
        text += f"\nСразу выдано ожидавшим оплатившим: {delivered}"
    await message.answer(text)


async def _delete_secret(message: Message) -> None:
    """Сообщение содержит ключи провайдера — убираем его из чата."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


def _title(code: str) -> str:
    return TARIFFS[code].title if code in TARIFFS else code


def _parse_user_id(value: str) -> int | None:
    # isdecimal, а не isdigit: «²» — digit, но int() его не разбирает. Длину проверяем до int():
    # SQLite хранит не больше 64 бит, большее число роняет запрос без ответа админу
    if not value.isdecimal() or len(value) > 19:
        return None
    user_id = int(value)
    return user_id if user_id < 2**63 else None


@router.message(Command("givekey"))
async def cmd_givekey(
    message: Message, command: CommandObject, bot: Bot, db: Database, settings: Settings
) -> None:
    parts = (command.args or "").split()
    if parts:
        await _delete_secret(message)
    user_id = _parse_user_id(parts[0]) if len(parts) == 2 else None
    if user_id is None:
        await message.answer("Формат: <code>/givekey user_id ключ</code>")
        return
    key = parts[1]
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
    force = bool(parts) and parts[-1].lower() == "force"
    if force:
        parts = parts[:-1]
    user_id = _parse_user_id(parts[0]) if len(parts) in (2, 3) else None
    if user_id is None or parts[1] not in TARIFFS:
        await message.answer(
            f"Формат: <code>/grant user_id тариф [дней]</code>\nТарифы: {TARIFF_CODES}"
        )
        return
    if len(parts) == 3 and (
        not parts[2].isdecimal() or len(parts[2]) > 5 or not 0 < int(parts[2]) <= MAX_GRANT_DAYS
    ):
        await message.answer(
            f"Количество дней должно быть положительным числом, не больше {MAX_GRANT_DAYS}"
        )
        return
    tariff = parts[1]
    days = int(parts[2]) if len(parts) == 3 else settings.subscription_days
    sub = await db.get_subscription(user_id)
    if sub and sub.is_active and sub.tariff != tariff and not force:
        # Выдача другого тарифа сжигает оплаченный остаток: например, компенсация «3 дня Pro»
        # клиенту с 57 оплаченными днями Unlimited оставила бы ему 3 дня
        current = TARIFFS[sub.tariff].title if sub.tariff in TARIFFS else sub.tariff
        await message.answer(
            f"⚠️ У клиента {current} до {fmt_dt(sub.expires_at)}. Выдача тарифа "
            f"{TARIFFS[tariff].title} сожжёт остаток: дни прежнего тарифа не переносятся.\n\n"
            f"Продлить текущий: <code>/grant {user_id} {sub.tariff} {days}</code>\n"
            f"Всё равно сменить: <code>/grant {user_id} {tariff} {days} force</code>"
        )
        return
    act = await db.grant(user_id, tariff, days)
    delivered = await deliver_activation(bot, db, settings, user_id, act, paid=False)
    text = f"✅ {TARIFFS[tariff].title} для <code>{user_id}</code> до {fmt_dt(act.expires_at)}"
    if await db.get_user(user_id) is None:
        text += "\n⚠️ Такого пользователя нет в базе — он не запускал бота. Проверьте ID."
    elif not delivered:
        text += "\n⚠️ Клиенту не удалось отправить сообщение (возможно, он заблокировал бота)."
    await message.answer(text)


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject, db: Database) -> None:
    user_id = _parse_user_id((command.args or "").strip())
    if user_id is None:
        await message.answer("Формат: <code>/user user_id</code>")
        return
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
        lines.append(f"Подписка: {_title(sub.tariff)}, {status}, до {fmt_dt(sub.expires_at)}")
    else:
        lines.append("Подписки нет")
    # Ключи — маской: карточку пользователя смотрят при разборе обращений, скриншотят
    # и пересылают. Полные ключи на отключение — в /revoke_list
    if key:
        # после смены тарифа при пустом пуле ключ может быть ещё от прежнего тарифа
        masked = escape(mask_key(key.key))
        lines.append(f"Ключ провайдера ({_title(key.tariff)}): <code>{masked}</code>")
    else:
        lines.append("Ключа нет")
    client_key = await db.get_client_key(user_id)
    if client_key:
        lines.append(f"Ключ клиента: <code>{escape(mask_key(client_key))}</code>")
    await message.answer("\n".join(lines))


@router.message(Command("refund"))
async def cmd_refund(message: Message) -> None:
    # в старых уведомлениях об оплате осталась кликабельная /refund — отвечаем понятно,
    # а не «Не понял запрос»
    await message.answer("Возвраты отключены.")


@router.message(Command("revoke_list"))
async def cmd_revoke_list(message: Message, db: Database) -> None:
    keys = await db.keys_to_revoke()
    if not keys:
        await message.answer("Нет ключей для отключения 👌")
        return
    lines = ["🔒 <b>Деактивируйте в панели провайдера</b>, затем /revoked ключ:", ""]
    size = sum(len(line) + 1 for line in lines)
    shown = 0
    for k in keys:
        line = f"<code>{escape(k.key)}</code> — {k.tariff}, user {k.user_id}"
        # Telegram не принимает сообщения длиннее 4096 символов
        if shown == 50 or size + len(line) + 1 > REVOKE_LIST_CHARS:
            break
        lines.append(line)
        size += len(line) + 1
        shown += 1
    if len(keys) > shown:
        lines.append(f"…и ещё {len(keys) - shown}")
    await message.answer("\n".join(lines))


@router.message(Command("revoked"))
async def cmd_revoked(message: Message, command: CommandObject, db: Database) -> None:
    key = (command.args or "").strip()
    if not key:
        await message.answer("Формат: <code>/revoked ключ</code>")
        return
    ok = await db.mark_revoked_done(key)
    await message.answer("✅ Отмечено" if ok else "❌ Ключ не найден в списке на отключение")
