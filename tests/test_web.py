"""Сайт: каталог, вход через бота, оплата криптой и прокси к API моделей."""

import contextlib
import json
from datetime import timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot.services.crypto as crypto_service
from bot.catalog import TOTAL_MODELS
from bot.config import Settings
from bot.db import Database, utcnow
from bot.services.crypto import CryptoPayments
from bot.services.cryptopay import Invoice
from bot.web.server import SESSION_COOKIE, WebApp, catalog_payload


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_) -> None:
        self.sent.append((chat_id, text))


class FakeCryptoClient:
    def __init__(self) -> None:
        self.status: dict[int, str] = {}
        self.created = 0

    async def create_invoice(self, amount_usd, description, payload, expires_in) -> Invoice:
        self.created += 1
        invoice_id = 1000 + self.created
        self.status[invoice_id] = "active"
        return Invoice(invoice_id, "active", f"https://t.me/CryptoBot?start={invoice_id}", payload)

    async def get_invoices(self, invoice_ids):
        return [Invoice(i, self.status[i], "", "") for i in invoice_ids]


async def fake_upstream(request: web.Request) -> web.StreamResponse:
    """Имитирует API моделей: проверяет ключ, отвечает JSON или SSE."""
    if request.headers.get("Authorization") == "Bearer k-lapsed":
        return web.json_response({"error": {"code": "subscription_required"}}, status=402)
    if request.headers.get("Authorization") != "Bearer k-pro":
        return web.json_response({"error": {"code": "invalid_api_key"}}, status=401)
    if request.method == "GET":
        return web.json_response({"id": request.match_info["tail"], "status": "completed"})
    body = await request.json()
    if body.get("model") == "broken-stream":
        # Провайдер обрывает соединение посреди ответа
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        request.transport.close()
        return resp
    if body.get("stream"):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response({"echo": body}, headers={"X-Plan-Remaining": "5"})


@pytest.fixture
async def env():
    db = Database(":memory:")
    await db.connect()

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/v1/{tail:.*}", fake_upstream)
    upstream = TestServer(upstream_app)
    await upstream.start_server()

    settings = Settings(
        bot_token="1:x", api_base_url=str(upstream.make_url("/v1")), admin_ids=[], public_api_url=""
    )
    bot = FakeBot()
    crypto_client = FakeCryptoClient()
    crypto = CryptoPayments(crypto_client, db, bot, settings)
    client = TestClient(TestServer(WebApp(db, settings, crypto, "shopbot").build()))
    await client.start_server()
    yield client, db, bot, crypto_client
    await client.close()
    await upstream.close()
    await db.close()


async def login(client: TestClient, db: Database, user_id: int) -> None:
    await db.upsert_user(user_id, "neo", "Neo")
    data = await (await client.post("/api/auth/login")).json()
    assert data["url"] == f"https://t.me/shopbot?start=login_{data['token']}"
    status = await (await client.get(f"/api/auth/login/{data['token']}")).json()
    assert status == {"status": "pending"}
    assert await db.confirm_web_login(data["token"], user_id)
    resp = await client.get(f"/api/auth/login/{data['token']}")
    assert (await resp.json())["status"] == "confirmed"
    assert SESSION_COOKIE in resp.cookies


async def test_catalog(env):
    client, *_ = env
    data = await (await client.get("/api/catalog")).json()
    assert len(data["models"]) == TOTAL_MODELS
    assert [t["code"] for t in data["tariffs"]] == ["pro", "unlimited"]
    astra = next(m for m in data["models"] if m["id"] == "gpt-6-astra")
    assert astra["default_reasoning"] == "medium" and astra["type"] == "text"
    assert data["bot_username"] == "shopbot"


async def test_me_requires_login(env):
    client, *_ = env
    resp = await client.get("/api/me")
    assert resp.status == 401
    assert (await resp.json())["error"]["code"] == "unauthorized"


