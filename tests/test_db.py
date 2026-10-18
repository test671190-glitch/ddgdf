from datetime import timedelta

import pytest

from bot.db import Database, KeyConflictError, _iso, utcnow


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def test_payment_assigns_key_from_pool(db):
    await db.add_keys("pro", ["k-pro-1", "k-pro-2"])
    act = await db.activate_paid(1, "pro", 30, 2541, "charge-1")
    assert act.key == "k-pro-1"
    assert not act.extended
    assert (await db.get_subscription(1)).is_active


async def test_duplicate_charge_is_ignored(db):
    await db.add_keys("pro", ["k1", "k2"])
    assert await db.activate_paid(1, "pro", 30, 1, "charge-1") is not None
    assert await db.activate_paid(1, "pro", 30, 1, "charge-1") is None
    assert (await db.key_stats())["pro"] == {"free": 1, "active": 1}


async def test_renewal_extends_and_keeps_key(db):
    await db.add_keys("pro", ["k1", "k2"])
    first = await db.activate_paid(1, "pro", 30, 1, "c1")
    second = await db.activate_paid(1, "pro", 30, 1, "c2")
    assert second.extended
    assert second.key == first.key
    assert second.expires_at - first.expires_at == timedelta(days=30)


async def test_tariff_change_revokes_old_key(db):
    await db.add_keys("start", ["k-start"])
    await db.add_keys("unlimited", ["k-unl"])
    await db.activate_paid(1, "start", 30, 1, "c1")
    act = await db.activate_paid(1, "unlimited", 30, 1, "c2")
    assert act.key == "k-unl"
    assert act.revoked_key == "k-start"
    assert [k.key for k in await db.keys_to_revoke()] == ["k-start"]


async def test_empty_pool_then_manual_key(db):
    act = await db.activate_paid(1, "pro", 30, 1, "c1")
    assert act.key is None
    sub, revoked = await db.assign_key(1, "manual-key")
    assert sub.tariff == "pro" and revoked is None
    assert (await db.get_active_key(1)).key == "manual-key"


async def test_manual_key_needs_active_subscription(db):
    await db.grant(1, "pro", 30)
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    assert await db.assign_key(1, "manual-key") is None


async def test_manual_key_cannot_be_stolen(db):
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 1, "c1")
    await db.grant(2, "pro", 30)
    with pytest.raises(KeyConflictError):
        await db.assign_key(2, "k1")


async def test_expiry_revokes_key(db):
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 1, "c1")
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    expired = await db.expire_due()
    assert [(s.user_id, key) for s, key in expired] == [(1, "k1")]
    assert await db.get_active_key(1) is None
    assert await db.expire_due() == []


async def test_reminder_sent_once(db):
    await db.grant(1, "pro", 2)
    due = await db.due_reminders(timedelta(days=3))
    assert [s.user_id for s in due] == [1]
    await db.mark_reminded(1, due[0].expires_at)
    assert await db.due_reminders(timedelta(days=3)) == []


async def test_renewal_during_reminder_keeps_next_reminder(db):
    """Клиент продлил подписку, пока бот рассылал напоминания. Отметка о старом напоминании
    не должна гасить напоминание о новом периоде."""
    await db.grant(1, "pro", 2)
    (due,) = await db.due_reminders(timedelta(days=3))
    await db.activate_paid(1, "pro", 30, 1, "c1")  # продлил: флаг сброшен, 32 дня
    await db.mark_reminded(1, due.expires_at)  # рассылка отмечает уже устаревший период
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() + timedelta(days=2)),),
    )
    assert [s.user_id for s in await db.due_reminders(timedelta(days=3))] == [1]


async def test_refund_ends_single_period(db):
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    refund = await db.refund("c1", 30)
    assert refund.revoked_key == "k1"
    assert not (await db.get_subscription(1)).is_active
    assert await db.refund("c1", 30) is None


async def expire_now(db, user_id, ago=timedelta(minutes=1)):
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=?", (_iso(utcnow() - ago), user_id)
    )
    await db.conn.commit()


async def test_refund_of_old_payment_keeps_new_period(db):
    """Старый период давно кончился, клиент купил снова. Возврат старого платежа не должен
    отнимать новый оплаченный период."""
    await db.add_keys("pro", ["k1", "k2"])
    await db.activate_paid(1, "pro", 30, 2541, "old")
    await db.conn.execute(
        "UPDATE payments SET created_at=? WHERE charge_id='old'",
        (_iso(utcnow() - timedelta(days=90)),),
    )
    await expire_now(db, 1, timedelta(days=60))
    await db.expire_due()
    await db.activate_paid(1, "pro", 30, 2541, "new")
    refund = await db.refund("old", 30)
    assert not refund.adjusted and refund.revoked_key is None
    assert (await db.get_subscription(1)).is_active
    assert await db.get_active_key(1) is not None


