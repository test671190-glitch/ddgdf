"""SQLite-хранилище: пользователи, подписки, платежи и пул API-ключей.

Ключи в пуле привязаны к тарифу. При покупке пользователь получает свободный ключ
своего тарифа. Ключ, который больше не должен работать (подписка закончилась,
сменился тариф), получает статус "revoke" — админ деактивирует его в панели провайдера.
"""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    tariff TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    notified_soon INTEGER NOT NULL DEFAULT 0,
    notified_expired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tariff TEXT NOT NULL,
    stars INTEGER NOT NULL,
    charge_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    refunded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tariff TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'free',  -- free | active | revoke | revoked
    assigned_at TEXT,
    exposed INTEGER NOT NULL DEFAULT 1  -- 1: клиент видел этот ключ, при отзыве его надо отключить
);
-- Собственные ключи клиентов (sk-us-...) для эндпоинта магазина
CREATE TABLE IF NOT EXISTS client_keys (
    user_id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys (user_id, status);
CREATE INDEX IF NOT EXISTS idx_keys_pool ON api_keys (tariff, status);
CREATE TABLE IF NOT EXISTS crypto_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tariff TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | expired
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    invoice_id INTEGER,
    pay_url TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_crypto_user ON crypto_orders (user_id, status);
CREATE TABLE IF NOT EXISTS web_logins (
    token TEXT PRIMARY KEY,
    user_id INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
-- Обращения в поддержку: какое сообщение в чате админа относится к какому клиенту.
-- Админ отвечает на него реплаем, и бот пересылает ответ этому клиенту
CREATE TABLE IF NOT EXISTS support_messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
"""

# Колонки, добавленные после первого релиза: (таблица, колонка, тип)
MIGRATIONS = (
    ("crypto_orders", "invoice_id", "INTEGER"),
    ("crypto_orders", "pay_url", "TEXT"),
    ("crypto_orders", "expires_at", "TEXT"),
    ("api_keys", "exposed", "INTEGER NOT NULL DEFAULT 1"),
)

CLIENT_KEY_PREFIX = "sk-us-"

CRYPTO_PREFIX = "crypto:"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class Subscription:
    user_id: int
    tariff: str
    expires_at: datetime

    @property
    def is_active(self) -> bool:
        return self.expires_at > utcnow()


@dataclass(frozen=True)
class ApiKey:
    id: int
    tariff: str
    key: str
    user_id: int | None
    status: str


@dataclass(frozen=True)
class Activation:
    tariff: str
    expires_at: datetime
    key: str | None  # None — в пуле нет свободных ключей, нужна ручная выдача
    revoked_key: str | None  # старый ключ, который надо деактивировать
    extended: bool


@dataclass(frozen=True)
class Payment:
    user_id: int
    tariff: str
    stars: int
    charge_id: str
    refunded: bool


@dataclass(frozen=True)
class CryptoOrder:
    id: int
    user_id: int
    tariff: str
    status: str
    invoice_id: int | None = None
    pay_url: str | None = None
    expires_at: datetime | None = None


class KeyConflictError(Exception):
    pass


class Database:
    def __init__(self, path: str, hide_keys: bool = False) -> None:
        self.path = path
        # True, когда клиенты ходят через наш эндпоинт и не видят ключи провайдера:
        # такие ключи после окончания подписки просто возвращаются в пул
        self.hide_keys = hide_keys
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:
            columns = {r["name"] for r in await self._all(f"PRAGMA table_info({table})")}
            if column not in columns:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        if not self.hide_keys:
            # Эндпоинт выключен: клиенты видят ключи провайдера напрямую, в том числе выданные,
            # пока он работал. Такие ключи при отзыве надо отключать, а не возвращать в пул
            await self._conn.execute(
                "UPDATE api_keys SET exposed=1 WHERE status='active' AND exposed=0"
            )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def _one(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    # --- пользователи ---

    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO users (id, username, full_name, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
                "full_name=excluded.full_name",
                (user_id, username, full_name, _iso(utcnow())),
            )

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM users WHERE id=?", (user_id,))

    # --- подписки и ключи ---

    async def get_subscription(self, user_id: int) -> Subscription | None:
        row = await self._one(
            "SELECT user_id, tariff, expires_at FROM subscriptions WHERE user_id=?", (user_id,)
        )
        return _subscription(row) if row else None

    async def get_active_key(self, user_id: int) -> ApiKey | None:
        row = await self._one(
            "SELECT * FROM api_keys WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return _api_key(row) if row else None

    async def activate_paid(
        self, user_id: int, tariff: str, days: int, stars: int, charge_id: str
    ) -> Activation | None:
        """Записывает платёж и продлевает подписку. None — платёж уже был обработан."""
        now = utcnow()
        async with self._tx() as conn:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO payments (user_id, tariff, stars, charge_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, tariff, stars, charge_id, _iso(now)),
            )
            if cur.rowcount == 0:
                return None
            return await self._apply(user_id, tariff, days, now)

    async def grant(self, user_id: int, tariff: str, days: int) -> Activation:
        async with self._tx():
            return await self._apply(user_id, tariff, days, utcnow())

    async def _apply(self, user_id: int, tariff: str, days: int, now: datetime) -> Activation:
        sub = await self.get_subscription(user_id)
        extended = sub is not None and sub.tariff == tariff and sub.expires_at > now
        start = sub.expires_at if extended and sub else now
        expires = start + timedelta(days=days)
        await self.conn.execute(
            "INSERT INTO subscriptions (user_id, tariff, expires_at, notified_soon, "
            "notified_expired) VALUES (?, ?, ?, 0, 0) ON CONFLICT(user_id) DO UPDATE SET "
            "tariff=excluded.tariff, expires_at=excluded.expires_at, notified_soon=0, "
            "notified_expired=0",
            (user_id, tariff, _iso(expires)),
        )

        current = await self.get_active_key(user_id)
        revoked = None
        if current and current.tariff == tariff:
            key = current.key
        else:
            key = await self._take_free_key(user_id, tariff, now)
            # Старый ключ снимаем, только когда есть новый: при пустом пуле клиент до выдачи
            # ключа нового тарифа работает по прежнему, а не остаётся совсем без доступа
            if current and key is not None:
                revoked = await self._revoke(current.id)
        return Activation(tariff, expires, key, revoked, extended)

    async def _take_free_key(self, user_id: int, tariff: str, now: datetime) -> str | None:
        row = await self._one(
            "SELECT id, key FROM api_keys WHERE tariff=? AND status='free' ORDER BY id LIMIT 1",
            (tariff,),
        )
        if row is None:
            return None
        await self.conn.execute(
            "UPDATE api_keys SET status='active', user_id=?, assigned_at=?, exposed=? WHERE id=?",
            (user_id, _iso(now), 0 if self.hide_keys else 1, row["id"]),
        )
        return row["key"]

    async def _revoke(self, key_id: int, reuse: bool = True) -> str | None:
        """Снимает ключ с пользователя. Возвращает ключ, если его надо отключить у провайдера.

        Ключ, который клиент ни разу не видел (работа через наш эндпоинт), безопасно
        вернуть в пул: тогда возвращается None и делать ничего не нужно.
        reuse=False — ключ заменяют потому, что он не работает: в пул его не возвращаем.
        """
        row = await self._one("SELECT key, exposed FROM api_keys WHERE id=?", (key_id,))
        if row is None:
            return None
        if not row["exposed"]:
            if reuse:
                await self.conn.execute(
                    "UPDATE api_keys SET status='free', user_id=NULL, assigned_at=NULL WHERE id=?",
                    (key_id,),
                )
            else:
                await self.conn.execute(
                    "UPDATE api_keys SET status='revoked' WHERE id=?", (key_id,)
                )
            return None
        await self.conn.execute("UPDATE api_keys SET status='revoke' WHERE id=?", (key_id,))
        return row["key"]

    async def assign_key(self, user_id: int, key: str) -> tuple[Subscription, str | None] | None:
        """Ручная выдача ключа под текущий тариф пользователя. None — нет активной подписки."""
        now = utcnow()
        async with self._tx() as conn:
            sub = await self.get_subscription(user_id)
            if sub is None or not sub.is_active:
                return None
            existing = await self._one("SELECT * FROM api_keys WHERE key=?", (key,))
            if existing and existing["status"] == "active" and existing["user_id"] != user_id:
                raise KeyConflictError(f"Ключ уже выдан пользователю {existing['user_id']}")
            if existing and existing["status"] in ("revoke", "revoked"):
                # такой ключ видел прежний клиент и он ждёт (или прошёл) отключения
                raise KeyConflictError("Ключ отозван — выдайте другой")
            current = await self.get_active_key(user_id)
            revoked = None
            if current and current.key != key:
                # Замена ключа того же тарифа — обычно потому, что он не работает: в пул его не
                # возвращаем. Ключ прежнего тарифа (остался после смены тарифа) исправен
                revoked = await self._revoke(current.id, reuse=current.tariff != sub.tariff)
            await conn.execute(
                "INSERT INTO api_keys (tariff, key, user_id, status, assigned_at, exposed) "
                "VALUES (?, ?, ?, 'active', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "tariff=excluded.tariff, user_id=excluded.user_id, status='active', "
                "assigned_at=excluded.assigned_at, "
                # Если клиент уже видел ключ, он остаётся «засвеченным». Свободный ключ из пула
                # не видел никто (у только что добавленного exposed=1 по умолчанию)
                "exposed=CASE WHEN api_keys.status='free' THEN excluded.exposed "
                "ELSE MAX(api_keys.exposed, excluded.exposed) END",
                (sub.tariff, key, user_id, _iso(now), 0 if self.hide_keys else 1),
            )
            return sub, revoked

    async def assign_waiting(self, tariff: str) -> list[tuple[int, str, str | None]]:
        """Раздаёт свободные ключи тем, кто оплатил тариф, пока пул был пуст.

        Возвращает [(user_id, ключ провайдера, ключ на отключение)] — им надо отправить ключ.
        Третий элемент — ключ прежнего тарифа, если клиент сменил тариф при пустом пуле.
        Первыми получают те, у кого подписка кончается раньше (то есть купившие раньше).
        """
        now = utcnow()
        async with self._tx():
            rows = await self._all(
                "SELECT s.user_id FROM subscriptions s WHERE s.tariff=? AND s.expires_at > ? "
                "AND NOT EXISTS (SELECT 1 FROM api_keys k "
                "WHERE k.user_id=s.user_id AND k.status='active' AND k.tariff=s.tariff) "
                "ORDER BY s.expires_at, s.user_id",
                (tariff, _iso(now)),
            )
            assigned = []
            for row in rows:
                old = await self.get_active_key(row["user_id"])
                key = await self._take_free_key(row["user_id"], tariff, now)
                if key is None:
                    break
                revoked = await self._revoke(old.id) if old else None
                assigned.append((row["user_id"], key, revoked))
            return assigned

    async def add_keys(self, tariff: str, keys: list[str]) -> int:
        async with self._tx() as conn:
            before = conn.total_changes
            # Ключ, снятый через /givekey как нерабочий (клиент его не видел), админ может
            # вернуть в пул, добавив снова: иначе ошибочно снятый рабочий ключ терялся навсегда
            await conn.executemany(
                "INSERT INTO api_keys (tariff, key) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                "tariff=excluded.tariff, status='free', user_id=NULL, assigned_at=NULL "
                "WHERE api_keys.status='revoked' AND api_keys.exposed=0",
                [(tariff, k) for k in keys],
            )
            return conn.total_changes - before

    async def key_stats(self) -> dict[str, dict[str, int]]:
        rows = await self._all(
            "SELECT tariff, status, COUNT(*) AS n FROM api_keys GROUP BY tariff, status"
        )
        stats: dict[str, dict[str, int]] = {}
        for row in rows:
            stats.setdefault(row["tariff"], {})[row["status"]] = row["n"]
        return stats

    async def keys_to_revoke(self) -> list[ApiKey]:
        rows = await self._all("SELECT * FROM api_keys WHERE status='revoke' ORDER BY id")
        return [_api_key(r) for r in rows]

    async def mark_revoked_done(self, key: str) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE api_keys SET status='revoked' WHERE key=? AND status='revoke'", (key,)
            )
            return cur.rowcount > 0

    # --- оплата криптой (счета Crypto Pay, проверяются автоматически) ---

    async def get_crypto_order(self, order_id: int) -> CryptoOrder | None:
        row = await self._one("SELECT * FROM crypto_orders WHERE id=?", (order_id,))
        return _crypto_order(row) if row else None

    async def open_crypto_order(self, user_id: int, tariff: str) -> CryptoOrder | None:
        """Неоплаченный и не просроченный счёт того же тарифа — чтобы не плодить дубли."""
        row = await self._one(
            "SELECT * FROM crypto_orders WHERE user_id=? AND tariff=? AND status='pending' "
            "AND pay_url IS NOT NULL AND expires_at > ? ORDER BY id DESC LIMIT 1",
            (user_id, tariff, _iso(utcnow() + timedelta(minutes=5))),
        )
        return _crypto_order(row) if row else None

    async def create_crypto_order(self, user_id: int, tariff: str) -> CryptoOrder:
        now = _iso(utcnow())
        async with self._tx() as conn:
            cur = await conn.execute(
                "INSERT INTO crypto_orders (user_id, tariff, status, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (user_id, tariff, now, now),
            )
            return CryptoOrder(cur.lastrowid, user_id, tariff, "pending")

    async def set_crypto_invoice(
        self, order_id: int, invoice_id: int, pay_url: str, expires_at: datetime
    ) -> CryptoOrder:
        async with self._tx() as conn:
            await conn.execute(
                "UPDATE crypto_orders SET invoice_id=?, pay_url=?, expires_at=?, updated_at=? "
                "WHERE id=?",
                (invoice_id, pay_url, _iso(expires_at), _iso(utcnow()), order_id),
            )
        return await self.get_crypto_order(order_id)

    async def pending_crypto_orders(self) -> list[CryptoOrder]:
        rows = await self._all(
            "SELECT * FROM crypto_orders WHERE status='pending' AND invoice_id IS NOT NULL"
        )
        return [_crypto_order(r) for r in rows]

    async def expire_crypto_order(self, order_id: int) -> bool:
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE crypto_orders SET status='expired', updated_at=? "
                "WHERE id=? AND status='pending'",
                (_iso(utcnow()), order_id),
            )
            return cur.rowcount > 0

    async def approve_crypto_order(
        self, order_id: int, days: int
    ) -> tuple[CryptoOrder, Activation] | None:
        """Счёт оплачен. None — заказ уже обработан (защита от двойного начисления)."""
        now = utcnow()
        async with self._tx() as conn:
            cur = await conn.execute(
                "UPDATE crypto_orders SET status='approved', updated_at=? "
                "WHERE id=? AND status IN ('pending', 'expired')",
                (_iso(now), order_id),
            )
            if cur.rowcount == 0:
                return None
            order = await self.get_crypto_order(order_id)
            charge_id = f"{CRYPTO_PREFIX}{order_id}"
            await conn.execute(
                "INSERT INTO payments (user_id, tariff, stars, charge_id, created_at) "
                "VALUES (?, ?, 0, ?, ?)",
                (order.user_id, order.tariff, charge_id, _iso(now)),
            )
            return order, await self._apply(order.user_id, order.tariff, days, now)

    # --- вход на сайт через бота ---

    async def create_web_login(self, token: str, ttl: timedelta) -> None:
        now = utcnow()
        async with self._tx() as conn:
            await conn.execute("DELETE FROM web_logins WHERE expires_at < ?", (_iso(now),))
            await conn.execute(
                "INSERT INTO web_logins (token, created_at, expires_at) VALUES (?, ?, ?)",
                (token, _iso(now), _iso(now + ttl)),
            )

    async def confirm_web_login(self, token: str, user_id: int) -> bool:
        async with self._tx() as conn:
            # Повторное нажатие тем же человеком — тоже успех, а не «ссылка устарела».
            # Подтвердить вход другим аккаунтом по-прежнему нельзя
            cur = await conn.execute(
                "UPDATE web_logins SET user_id=? WHERE token=? "
                "AND (user_id IS NULL OR user_id=?) AND expires_at > ?",
                (user_id, token, user_id, _iso(utcnow())),
            )
            return cur.rowcount > 0

    async def web_login_status(self, token: str) -> tuple[str, int | None]:
        """("pending" | "confirmed" | "expired", user_id)."""
        row = await self._one("SELECT * FROM web_logins WHERE token=?", (token,))
        if row is None:
            return "expired", None
        if row["user_id"] is not None:
            return "confirmed", row["user_id"]
        if datetime.fromisoformat(row["expires_at"]) <= utcnow():
            return "expired", None
        return "pending", None

    async def consume_web_login(self, token: str) -> int | None:
        """Одноразово превращает подтверждённый вход в user_id."""
        async with self._tx() as conn:
            row = await self._one("SELECT user_id FROM web_logins WHERE token=?", (token,))
            if row is None or row["user_id"] is None:
                return None
            await conn.execute("DELETE FROM web_logins WHERE token=?", (token,))
            return row["user_id"]

    async def create_web_session(self, session_id: str, user_id: int, ttl: timedelta) -> None:
        now = utcnow()
        async with self._tx() as conn:
            await conn.execute("DELETE FROM web_sessions WHERE created_at <= ?", (_iso(now - ttl),))
            await conn.execute(
                "INSERT INTO web_sessions (id, user_id, created_at) VALUES (?, ?, ?)",
                (session_id, user_id, _iso(now)),
            )

    async def web_session_user(self, session_id: str, ttl: timedelta) -> int | None:
        """Сессия живёт ttl с момента входа: украденная cookie не работает вечно."""
        row = await self._one(
            "SELECT user_id FROM web_sessions WHERE id=? AND created_at > ?",
            (session_id, _iso(utcnow() - ttl)),
        )
        return row["user_id"] if row else None

    async def delete_web_session(self, session_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute("DELETE FROM web_sessions WHERE id=?", (session_id,))

    # --- поддержка ---

    async def add_support_message(self, chat_id: int, message_id: int, user_id: int) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO support_messages "
                "(chat_id, message_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, message_id, user_id, _iso(utcnow())),
            )

    async def support_user(self, chat_id: int, message_id: int) -> int | None:
        """Клиент, чьё обращение админ получил сообщением message_id."""
        row = await self._one(
            "SELECT user_id FROM support_messages WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        )
        return row["user_id"] if row else None

    # --- платежи ---

    async def get_payment(self, charge_id: str) -> Payment | None:
        row = await self._one("SELECT * FROM payments WHERE charge_id=?", (charge_id,))
        if row is None:
            return None
        return Payment(
            row["user_id"], row["tariff"], row["stars"], row["charge_id"], bool(row["refunded"])
        )

    # --- напоминания и окончание подписок ---

    async def due_reminders(self, within: timedelta) -> list[Subscription]:
        now = utcnow()
        rows = await self._all(
            "SELECT user_id, tariff, expires_at FROM subscriptions "
            "WHERE notified_soon=0 AND expires_at > ? AND expires_at <= ?",
            (_iso(now), _iso(now + within)),
        )
        return [_subscription(r) for r in rows]

    async def mark_reminded(self, user_id: int, expires_at: datetime) -> None:
        """Отмечает напоминание о периоде, который кончается в expires_at. Если клиент
        успел продлить подписку, пока шла рассылка, флаг нового периода не трогаем."""
        async with self._tx() as conn:
            # datetime() сравнивает моменты, а не строки: дата, поправленная в базе вручную
            # в другом формате («Z» вместо «+00:00»), иначе давала напоминание каждые 10 минут
            await conn.execute(
                "UPDATE subscriptions SET notified_soon=1 "
                "WHERE user_id=? AND datetime(expires_at)=datetime(?)",
                (user_id, _iso(expires_at)),
            )

    async def expire_due(self) -> list[tuple[Subscription, str | None]]:
        """Закрывает истёкшие подписки. Возвращает их и ключи, которые надо деактивировать."""
        async with self._tx() as conn:
            rows = await self._all(
                "SELECT user_id, tariff, expires_at FROM subscriptions "
                "WHERE notified_expired=0 AND expires_at <= ?",
                (_iso(utcnow()),),
            )
            result = []
            for row in rows:
                sub = _subscription(row)
                key = await self.get_active_key(sub.user_id)
                to_disable = await self._revoke(key.id) if key else None
                await conn.execute(
                    "UPDATE subscriptions SET notified_expired=1 WHERE user_id=?", (sub.user_id,)
                )
                result.append((sub, to_disable))
            return result

    # --- собственные ключи клиентов (эндпоинт магазина) ---

    async def get_client_key(self, user_id: int) -> str | None:
        row = await self._one("SELECT key FROM client_keys WHERE user_id=?", (user_id,))
        return row["key"] if row else None

    async def get_or_create_client_key(self, user_id: int) -> str:
        existing = await self.get_client_key(user_id)
        if existing:
            return existing
        async with self._tx() as conn:
            key = CLIENT_KEY_PREFIX + secrets.token_urlsafe(32)
            await conn.execute(
                "INSERT OR IGNORE INTO client_keys (user_id, key, created_at) VALUES (?, ?, ?)",
                (user_id, key, _iso(utcnow())),
            )
        return await self.get_client_key(user_id)  # type: ignore[return-value]

    async def rotate_client_key(self, user_id: int) -> str:
        """Перевыпуск: старый ключ сразу перестаёт работать.

        Заодно закрываются входы на сайт: ключ перевыпускают, когда доступ мог попасть
        к чужому, а сайт показывает ключ — иначе чужой браузер сразу увидел бы новый.
        """
        key = CLIENT_KEY_PREFIX + secrets.token_urlsafe(32)
        async with self._tx() as conn:
            await conn.execute(
                "INSERT INTO client_keys (user_id, key, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET key=excluded.key, "
                "created_at=excluded.created_at",
                (user_id, key, _iso(utcnow())),
            )
            await conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
        return key

    async def user_by_client_key(self, key: str) -> int | None:
        row = await self._one("SELECT user_id FROM client_keys WHERE key=?", (key,))
        return row["user_id"] if row else None

    # --- статистика ---

    async def stats(self, exclude_users: list[int] | tuple[int, ...] = ()) -> dict:
        """exclude_users — кого не считать клиентами (админы тоже пишут боту)."""
        now = _iso(utcnow())
        marks = ",".join("?" * len(exclude_users))
        users = await self._one(
            f"SELECT COUNT(*) AS n FROM users WHERE id NOT IN ({marks})", tuple(exclude_users)
        )
        active = await self._all(
            "SELECT tariff, COUNT(*) AS n FROM subscriptions WHERE expires_at > ? GROUP BY tariff",
            (now,),
        )
        pays = await self._one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(stars), 0) AS stars FROM payments "
            "WHERE refunded=0 AND charge_id NOT LIKE ?",
            (CRYPTO_PREFIX + "%",),
        )
        crypto = await self._all(
            "SELECT tariff, COUNT(*) AS n FROM payments WHERE refunded=0 AND charge_id LIKE ? "
            "GROUP BY tariff",
            (CRYPTO_PREFIX + "%",),
        )
        return {
            "users": users["n"] if users else 0,
            "active": {r["tariff"]: r["n"] for r in active},
            "payments": pays["n"] if pays else 0,
            "stars": pays["stars"] if pays else 0,
            "crypto": {r["tariff"]: r["n"] for r in crypto},
        }


def _subscription(row: aiosqlite.Row) -> Subscription:
    return Subscription(row["user_id"], row["tariff"], datetime.fromisoformat(row["expires_at"]))


def _crypto_order(row: aiosqlite.Row) -> CryptoOrder:
    return CryptoOrder(
        row["id"],
        row["user_id"],
        row["tariff"],
        row["status"],
        row["invoice_id"],
        row["pay_url"],
        datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
    )


def _api_key(row: aiosqlite.Row) -> ApiKey:
    return ApiKey(row["id"], row["tariff"], row["key"], row["user_id"], row["status"])
