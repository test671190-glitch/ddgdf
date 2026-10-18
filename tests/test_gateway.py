"""Собственный эндпоинт: проверка ключа, подписки и прозрачная пересылка к провайдеру."""

from datetime import timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

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
    return web.json_response({"ok": True}, status=200, headers={"X-Plan-Remaining": "7"})


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
    resp = await client.post("/v1/models", headers={"Authorization": f"Bearer {key}"})
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