async def test_login_and_me(env):
    client, db, *_ = env
    await login(client, db, 42)
    me = await (await client.get("/api/me")).json()
    assert me["user"]["id"] == 42 and me["subscription"] is None and me["api_key"] is None

    await client.post("/api/auth/logout")
    assert (await client.get("/api/me")).status == 401


async def test_login_token_is_single_use(env):
    client, db, *_ = env
    data = await (await client.post("/api/auth/login")).json()
    await db.confirm_web_login(data["token"], 1)
    await client.get(f"/api/auth/login/{data['token']}")
    second = await (await client.get(f"/api/auth/login/{data['token']}")).json()
    assert second == {"status": "expired"}


async def test_login_link_works_only_in_browser_that_started_it(env):
    """Login CSRF: злоумышленник подтверждает вход своим аккаунтом и отправляет жертву на адрес
    проверки. Браузер жертвы этот вход не начинал и сессию чужого аккаунта не получает."""
    client, db, *_ = env
    data = await (await client.post("/api/auth/login")).json()
    await db.confirm_web_login(data["token"], 666)
    client.session.cookie_jar.clear()  # другой браузер: cookie входа у него нет
    resp = await client.get(f"/api/auth/login/{data['token']}")
    assert (await resp.json())["status"] == "expired"
    assert SESSION_COOKIE not in resp.cookies
    assert (await client.get("/api/me")).status == 401


async def test_login_status_with_unicode_token(env):
    client, *_ = env
    resp = await client.get("/api/auth/login/%D1%82%D0%BE%D0%BA%D0%B5%D0%BD")
    assert resp.status == 200 and (await resp.json())["status"] == "expired"


async def test_parallel_logins_in_one_browser(env):
    """Вход из двух вкладок: cookie каждого входа своя и не мешает другой."""
    client, db, *_ = env
    first = await (await client.post("/api/auth/login")).json()
    await (await client.post("/api/auth/login")).json()
    await db.confirm_web_login(first["token"], 42)
    resp = await client.get(f"/api/auth/login/{first['token']}")
    assert (await resp.json())["status"] == "confirmed"


async def test_crypto_payment_activates_subscription(env, monkeypatch):
    # статус проверяется сразу после оплаты: пауза между запросами к Crypto Pay здесь мешает
    monkeypatch.setattr(crypto_service, "CHECK_INTERVAL", 0)
    client, db, bot, crypto_client = env
    await db.add_keys("pro", ["k-pro"])
    await login(client, db, 42)

    resp = await client.post("/api/pay/crypto", json={"tariff": "pro"})
    order = await resp.json()
    assert order["amount_usd"] == 50 and order["pay_url"].startswith("https://t.me/CryptoBot")
    # Повторный запрос возвращает тот же счёт
    again = await (await client.post("/api/pay/crypto", json={"tariff": "pro"})).json()
    assert again["order_id"] == order["order_id"]

    status = await (await client.get(f"/api/pay/crypto/{order['order_id']}")).json()
    assert status == {"status": "pending"}

    crypto_client.status[1001] = "paid"
    status = await (await client.get(f"/api/pay/crypto/{order['order_id']}")).json()
    assert status == {"status": "paid"}
    me = await (await client.get("/api/me")).json()
    assert me["subscription"]["tariff"] == "pro" and me["api_key"] == "k-pro"
    assert any(chat_id == 42 for chat_id, _ in bot.sent)


async def test_crypto_order_of_other_user_is_hidden(env):
    client, db, *_ = env
    order = await db.create_crypto_order(7, "pro")
    await login(client, db, 42)
    assert (await client.get(f"/api/pay/crypto/{order.id}")).status == 404


async def test_proxy_requires_subscription(env):
    client, db, *_ = env
    await login(client, db, 42)
    resp = await client.post("/api/chat/completions", json={"model": "x", "messages": []})
    assert resp.status == 402
    assert (await resp.json())["error"]["code"] == "subscription_required"