async def test_refund_of_payment_burned_by_tariff_switch(db):
    """Pro → Unlimited (дни Pro сгорели) → снова Pro. Возврат первого Pro не трогает второй."""
    await db.add_keys("pro", ["k1", "k2"])
    await db.add_keys("unlimited", ["u1"])
    await db.activate_paid(1, "pro", 30, 2541, "p1")
    await db.activate_paid(1, "unlimited", 30, 6160, "u1")
    await db.activate_paid(1, "pro", 30, 2541, "p2")
    refund = await db.refund("p1", 30)
    assert not refund.adjusted
    assert (await db.get_subscription(1)).is_active


async def test_refund_of_first_of_two_renewals_takes_its_days(db):
    """Клиент случайно оплатил дважды подряд. Возврат любого из платежей списывает 30 дней."""
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 2541, "p1")
    second = await db.activate_paid(1, "pro", 30, 2541, "p2")
    refund = await db.refund("p1", 30)
    assert refund.adjusted
    sub = await db.get_subscription(1)
    assert sub.expires_at == second.expires_at - timedelta(days=30)
    assert (await db.get_active_key(1)).key == "k1"


async def test_stats(db):
    await db.upsert_user(1, "u", "User")
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    stats = await db.stats()
    assert stats == {
        "users": 1,
        "active": {"pro": 1},
        "payments": 1,
        "stars": 2541,
        "crypto": {},
    }


async def test_crypto_order_flow(db):
    await db.add_keys("unlimited", ["k-unl"])
    order = await db.create_crypto_order(1, "unlimited")
    # Без счёта заказ не считается открытым
    assert await db.open_crypto_order(1, "unlimited") is None
    order = await db.set_crypto_invoice(
        order.id, 555, "https://t.me/pay", utcnow() + timedelta(hours=1)
    )
    assert (await db.open_crypto_order(1, "unlimited")).id == order.id
    assert [o.id for o in await db.pending_crypto_orders()] == [order.id]

    approved, act = await db.approve_crypto_order(order.id, 30)
    assert approved.status == "approved" and act.key == "k-unl"
    # Повторная проверка того же счёта не начисляет второй раз
    assert await db.approve_crypto_order(order.id, 30) is None
    assert (await db.stats())["crypto"] == {"unlimited": 1}
    assert await db.open_crypto_order(1, "unlimited") is None


async def test_crypto_order_expiry(db):
    order = await db.create_crypto_order(1, "pro")
    await db.set_crypto_invoice(order.id, 1, "https://t.me/pay", utcnow() + timedelta(minutes=2))
    # Счёт, который вот-вот истечёт, не переиспользуется
    assert await db.open_crypto_order(1, "pro") is None
    assert await db.expire_crypto_order(order.id)
    assert await db.pending_crypto_orders() == []
    assert await db.get_subscription(1) is None


async def test_web_login_flow(db):
    await db.create_web_login("tok", timedelta(minutes=5))
    assert await db.web_login_status("tok") == ("pending", None)
    assert await db.consume_web_login("tok") is None
    assert await db.confirm_web_login("tok", 42)
    # Подтвердить второй раз (другим аккаунтом) нельзя
    assert not await db.confirm_web_login("tok", 43)
    assert await db.web_login_status("tok") == ("confirmed", 42)
    assert await db.consume_web_login("tok") == 42
    assert await db.consume_web_login("tok") is None
    assert await db.web_login_status("missing") == ("expired", None)


async def test_web_login_double_tap_is_still_confirmed(db):
    """Двойное нажатие «Да, это я» перетирало «Вход подтверждён» текстом «Ссылка устарела»."""
    await db.create_web_login("tok", timedelta(minutes=5))
    assert await db.confirm_web_login("tok", 42)
    assert await db.confirm_web_login("tok", 42)
    assert not await db.confirm_web_login("tok", 43)
    assert await db.web_login_status("tok") == ("confirmed", 42)


async def test_rotating_key_logs_out_web_sessions(db):
    """Ключ перевыпускают, когда доступ мог уйти к чужому, а сайт показывает ключ:
    чужой браузер не должен сразу увидеть новый."""
    await db.create_web_session("s1", 42, timedelta(days=30))
    await db.create_web_session("s2", 7, timedelta(days=30))
    await db.rotate_client_key(42)
    assert await db.web_session_user("s1", timedelta(days=30)) is None
    assert await db.web_session_user("s2", timedelta(days=30)) == 7


