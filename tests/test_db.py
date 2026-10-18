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
    await db.mark_reminded(1)
    assert await db.due_reminders(timedelta(days=3)) == []


async def test_refund_ends_single_period(db):
    await db.add_keys("pro", ["k1"])
    await db.activate_paid(1, "pro", 30, 2541, "c1")
    refund = await db.refund("c1", 30)
    assert refund.revoked_key == "k1"
    assert not (await db.get_subscription(1)).is_active
    assert await db.refund("c1", 30) is None


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