async def test_proxy_forwards_with_user_key(env):
    client, db, *_ = env
    await db.add_keys("pro", ["k-pro"])
    await db.grant(42, "pro", 30)
    await login(client, db, 42)

    resp = await client.post("/api/images/generations", json={"model": "flux-2", "prompt": "cat"})
    assert resp.status == 200
    assert (await resp.json())["echo"]["prompt"] == "cat"
    assert resp.headers["X-Plan-Remaining"] == "5"

    resp = await client.post(
        "/api/chat/completions", json={"model": "gpt-6-astra", "stream": True, "messages": []}
    )
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    body = (await resp.read()).decode()
    assert "data: [DONE]" in body
    assert json.loads(body.split("\n")[0].removeprefix("data: "))["choices"][0]["delta"]

    video = await (await client.get("/api/videos/vid_1")).json()
    assert video == {"id": "videos/vid_1", "status": "completed"}


async def test_proxy_rejects_form_posts(env):
    client, db, *_ = env
    await db.grant(42, "pro", 30)
    await login(client, db, 42)
    resp = await client.post("/api/chat/completions", data="model=x")
    assert resp.status == 415


async def test_expired_subscription_blocks_proxy(env):
    client, db, *_ = env
    await db.add_keys("pro", ["k-pro"])
    await db.grant(42, "pro", 30)
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=42",
        ((utcnow() - timedelta(minutes=1)).isoformat(),),
    )
    await login(client, db, 42)
    resp = await client.post("/api/videos", json={"model": "veo-3.1", "prompt": "x"})
    assert resp.status == 402


async def test_asset_path_traversal_blocked(env):
    client, *_ = env
    resp = await client.get("/assets/..%2F..%2F.env")
    assert resp.status == 404


@pytest.mark.parametrize(
    "video_id", ["..", "%2E%2E", "..%2F..%2Fadmin", "x%3Fa=1", ".hidden", "a%2Fb"]
)
async def test_video_status_rejects_path_tricks(env, video_id):
    """ID видео не должен уводить запрос с ключом провайдера на другие адреса."""
    client, db, *_ = env
    await db.add_keys("pro", ["k-pro"])
    await db.grant(42, "pro", 30)
    await login(client, db, 42)
    resp = await client.get(f"/api/videos/{video_id}")
    assert resp.status == 404


async def test_session_expires_on_server(env):
    client, db, *_ = env
    await login(client, db, 42)
    assert (await client.get("/api/me")).status == 200
    await db.conn.execute(
        "UPDATE web_sessions SET created_at=?",
        ((utcnow() - timedelta(days=31)).isoformat(),),
    )
    assert (await client.get("/api/me")).status == 401


async def test_security_headers(env):
    client, *_ = env
    for path in ("/api/catalog", "/assets/js/app.js", "/api/nope"):
        resp = await client.get(path)
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_catalog_shows_shop_endpoint():
    """С собственным эндпоинтом ключ sk-us-... работает только с нашим адресом,
    поэтому сайт должен показывать его, а не адрес провайдера."""
    settings = Settings(
        bot_token="1:x",
        api_base_url="https://provider.example/api/v1",
        public_api_url="https://shop.example/v1/",
    )
    assert catalog_payload(settings, "shopbot")["api_base_url"] == "https://shop.example/v1"


async def test_rejected_provider_key_is_not_session_error(env):
    """401 провайдера (плохой ключ из пула) не должен выглядеть для сайта как выход из сессии."""
    client, db, *_ = env
    await db.add_keys("pro", ["k-bad"])
    await db.grant(42, "pro", 30)
    await login(client, db, 42)
    resp = await client.post("/api/chat/completions", json={"model": "x", "messages": []})
    assert resp.status == 503
    assert (await resp.json())["error"]["code"] == "provider_key_error"


async def test_provider_402_is_not_shown_as_missing_subscription(env):
    """У аккаунта провайдера кончился тариф: оплативший клиент не должен видеть
    «нужна активная подписка» — исправление из gateway.py не было перенесено сюда."""
    client, db, *_ = env
    await db.add_keys("pro", ["k-lapsed"])
    await db.grant(42, "pro", 30)
    await login(client, db, 42)
    resp = await client.post("/api/chat/completions", json={"model": "x", "messages": []})
    assert resp.status == 503
    assert (await resp.json())["error"]["code"] == "provider_key_error"


