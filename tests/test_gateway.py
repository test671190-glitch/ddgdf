"""Собственный эндпоинт: проверка ключа, подписки и прозрачная пересылка к провайдеру."""

import asyncio
import time
from datetime import timedelta

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot.gateway as gateway_module
from bot.config import Settings
from bot.db import Database, _iso, utcnow
from bot.gateway import Gateway

seen: list[dict] = []
alerts: list[str] = []


async def fake_upstream(request: web.Request) -> web.StreamResponse:
    """Имитирует провайдера: запоминает запрос, отвечает JSON или потоком SSE."""
    body = await request.read()
    seen.append(
        {
            "path": request.match_info["tail"],
            "method": request.method,
            "auth": request.headers.get("Authorization"),
            "x_api_key": request.headers.get("x-api-key"),
            "anthropic_version": request.headers.get("anthropic-version"),
            "accept_encoding": request.headers.get("Accept-Encoding"),
            "query": dict(request.query),
            "body": body,
        }
    )
    if request.headers.get("Authorization") == "Bearer up-lapsed":
        return web.json_response({"error": {"code": "subscription_required"}}, status=402)
    if request.headers.get("Authorization") != "Bearer up-pro":
        return web.json_response({"error": {"code": "invalid_api_key"}}, status=401)
    if b'"stream": true' in body:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response(
        {"ok": True},
        status=200,
        headers={"X-Plan-Remaining": "7", "openai-organization": "org-shop", "Via": "1.1 provider"},
    )


@pytest.fixture
async def env():
    seen.clear()
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/api/v1/{tail:.*}", fake_upstream)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    settings = Settings(
        bot_token="1:x",
        api_base_url=str(upstream.make_url("/api/v1")),
        public_api_url="https://154-83-140-29.sslip.io/v1",
    )
    alerts.clear()

    async def alert(text: str) -> None:
        alerts.append(text)

    client = TestClient(TestServer(Gateway(db, settings, alert=alert).build()))
    await client.start_server()
    yield client, db
    await client.close()
    await upstream.close()
    await db.close()


async def subscriber(db: Database, user_id: int = 42) -> str:
    await db.add_keys("pro", ["up-pro"])
    await db.grant(user_id, "pro", 30)
    return await db.get_or_create_client_key(user_id)


async def test_rejects_missing_or_unknown_key(env):
    client, _ = env
    for headers in ({}, {"Authorization": "Bearer sk-us-nope"}, {"Authorization": "Bearer up-pro"}):
        resp = await client.post("/v1/chat/completions", json={}, headers=headers)
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "invalid_api_key"
    assert seen == []


async def test_forwards_with_upstream_key(env):
    client, db = env
    key = await subscriber(db)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-6-astra", "messages": []},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status == 200
    assert await resp.json() == {"ok": True}
    assert resp.headers["X-Plan-Remaining"] == "7"
    assert seen[-1]["path"] == "chat/completions"
    assert seen[-1]["auth"] == "Bearer up-pro"
    assert key.encode() not in seen[-1]["body"]


async def test_anthropic_style_x_api_key(env):
    client, db = env
    key = await subscriber(db)
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-opus-5-5", "max_tokens": 10, "messages": []},
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    assert resp.status == 200
    assert seen[-1]["path"] == "messages"
    assert seen[-1]["x_api_key"] == "up-pro"
    assert seen[-1]["anthropic_version"] == "2023-06-01"