async def test_web_login_expires(db):
    await db.create_web_login("old", timedelta(seconds=-1))
    assert await db.web_login_status("old") == ("expired", None)
    assert not await db.confirm_web_login("old", 1)


async def test_web_session_ttl_and_cleanup(db):
    ttl = timedelta(days=30)
    await db.create_web_session("old", 1, ttl)
    await db.conn.execute(
        "UPDATE web_sessions SET created_at=? WHERE id='old'", (_iso(utcnow() - ttl),)
    )
    assert await db.web_session_user("old", ttl) is None
    # Новый вход заодно чистит просроченные сессии
    await db.create_web_session("new", 2, ttl)
    assert await db.web_session_user("new", ttl) == 2
    rows = await db._all("SELECT id FROM web_sessions")
    assert [r["id"] for r in rows] == ["new"]


async def test_hidden_keys_return_to_pool():
    database = Database(":memory:", hide_keys=True)
    await database.connect()
    await database.add_keys("pro", ["k1"])
    await database.add_keys("unlimited", ["u1"])
    await database.activate_paid(1, "pro", 30, 1, "c1")
    # смена тарифа: скрытый ключ просто возвращается в пул, отключать у провайдера не нужно
    act = await database.activate_paid(1, "unlimited", 30, 1, "c2")
    assert act.key == "u1" and act.revoked_key is None
    assert (await database.key_stats())["pro"] == {"free": 1}
    # окончание подписки: то же самое
    await database.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    expired = await database.expire_due()
    assert [(s.user_id, key) for s, key in expired] == [(1, None)]
    assert (await database.key_stats())["unlimited"] == {"free": 1}
    assert await database.keys_to_revoke() == []
    # освободившийся ключ достаётся следующему покупателю
    assert (await database.activate_paid(2, "pro", 30, 1, "c3")).key == "k1"
    await database.close()


async def test_exposed_keys_still_need_revoking(db):
    # ключи, выданные до включения эндпоинта, клиент видел — их надо отключать вручную
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 1, "c1")
    db.hide_keys = True
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    assert [key for _, key in await db.expire_due()] == ["k1"]


async def test_keys_shown_after_gateway_off_need_revoking(tmp_path):
    """Эндпоинт выключили: клиент теперь видит ключ провайдера напрямую. После окончания
    подписки такой ключ нельзя молча отдавать следующему покупателю: прежний клиент его знает."""
    path = str(tmp_path / "bot.db")
    with_gateway = Database(path, hide_keys=True)
    await with_gateway.connect()
    await with_gateway.add_keys("pro", ["k1"])
    await with_gateway.activate_paid(1, "pro", 30, 1, "c1")
    await with_gateway.close()

    without_gateway = Database(path, hide_keys=False)
    await without_gateway.connect()
    await without_gateway.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=1",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    assert [key for _, key in await without_gateway.expire_due()] == ["k1"]
    assert (await without_gateway.key_stats())["pro"] == {"revoke": 1}
    await without_gateway.close()


async def test_waiting_subscribers_get_keys_after_pool_refill(db):
    """Оплатил, пока пул был пуст → после /addkeys ключ должен достаться ему сам."""
    await db.activate_paid(1, "pro", 30, 1, "c1")  # пул пуст — ключа нет
    await db.activate_paid(2, "unlimited", 30, 1, "c2")  # другой тариф
    await db.add_keys("pro", ["k-old"])
    await db.activate_paid(3, "pro", 30, 1, "c3")  # получил k-old сразу
    await db.grant(4, "pro", 30)  # пул снова пуст
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=4",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )  # подписка уже кончилась — ключ ему не нужен

    await db.add_keys("pro", ["k1", "k2", "k3"])
    assigned = await db.assign_waiting("pro")

    assert assigned == [(1, "k1", None)]
    assert (await db.get_active_key(1)).key == "k1"
    assert (await db.get_active_key(3)).key == "k-old"
    assert await db.get_active_key(2) is None
    assert await db.get_active_key(4) is None
    assert (await db.key_stats())["pro"] == {"active": 2, "free": 2}


async def test_waiting_subscribers_more_than_keys(db):
    for user in (1, 2, 3):
        await db.grant(user, "pro", 30)
    await db.add_keys("pro", ["k1"])
    assigned = await db.assign_waiting("pro")
    assert assigned == [(1, "k1", None)]  # кто раньше купил — тот раньше получил
    assert await db.assign_waiting("pro") == []