async def test_stream_broken_by_provider_ends_cleanly(env):
    client, db, *_ = env
    await db.add_keys("pro", ["k-pro"])
    await db.grant(42, "pro", 30)
    await login(client, db, 42)
    resp = await client.post(
        "/api/chat/completions", json={"model": "broken-stream", "stream": True, "messages": []}
    )
    assert resp.status == 200
    body = await resp.read()
    assert body.startswith(b"data: ") and b"HTTP/1.1" not in body
    # Обрезанный ответ не должен выглядеть законченным: сайт показывает ошибку из потока
    last = json.loads(body.strip().split(b"\n\n")[-1].removeprefix(b"data: "))
    assert last["error"]["code"] == "upstream_error"


async def test_huge_order_id_is_not_found(env):
    client, db, *_ = env
    await login(client, db, 42)
    resp = await client.get("/api/pay/crypto/99999999999999999999")
    assert resp.status == 404


async def test_cookie_secure_behind_https_proxy(env):
    client, db, *_ = env
    data = await (await client.post("/api/auth/login")).json()
    await db.confirm_web_login(data["token"], 42)
    resp = await client.get(
        f"/api/auth/login/{data['token']}", headers={"X-Forwarded-Proto": "https"}
    )
    assert resp.cookies[SESSION_COOKIE]["secure"]


@contextlib.asynccontextmanager
async def provider_env(handler):
    """Сайт с провайдером handler и двумя клиентами с подпиской (ключи k-one и k-two)."""
    db = Database(":memory:")
    await db.connect()
    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/v1/{tail:.*}", handler)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    # по имени, а не по IP: cookie с IP-адресов aiohttp не хранит, а у провайдера домен
    api_base_url = f"http://localhost:{upstream.port}/v1"
    settings = Settings(bot_token="1:x", api_base_url=api_base_url, admin_ids=[], public_api_url="")
    client = TestClient(TestServer(WebApp(db, settings, None, "shopbot").build()))
    await client.start_server()
    try:
        await db.add_keys("pro", ["k-one", "k-two"])
        for user_id in (1, 2):
            await db.grant(user_id, "pro", 30)
        yield client, db
    finally:
        await client.close()
        await upstream.close()
        await db.close()


async def test_provider_cookie_of_one_client_is_not_sent_for_another():
    """Сессия сайта к провайдеру общая на всех: cookie, которую провайдер поставил на запрос
    одного клиента, уходила с запросами другого."""
    cookies: list[str | None] = []

    async def upstream(request: web.Request) -> web.Response:
        cookies.append(request.headers.get("Cookie"))
        resp = web.json_response({"ok": True})
        resp.set_cookie("provider_session", request.headers["Authorization"].split()[-1])
        return resp

    async with provider_env(upstream) as (client, db):
        for user_id in (1, 2):
            await login(client, db, user_id)
            resp = await client.post("/api/chat/completions", json={"model": "x", "messages": []})
            assert resp.status == 200
            await client.post("/api/auth/logout")
    assert cookies == [None, None]


async def test_provider_redirect_is_not_followed_with_key():
    """Сайт сам проходил редиректы провайдера и уносил ключ на любой путь того же хоста."""
    hits: list[str] = []

    async def upstream(request: web.Request) -> web.Response:
        hits.append(request.match_info["tail"])
        if request.match_info["tail"] == "videos/vid_1":
            raise web.HTTPFound("/v1/dashboard/billing")
        return web.json_response({"billing": "secret"})

    async with provider_env(upstream) as (client, db):
        await login(client, db, 1)
        resp = await client.get("/api/videos/vid_1")
        assert resp.status == 502 and "secret" not in await resp.text()
    assert hits == ["videos/vid_1"]