async def test_streams_sse(env):
    client, db = env
    key = await subscriber(db)
    resp = await client.post(
        "/v1/chat/completions",
        data=b'{"model": "x", "stream": true, "messages": []}',
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    text = (await resp.read()).decode()
    assert text.endswith("data: [DONE]\n\n")


async def test_get_with_query(env):
    client, db = env
    key = await subscriber(db)
    resp = await client.get(
        "/v1/videos/vid_1?verbose=1", headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status == 200
    assert seen[-1]["path"] == "videos/vid_1"
    assert seen[-1]["method"] == "GET"
    assert seen[-1]["query"] == {"verbose": "1"}


async def test_expired_subscription_is_402(env):
    client, db = env
    key = await subscriber(db)
    await db.conn.execute(
        "UPDATE subscriptions SET expires_at=? WHERE user_id=42",
        (_iso(utcnow() - timedelta(minutes=1)),),
    )
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 402
    assert (await resp.json())["error"]["code"] == "subscription_required"


async def test_no_upstream_key_yet_is_503(env):
    client, db = env
    await db.grant(7, "pro", 30)  # пул пуст
    key = await db.get_or_create_client_key(7)
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 503
    assert (await resp.json())["error"]["code"] == "key_pending"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/models/%2e%2e/%2e%2e/account",
        "/v1/account/settings",
        "/v1/chat//completions",
        "/v1/",
    ],
)
async def test_blocks_paths_outside_model_api(env, path):
    client, db = env
    key = await subscriber(db)
    resp = await client.get(path, headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 404
    assert seen == []


async def test_compression_only_when_client_asks(env):
    client, db = env
    key = await subscriber(db)
    auth = {"Authorization": f"Bearer {key}"}
    await client.get("/v1/models", headers={**auth, "Accept-Encoding": "identity"})
    assert seen[-1]["accept_encoding"] == "identity"
    await client.get("/v1/models", headers={**auth, "Accept-Encoding": "gzip"})
    assert seen[-1]["accept_encoding"] == "gzip"


async def test_broken_pool_key_is_503_and_alerts_once(env):
    client, db = env
    await db.add_keys("pro", ["up-broken"])
    await db.grant(9, "pro", 30)
    key = await db.get_or_create_client_key(9)
    for _ in range(2):
        resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        assert resp.status == 503
        assert (await resp.json())["error"]["code"] == "provider_key_error"
    assert len(alerts) == 1 and "/givekey 9" in alerts[0]


async def test_provider_402_is_our_problem_not_clients(env):
    """У аккаунта провайдера кончился тариф: клиент не должен видеть «нет подписки»."""
    client, db = env
    await db.add_keys("pro", ["up-lapsed"])
    await db.grant(11, "pro", 30)
    key = await db.get_or_create_client_key(11)
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 503
    assert (await resp.json())["error"]["code"] == "provider_key_error"
    assert len(alerts) == 1 and "HTTP 402" in alerts[0]


async def test_empty_bearer_does_not_hide_x_api_key(env):
    client, db = env
    key = await subscriber(db)
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer ", "x-api-key": key})
    assert resp.status == 200


async def test_rotated_key_stops_working(env):
    client, db = env
    old = await subscriber(db)
    new = await db.rotate_client_key(42)
    assert new != old and new.startswith("sk-us-")
    assert (
        await client.get("/v1/models", headers={"Authorization": f"Bearer {old}"})
    ).status == 401
    assert (
        await client.get("/v1/models", headers={"Authorization": f"Bearer {new}"})
    ).status == 200


async def test_redirect_to_other_host_does_not_carry_provider_key(env):
    """Провайдер отвечает 302 на чужой хост (видео на CDN). aiohttp при таком переходе убирает
    только Authorization, и x-api-key с ключом провайдера уходил постороннему серверу.
    Редирект на свой же адрес провайдера по-прежнему идёт с ключом."""
    client, db = env
    cdn_seen: list[dict] = []
    provider_seen: list[dict] = []

    async def cdn(request: web.Request) -> web.Response:
        cdn_seen.append(dict(request.headers))
        return web.Response(body=b"video")

    cdn_app = web.Application()
    cdn_app.router.add_get("/{tail:.*}", cdn)
    cdn_server = TestServer(cdn_app)
    await cdn_server.start_server()

    async def provider(request: web.Request) -> web.Response:
        provider_seen.append(dict(request.headers))
        tail = request.match_info["tail"]
        if tail == "videos/vid_1/content":
            raise web.HTTPFound(str(cdn_server.make_url("/file.mp4")))
        if tail == "models/old":
            raise web.HTTPMovedPermanently("/api/v1/models")
        return web.json_response({"data": []})

    provider_app = web.Application()
    provider_app.router.add_get("/api/v1/{tail:.*}", provider)
    provider_server = TestServer(provider_app)
    await provider_server.start_server()
    gateway = Gateway(
        db,
        Settings(bot_token="1:x", api_base_url=str(provider_server.make_url("/api/v1"))),
    )
    gateway.allow_private_redirects = True  # «CDN» в тесте — localhost по http
    gw_client = TestClient(TestServer(gateway.build()))
    await gw_client.start_server()
    try:
        key = await subscriber(db)
        auth = {"Authorization": f"Bearer {key}"}
        resp = await gw_client.get("/v1/videos/vid_1/content", headers=auth)
        assert resp.status == 200 and await resp.read() == b"video"
        leaked = [h for h in cdn_seen[-1] if h.lower() in ("authorization", "x-api-key")]
        assert leaked == []

        resp = await gw_client.get("/v1/models/old", headers=auth)
        assert resp.status == 200
        assert provider_seen[-1]["x-api-key"] == "up-pro"
    finally:
        await gw_client.close()
        await provider_server.close()
        await cdn_server.close()


@pytest.fixture
async def broken_upstream():
    """Провайдер, который обещает 1000 байт JSON, отдаёт 40 и рвёт соединение."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                await reader.readexactly(int(line.split(b":", 1)[1]))
        if b"stream" in head.split(b"\r\n", 1)[0]:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            chunk = b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            writer.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        else:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 1000\r\n\r\n"
                b'{"choices":[{"message":{"content":"trunc'
            )
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.transport.abort()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/api/v1"
    server.close()


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/chat/completions?stream=1"])
async def test_upstream_cut_mid_response_is_visible_to_client(broken_upstream, path):
    """Провайдер оборвал ответ на середине. Раньше клиент получал корректно завершённый
    ответ 200 с обрезанным JSON или потоком без [DONE] и считал его полным."""
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    gateway = Gateway(db, Settings(bot_token="1:x", api_base_url=broken_upstream))
    client = TestClient(TestServer(gateway.build()))
    await client.start_server()
    try:
        key = await subscriber(db)
        resp = await client.post(path, json={}, headers={"Authorization": f"Bearer {key}"})
        with pytest.raises(aiohttp.ClientPayloadError):
            await resp.read()
    finally:
        await client.close()
        await db.close()


async def test_slow_admin_alert_does_not_delay_client(env):
    """Алерт админам уходит через Telegram и может идти секундами — клиент не ждёт его."""
    client, db = env
    gateway = next(
        r.handler.__self__
        for r in client.server.app.router.routes()
        if isinstance(getattr(r.handler, "__self__", None), Gateway)
    )
    sent = asyncio.Event()

    async def slow_alert(text: str) -> None:
        await asyncio.sleep(2)
        sent.set()

    gateway.alert = slow_alert
    await db.add_keys("pro", ["up-broken"])
    await db.grant(9, "pro", 30)
    key = await db.get_or_create_client_key(9)
    started = time.monotonic()
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 503
    assert time.monotonic() - started < 1
    await asyncio.wait_for(sent.wait(), 5)  # алерт всё равно доставлен


async def test_framework_errors_are_openai_json(env, monkeypatch):
    """404 на /v1 и 413 на слишком большое тело приходили text/plain, а документация
    обещает тело ошибки в формате OpenAI."""
    client, db = env
    resp = await client.get("/v1")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "not_found"

    monkeypatch.setattr(gateway_module, "MAX_BODY", 10)
    small = TestClient(TestServer(Gateway(db, Settings(bot_token="1:x")).build()))
    await small.start_server()
    try:
        key = await subscriber(db)
        resp = await small.post(
            "/v1/chat/completions", data=b"x" * 100, headers={"Authorization": f"Bearer {key}"}
        )
        assert resp.status == 413
        assert (await resp.json())["error"]["code"] == "request_too_large"
    finally:
        await small.close()


@pytest.fixture
async def slow_provider(env):
    """Провайдер, который держит каждый запрос, пока тест его не отпустит."""
    _, db = env
    state = {"in_flight": 0, "release": asyncio.Event()}

    async def slow(request: web.Request) -> web.Response:
        state["in_flight"] += 1
        await state["release"].wait()
        return web.json_response({"ok": True})

    slow_app = web.Application()
    slow_app.router.add_route("*", "/api/v1/{tail:.*}", slow)
    slow_server = TestServer(slow_app)
    await slow_server.start_server()
    gateway = Gateway(
        db, Settings(bot_token="1:x", api_base_url=str(slow_server.make_url("/api/v1")))
    )
    gw = TestServer(gateway.build())
    await gw.start_server()
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0))

    async def client_key(user_id: int) -> dict[str, str]:
        await db.add_keys("pro", [f"up-{user_id}"])
        await db.grant(user_id, "pro", 30)
        key = await db.get_or_create_client_key(user_id)
        # у каждого клиента свой адрес (за Caddy): лимит запросов в секунду считается по адресу
        return {"Authorization": f"Bearer {key}", "X-Forwarded-For": f"203.0.113.{user_id}"}

    async def wait_in_flight(count: int) -> None:
        for _ in range(200):
            if state["in_flight"] >= count:
                return
            await asyncio.sleep(0.02)

    yield gw, session, state, client_key, wait_in_flight
    state["release"].set()
    await session.close()
    await gw.close()
    await slow_server.close()


async def test_many_slow_requests_do_not_block_other_clients(slow_provider):
    """Сто двадцать долгих запросов занимали все 100 соединений к провайдеру,
    и остальные клиенты висели без ответа."""
    gw, session, state, client_key, wait_in_flight = slow_provider
    url = gw.make_url("/v1/chat/completions")
    hogs = []
    for user_id in range(1, 7):  # шесть клиентов по 20 запросов
        auth = await client_key(user_id)
        hogs += [asyncio.create_task(session.post(url, json={}, headers=auth)) for _ in range(20)]
    await wait_in_flight(120)
    assert state["in_flight"] == 120
    victim = asyncio.create_task(
        session.get(gw.make_url("/v1/models"), headers=await client_key(7))
    )
    await wait_in_flight(121)
    assert state["in_flight"] == 121  # запрос другого клиента дошёл до провайдера
    state["release"].set()
    for resp in await asyncio.gather(*hogs, victim):
        assert resp.status == 200
        resp.release()


async def test_one_client_cannot_take_all_connections(slow_provider):
    """Без лимита на клиента один ключ мог занять все соединения к провайдеру."""
    gw, session, state, client_key, wait_in_flight = slow_provider
    auth = await client_key(1)
    url = gw.make_url("/v1/chat/completions")
    tasks = [asyncio.create_task(session.post(url, json={}, headers=auth)) for _ in range(25)]
    await wait_in_flight(gateway_module.MAX_CONCURRENT_PER_CLIENT)
    await asyncio.sleep(0.2)
    assert state["in_flight"] == gateway_module.MAX_CONCURRENT_PER_CLIENT
    state["release"].set()
    responses = await asyncio.gather(*tasks)
    statuses = sorted(r.status for r in responses)
    assert statuses == [200] * 20 + [429] * 5
    limited = next(r for r in responses if r.status == 429)
    assert (await limited.json())["error"]["code"] == "too_many_concurrent_requests"
    for resp in responses:
        resp.release()
    # ответы пришли — лимит освободился
    again = await session.post(url, json={}, headers=auth)
    assert again.status == 200
    again.release()


@pytest.fixture
async def redirecting():
    """Провайдер и CDN с редиректами между ними: битый Location, петля, возврат на провайдера."""
    ports: dict = {}
    alerts: list[str] = []
    cookies_seen: list[str | None] = []

    async def needauth(request: web.Request) -> web.Response:
        if "Authorization" not in request.headers:
            return web.json_response({"error": "no key"}, status=401)
        return web.json_response({"ok": True})

    async def badloc(request: web.Request) -> web.Response:
        return web.Response(status=302, headers={"Location": "http://[bad"})

    async def loop(request: web.Request) -> web.Response:
        location = f"http://localhost:{ports['up']}/api/v1/models/loop?sig=secret"
        return web.Response(status=302, headers={"Location": location})

    async def tocdn(request: web.Request) -> web.Response:
        return web.Response(
            status=302, headers={"Location": f"http://127.0.0.1:{ports['cdn']}/hop"}
        )

    async def cookie(request: web.Request) -> web.Response:
        cookies_seen.append(request.headers.get("Cookie"))
        response = web.json_response({"ok": True})
        response.set_cookie("provider_session", "client-a-secret")
        return response

    async def hop(request: web.Request) -> web.Response:
        location = f"http://localhost:{ports['up']}/api/v1/models/needauth"
        return web.Response(status=302, headers={"Location": location})

    up_app = web.Application()
    for name, handler in [
        ("needauth", needauth),
        ("badloc", badloc),
        ("loop", loop),
        ("tocdn", tocdn),
        ("cookie", cookie),
    ]:
        up_app.router.add_route("*", f"/api/v1/models/{name}", handler)
    cdn_app = web.Application()
    cdn_app.router.add_get("/hop", hop)
    up, cdn = TestServer(up_app), TestServer(cdn_app)
    await up.start_server()
    await cdn.start_server()
    ports["up"], ports["cdn"] = up.port, cdn.port

    db = Database(":memory:", hide_keys=True)
    await db.connect()
    await db.add_keys("pro", ["UPKEY"])
    await db.grant(1, "pro", 30)
    key = await db.get_or_create_client_key(1)

    async def alert(text: str) -> None:
        alerts.append(text)

    gateway = Gateway(
        db,
        # localhost, а не 127.0.0.1: для IP-адресов aiohttp cookies не хранит вовсе
        Settings(bot_token="1:x", api_base_url=f"http://localhost:{up.port}/api/v1"),
        alert=alert,
    )
    gateway.allow_private_redirects = True  # «CDN» в тесте — localhost по http
    client = TestClient(TestServer(gateway.build()))
    await client.start_server()
    yield client, {"Authorization": f"Bearer {key}"}, alerts, cookies_seen
    await client.close()
    await up.close()
    await cdn.close()
    await db.close()


async def test_malformed_redirect_is_502_json(redirecting):
    """Битый Location у провайдера давал 500 text/plain, который SDK клиента не разберёт."""
    client, auth, *_ = redirecting
    resp = await client.get("/v1/models/badloc", headers=auth)
    assert resp.status == 502
    assert (await resp.json())["error"]["code"] == "upstream_error"


async def test_redirect_loop_does_not_expose_provider_address(redirecting):
    """После пяти редиректов клиенту уходил сырой 302 с адресом и подписью провайдера."""
    client, auth, *_ = redirecting
    resp = await client.get("/v1/models/loop", headers=auth, allow_redirects=False)
    assert resp.status == 502
    assert "Location" not in resp.headers


async def test_cdn_bouncing_back_to_provider_is_not_bad_key_alert(redirecting):
    """Провайдер → CDN → провайдер: ключ мы сняли сами на чужом хосте. 401 провайдера тогда
    не значит, что ключ сломан, — админ не должен получать ложную тревогу."""
    client, auth, alerts, _ = redirecting
    resp = await client.get("/v1/models/tocdn", headers=auth)
    await asyncio.sleep(0.05)
    assert resp.status == 401
    assert alerts == []


async def test_provider_cookies_are_not_shared_between_clients(redirecting):
    """Сессия к провайдеру общая на всех: cookie, выданная на запрос одного клиента,
    уходила с запросами следующих."""
    client, auth, _, cookies_seen = redirecting
    for _ in range(2):
        assert (await client.get("/v1/models/cookie", headers=auth)).status == 200
    assert cookies_seen == [None, None]


async def test_requests_per_ip_are_limited(env, monkeypatch):
    """Флуд с одного адреса (в том числе с выдуманными ключами) отсекается до проверки
    ключа; другие адреса за прокси Caddy при этом работают."""
    client, db = env
    gateway = next(
        r.handler.__self__
        for r in client.server.app.router.routes()
        if isinstance(getattr(r.handler, "__self__", None), Gateway)
    )
    gateway._per_ip = gateway_module.RateLimiter(3)
    key = await subscriber(db)
    auth = {"Authorization": f"Bearer {key}", "X-Forwarded-For": "203.0.113.7"}
    statuses = [(await client.get("/v1/models", headers=auth)).status for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    resp = await client.get("/v1/models", headers=auth)
    assert (await resp.json())["error"]["code"] == "rate_limited"
    other = {"Authorization": f"Bearer {key}", "X-Forwarded-For": "198.51.100.1"}
    assert (await client.get("/v1/models", headers=other)).status == 200


def test_client_ip_trusts_forwarded_only_from_local_proxy():
    from types import SimpleNamespace

    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.7"}
    via_caddy = SimpleNamespace(remote="127.0.0.1", headers=headers)
    assert gateway_module.client_ip(via_caddy) == "203.0.113.7"
    # напрямую, мимо Caddy: подделанному заголовку не верим
    direct = SimpleNamespace(remote="198.51.100.9", headers=headers)
    assert gateway_module.client_ip(direct) == "198.51.100.9"


def test_rate_limiter_refills():
    limiter = gateway_module.RateLimiter(2)
    assert [limiter.allow("a") for _ in range(3)] == [True, True, False]
    assert limiter.allow("b")


async def test_redirect_to_internal_address_is_refused(env):
    """Провайдер (или тот, кто подменил его ответ) редиректит на localhost: раньше эндпоинт
    шёл туда POST-ом с телом клиента — например, в админку Caddy на 127.0.0.1:2019."""
    client, db = env
    internal_hits: list[str] = []

    async def internal(request: web.Request) -> web.Response:
        internal_hits.append(request.path)
        return web.json_response({"internal": True})

    internal_app = web.Application()
    internal_app.router.add_route("*", "/{tail:.*}", internal)
    internal_server = TestServer(internal_app)
    await internal_server.start_server()

    async def evil(request: web.Request) -> web.Response:
        location = str(internal_server.make_url("/load"))
        return web.Response(status=307, headers={"Location": location})

    evil_app = web.Application()
    evil_app.router.add_route("*", "/api/v1/{tail:.*}", evil)
    evil_server = TestServer(evil_app)
    await evil_server.start_server()
    gateway = Gateway(
        db, Settings(bot_token="1:x", api_base_url=str(evil_server.make_url("/api/v1")))
    )
    gw_client = TestClient(TestServer(gateway.build()))
    await gw_client.start_server()
    try:
        key = await subscriber(db)
        resp = await gw_client.post(
            "/v1/chat/completions", json={"x": 1}, headers={"Authorization": f"Bearer {key}"}
        )
        assert resp.status == 502
        assert internal_hits == []
    finally:
        await gw_client.close()
        await evil_server.close()
        await internal_server.close()


async def test_same_host_redirect_to_other_path_keeps_key_away(env):
    """307 на /api/v1/dashboard/billing у самого провайдера увёл бы туда ключ провайдера."""
    client, db = env
    seen_paths: list[str] = []

    async def provider(request: web.Request) -> web.Response:
        seen_paths.append(request.path)
        if request.path.endswith("/models"):
            raise web.HTTPTemporaryRedirect("/api/v1/dashboard/billing")
        return web.json_response({"billing": "secret"})

    app = web.Application()
    app.router.add_route("*", "/api/v1/{tail:.*}", provider)
    server = TestServer(app)
    await server.start_server()
    gateway = Gateway(db, Settings(bot_token="1:x", api_base_url=str(server.make_url("/api/v1"))))
    gw_client = TestClient(TestServer(gateway.build()))
    await gw_client.start_server()
    try:
        key = await subscriber(db)
        resp = await gw_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        assert resp.status == 502
        assert seen_paths == ["/api/v1/models"]
    finally:
        await gw_client.close()
        await server.close()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/v1/responses/resp_1"),
        ("GET", "/v1/responses/resp_1"),
        ("GET", "/v1/chat/completions"),
        ("POST", "/v1/models"),
        ("GET", "/v1/messages/batches/b1/results"),
    ],
)
async def test_only_model_requests_are_forwarded(env, method, path):
    """Ключи провайдера переходят от клиента к клиенту: чтение и удаление сохранённых данных
    (ответы, батчи) дало бы новому покупателю доступ к данным прежнего."""
    client, db = env
    key = await subscriber(db)
    resp = await client.request(method, path, headers={"Authorization": f"Bearer {key}"})
    assert resp.status == 404
    assert seen == []


async def test_provider_account_headers_are_not_forwarded(env):
    """Организация и инфраструктура провайдера клиенту знать незачем."""
    client, db = env
    key = await subscriber(db)
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.headers["X-Plan-Remaining"] == "7"
    assert "openai-organization" not in resp.headers
    assert "Via" not in resp.headers


def test_ipv6_clients_are_counted_per_64_network():
    from bot.ratelimit import ip_key

    assert ip_key("2001:db8:1:2::1") == ip_key("2001:db8:1:2:ffff::9")
    assert ip_key("2001:db8:1:3::1") != ip_key("2001:db8:1:2::1")
    assert ip_key("203.0.113.7") == "203.0.113.7"


async def test_dotdot_model_id_is_not_forwarded(env):
    """«..» проходил шаблон models/...: GET /v1/models/.. уходил с ключом провайдера на корень
    его API, которого нет в ROUTES."""
    from yarl import URL

    client, db = env
    key = await subscriber(db)
    for raw in ("/v1/models/%2E%2E", "/v1/models/..", "/v1/models/.hidden"):
        seen.clear()
        url = URL(str(client.make_url("/")).rstrip("/") + raw, encoded=True)
        async with client.session.get(url, headers={"Authorization": f"Bearer {key}"}) as resp:
            assert resp.status == 404, raw
        assert seen == []
    resp = await client.get(
        "/v1/models/claude-sonnet-5.5", headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status == 200 and seen[-1]["path"] == "models/claude-sonnet-5.5"


async def test_chunked_body_waits_for_big_body_slot(slow_provider):
    """Тело без Content-Length (chunked) шло мимо лимита одновременных больших тел: один ключ
    двадцатью такими запросами по 25 МБ занимал память до MemoryMax, и процесс падал у всех."""
    gw, session, state, client_key, wait_in_flight = slow_provider
    auth = await client_key(1)
    url = gw.make_url("/v1/chat/completions")
    big = b"x" * (gateway_module.BIG_BODY + 1)
    slots = gateway_module.BIG_BODY_SLOTS
    hogs = [asyncio.create_task(session.post(url, data=big, headers=auth)) for _ in range(slots)]
    await wait_in_flight(slots)

    async def chunks():
        yield b"x" * 1024

    chunked = asyncio.create_task(session.post(url, data=chunks(), headers=auth))
    await asyncio.sleep(0.3)
    assert state["in_flight"] == slots  # ждёт свободный слот, а не идёт к провайдеру
    state["release"].set()
    for resp in await asyncio.gather(*hogs, chunked):
        assert resp.status == 200
        resp.release()