async def test_givekey_replacement_does_not_recycle_bad_key():
    """Провайдер отклонил ключ, админ выдал новый через /givekey. Отклонённый ключ не должен
    вернуться в пул и достаться следующему покупателю."""
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    await db.add_keys("pro", ["dead-key"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    await db.assign_key(1, "good-key")
    assert (await db.key_stats())["pro"] == {"active": 1, "revoked": 1}
    assert (await db.activate_paid(2, "pro", 30, 2541, "c2")).key is None
    await db.close()


async def test_givekey_after_tariff_switch_returns_old_key_to_pool():
    """Ключ прежнего тарифа (клиент сменил тариф при пустом пуле) исправен: после /givekey
    он возвращается в пул."""
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    await db.add_keys("pro", ["k-pro"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    await db.activate_paid(1, "unlimited", 30, 6160, "c2")  # пул Unlimited пуст
    await db.assign_key(1, "u-manual")
    assert (await db.key_stats())["pro"] == {"free": 1}
    await db.close()


async def test_givekey_keeps_key_marked_as_seen(tmp_path):
    """Клиент видел ключ, пока эндпоинт был выключен. Повторная выдача того же ключа через
    /givekey после включения эндпоинта не должна снимать отметку «видел»: иначе после
    окончания подписки ключ молча уйдёт следующему покупателю."""
    path = str(tmp_path / "bot.db")
    old = Database(path, hide_keys=False)
    await old.connect()
    await old.add_keys("pro", ["k1"])
    await old.activate_paid(1, "pro", 30, 1, "c1")
    await old.close()

    db = Database(path, hide_keys=True)
    await db.connect()
    await db.assign_key(1, "k1")
    await expire_now(db, 1)
    assert [key for _, key in await db.expire_due()] == ["k1"]
    await db.close()


async def test_givekey_refuses_key_waiting_for_revocation(db):
    """Ключ в списке на отключение нельзя выдать снова: он пропал бы из /revoke_list,
    так и не отключённый у провайдера."""
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 1, "c1")
    await expire_now(db, 1)
    await db.expire_due()
    await db.grant(2, "pro", 30)
    with pytest.raises(KeyConflictError):
        await db.assign_key(2, "k1")
    assert [k.key for k in await db.keys_to_revoke()] == ["k1"]


async def test_tariff_switch_with_empty_pool_keeps_working_key():
    """Клиент на Pro оплатил Unlimited, а пул Unlimited пуст. Рабочий ключ Pro не должен
    уйти следующему покупателю, пока ключ Unlimited не выдан."""
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    await db.add_keys("pro", ["k-pro"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    act = await db.activate_paid(1, "unlimited", 30, 6160, "c2")
    assert act.key is None and act.revoked_key is None
    assert (await db.get_active_key(1)).key == "k-pro"
    assert (await db.activate_paid(2, "pro", 30, 2541, "c3")).key is None

    # пул Unlimited пополнили: клиент получает ключ, ключ Pro возвращается в пул
    await db.add_keys("unlimited", ["k-unl"])
    assert await db.assign_waiting("unlimited") == [(1, "k-unl", None)]
    assert (await db.get_active_key(1)).key == "k-unl"
    assert (await db.key_stats())["pro"] == {"free": 1}
    # и достаётся покупателю Pro, который ждал
    assert await db.assign_waiting("pro") == [(2, "k-pro", None)]
    await db.close()


async def test_waiting_after_switch_reports_seen_key_for_revocation(db):
    """Без эндпоинта клиент видел ключ прежнего тарифа: при выдаче нового его надо отключить."""
    await db.add_keys("pro", ["k-pro"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    await db.activate_paid(1, "unlimited", 30, 6160, "c2")
    await db.add_keys("unlimited", ["k-unl"])
    assert await db.assign_waiting("unlimited") == [(1, "k-unl", "k-pro")]
    assert [k.key for k in await db.keys_to_revoke()] == ["k-pro"]


async def test_client_keys(db):
    key = await db.get_or_create_client_key(5)
    assert key.startswith("sk-us-") and len(key) > 40
    assert await db.get_or_create_client_key(5) == key
    assert await db.user_by_client_key(key) == 5
    new = await db.rotate_client_key(5)
    assert await db.user_by_client_key(key) is None
    assert await db.user_by_client_key(new) == 5


async def test_migration_adds_columns(tmp_path):
    import aiosqlite

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE crypto_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
            "tariff TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )
        await conn.commit()
    database = Database(str(path))
    await database.connect()
    order = await database.create_crypto_order(1, "pro")
    order = await database.set_crypto_invoice(order.id, 7, "u", utcnow() + timedelta(hours=1))
    assert order.invoice_id == 7
    await database.close()
