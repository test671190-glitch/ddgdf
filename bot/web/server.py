"""Бэкенд сайта: страницы, каталог, вход через Telegram-бота, оплата криптой и прокси к API.

Запускается в одном процессе с ботом (общая база и экземпляр Bot).
Чат на сайте ходит в API моделей через этот сервер с ключом пользователя из пула,
поэтому ключ не нужно вводить в браузере.
"""

import contextlib
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import aiohttp
from aiohttp import web

from bot.catalog import (
    FAQ,
    IMAGE_MODELS,
    REASONING,
    SHOP_NAME,
    TARIFFS,
    TARIFFS_ON_SALE,
    TEXT_MODELS,
    VIDEO_MODELS,
)
from bot.config import Settings
from bot.db import Database, utcnow
from bot.services.access import shown_key
from bot.services.crypto import CryptoPayments
from bot.services.cryptopay import CryptoPayError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
DOCS_FILE = ROOT / "docs" / "dist" / "index.html"
SESSION_COOKIE = "us_session"
# Привязка входа к браузеру, который его начал: без неё злоумышленник подтверждает вход
# своим аккаунтом и подсовывает жертве ссылку на проверку (login CSRF)
LOGIN_COOKIE = "us_login"
LOGIN_PATH = "/api/auth/login/"
SESSION_DAYS = 30
SESSION_TTL = timedelta(days=SESSION_DAYS)
LOGIN_TTL = timedelta(minutes=5)
MAX_BODY = 25 * 1024 * 1024  # картинки в чате приходят data URL
# Заголовки ответа API, которые стоит отдать браузеру
PASS_HEADERS = ("X-Plan-Limit", "X-Plan-Remaining", "X-Plan-Reset")
POPULAR_TARIFF = "pro"
# ID видео — один сегмент пути. Без этой проверки «..%2F..» уводит запрос с ключом
# провайдера на любой адрес провайдера (как и в gateway.is_allowed_path)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._:-]*")
STREAM_BROKEN = (
    "data: "
    + json.dumps({"error": {"code": "upstream_error", "message": "Сервис моделей оборвал ответ"}})
    + "\n\n"
).encode()
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def api_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def catalog_payload(settings: Settings, bot_username: str) -> dict:
    models = []
    for kind, items in (("text", TEXT_MODELS), ("image", IMAGE_MODELS), ("video", VIDEO_MODELS)):
        for m in items:
            levels, default = REASONING.get(m.id, (None, None))
            models.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "vendor": m.vendor,
                    "type": kind,
                    "reasoning": list(levels) if levels else None,
                    "default_reasoning": default,
                }
            )
    tariffs = [
        {
            "code": t.code,
            "title": t.title,
            "emoji": t.emoji,
            "tagline": t.tagline,
            "price_usd": t.price_usd,
            "stars": t.stars(settings.stars_per_usd),
            "features": list(t.features),
            "popular": t.code == POPULAR_TARIFF,
        }
        for t in TARIFFS_ON_SALE.values()
    ]
    return {
        "shop_name": SHOP_NAME,
        "bot_username": bot_username,
        # Адрес, с которым работает ключ из /api/me: наш эндпоинт, если он включён
        "api_base_url": settings.client_api_url,
        "subscription_days": settings.subscription_days,
        "models": models,
        "tariffs": tariffs,
        "faq": [{"q": q, "a": a} for q, a in FAQ],
    }


class WebApp:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        crypto: CryptoPayments | None,
        bot_username: str,
    ) -> None:
        self.db = db
        self.settings = settings
        self.crypto = crypto
        self.bot_username = bot_username
        self._http: aiohttp.ClientSession | None = None

    # --- сборка приложения ---

    def build(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY, middlewares=[self._errors])
        app.on_response_prepare.append(_security_headers)
        r = app.router
        r.add_get("/", self._page("index.html"))
        r.add_get("/app", self._page("app.html"))
        r.add_get("/docs", self.docs)
        r.add_get("/assets/{path:.+}", self.asset)
        r.add_get("/api/catalog", self.catalog)
        r.add_post("/api/auth/login", self.login_start)
        r.add_get("/api/auth/login/{token}", self.login_status)
        r.add_post("/api/auth/logout", self.logout)
        r.add_get("/api/me", self.me)
        r.add_post("/api/pay/crypto", self.pay_crypto)
        # До 18 цифр: большее число не влезает в INTEGER SQLite и роняло запрос с 500
        r.add_get("/api/pay/crypto/{order_id:\\d{1,18}}", self.pay_crypto_status)
        r.add_post("/api/chat/completions", self._proxy("POST", "/chat/completions"))
        r.add_post("/api/images/generations", self._proxy("POST", "/images/generations"))
        r.add_post("/api/videos", self._proxy("POST", "/videos"))
        r.add_get("/api/videos/{video_id}", self.video_status)
        app.on_cleanup.append(self._close)
        return app

    async def _close(self, _: web.Application) -> None:
        if self._http is not None:
            await self._http.close()

    @web.middleware
    async def _errors(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException as e:
            if request.path.startswith("/api/"):
                return api_error(
                    e.status, "not_found" if e.status == 404 else "http_error", e.reason
                )
            raise
        except Exception:
            logger.exception("Ошибка сайта: %s %s", request.method, request.path)
            return api_error(500, "server_error", "Внутренняя ошибка, попробуйте ещё раз")

    # --- страницы и файлы ---

    def _page(self, name: str) -> Handler:
        async def handler(_: web.Request) -> web.StreamResponse:
            path = WEB_DIR / name
            if not path.is_file():
                return web.Response(status=503, text="Сайт ещё не собран: нет web/" + name)
            return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

        return handler

    async def docs(self, _: web.Request) -> web.StreamResponse:
        if not DOCS_FILE.is_file():
            return web.Response(status=404, text="Соберите docs: python scripts/build_docs.py")
        return web.FileResponse(DOCS_FILE)

    async def asset(self, request: web.Request) -> web.StreamResponse:
        base = (WEB_DIR / "assets").resolve()
        path = (base / request.match_info["path"]).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    # --- каталог ---

    async def catalog(self, _: web.Request) -> web.Response:
        return web.json_response(catalog_payload(self.settings, self.bot_username))

    # --- вход через бота ---

    async def _user_id(self, request: web.Request) -> int | None:
        session_id = request.cookies.get(SESSION_COOKIE)
        if not session_id:
            return None
        return await self.db.web_session_user(session_id, SESSION_TTL)

    async def login_start(self, request: web.Request) -> web.Response:
        token = secrets.token_urlsafe(16)
        await self.db.create_web_login(token, LOGIN_TTL)
        response = web.json_response(
            {
                "token": token,
                "url": f"https://t.me/{self.bot_username}?start=login_{token}",
                "expires_in": int(LOGIN_TTL.total_seconds()),
            }
        )
        # Путь cookie — адрес проверки этого токена: входы из разных вкладок не мешают друг другу
        response.set_cookie(
            LOGIN_COOKIE,
            token,
            max_age=int(LOGIN_TTL.total_seconds()),
            httponly=True,
            samesite="Strict",
            secure=self.settings.web_secure_cookies or _is_https(request),
            path=LOGIN_PATH + token,
        )
        return response

    async def login_status(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        cookie = request.cookies.get(LOGIN_COOKIE, "")
        if not secrets.compare_digest(cookie.encode(), token.encode()):
            # Вход начат в другом браузере: для этого браузера ссылка недействительна
            return web.json_response({"status": "expired"})
        status, _ = await self.db.web_login_status(token)
        if status != "confirmed":
            return web.json_response({"status": status})
        user_id = await self.db.consume_web_login(token)
        if user_id is None:
            return web.json_response({"status": "expired"})
        session_id = secrets.token_urlsafe(32)
        await self.db.create_web_session(session_id, user_id, SESSION_TTL)
        response = web.json_response({"status": "confirmed", "user": await self._user(user_id)})
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=int(SESSION_TTL.total_seconds()),
            httponly=True,
            samesite="Lax",
            secure=self.settings.web_secure_cookies or _is_https(request),
            path="/",
        )
        response.del_cookie(LOGIN_COOKIE, path=LOGIN_PATH + token)
        return response

    async def logout(self, request: web.Request) -> web.Response:
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            await self.db.delete_web_session(session_id)
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def _user(self, user_id: int) -> dict:
        row = await self.db.get_user(user_id)
        return {
            "id": user_id,
            "first_name": (row["full_name"] if row else None) or "Пользователь",
            "username": row["username"] if row else None,
        }

    async def me(self, request: web.Request) -> web.Response:
        user_id = await self._user_id(request)
        if user_id is None:
            return api_error(401, "unauthorized", "Войдите через Telegram")
        sub = await self.db.get_subscription(user_id)
        upstream = await self.db.get_active_key(user_id) if sub and sub.is_active else None
        key = await shown_key(self.db, self.settings, user_id, upstream.key if upstream else None)
        subscription = None
        if sub:
            tariff = TARIFFS.get(sub.tariff)
            subscription = {
                "tariff": sub.tariff,
                "title": tariff.title if tariff else sub.tariff,
                "expires_at": sub.expires_at.isoformat(),
                "days_left": max(0, (sub.expires_at - utcnow()).days),
                "active": sub.is_active,
            }
        return web.json_response(
            {
                "user": await self._user(user_id),
                "subscription": subscription,
                "api_key": key,
            }
        )

    # --- оплата криптой ---

    async def pay_crypto(self, request: web.Request) -> web.Response:
        user_id = await self._user_id(request)
        if user_id is None:
            return api_error(401, "unauthorized", "Войдите через Telegram")
        if self.crypto is None:
            return api_error(503, "crypto_disabled", "Оплата криптой сейчас недоступна")
        body = await _json_body(request)
        tariff = TARIFFS_ON_SALE.get(str(body.get("tariff", "")))
        if tariff is None:
            return api_error(400, "invalid_request", "Неизвестный тариф")
        try:
            order = await self.crypto.get_or_create(user_id, tariff)
        except CryptoPayError as e:
            logger.error("Crypto Pay: %s", e)
            return api_error(502, "payment_error", "Не получилось создать счёт, попробуйте позже")
        return web.json_response(
            {"order_id": order.id, "pay_url": order.pay_url, "amount_usd": tariff.price_usd}
        )

    async def pay_crypto_status(self, request: web.Request) -> web.Response:
        user_id = await self._user_id(request)
        if user_id is None:
            return api_error(401, "unauthorized", "Войдите через Telegram")
        order = await self.db.get_crypto_order(int(request.match_info["order_id"]))
        if order is None or order.user_id != user_id:
            return api_error(404, "not_found", "Заказ не найден")
        status = order.status
        if self.crypto is not None and status == "pending":
            try:
                status = await self.crypto.check(order)
            except CryptoPayError as e:
                logger.warning("Crypto Pay: %s", e)
        public = {"approved": "paid", "pending": "pending"}.get(status, "expired")
        return web.json_response({"status": public})

    # --- прокси к API моделей ---

    async def _api_key(self, request: web.Request) -> str | web.Response:
        user_id = await self._user_id(request)
        if user_id is None:
            return api_error(401, "unauthorized", "Войдите через Telegram")
        sub = await self.db.get_subscription(user_id)
        if sub is None or not sub.is_active:
            return api_error(402, "subscription_required", "Нужна активная подписка")
        key = await self.db.get_active_key(user_id)
        if key is None:
            return api_error(402, "no_api_key", "Ключ ещё не выдан, мы пришлём его в бот")
        return key.key

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=600)
            )
        return self._http

    def _proxy(self, method: str, path: str) -> Handler:
        async def handler(request: web.Request) -> web.StreamResponse:
            if request.content_type != "application/json":
                return api_error(415, "invalid_request", "Ожидается JSON")
            key = await self._api_key(request)
            if isinstance(key, web.Response):
                return key
            return await self._forward(request, method, path, key, await request.read())

        return handler

    async def video_status(self, request: web.Request) -> web.StreamResponse:
        key = await self._api_key(request)
        if isinstance(key, web.Response):
            return key
        video_id = request.match_info["video_id"]
        if not VIDEO_ID.fullmatch(video_id):
            return api_error(404, "not_found", "Видео не найдено")
        return await self._forward(request, "GET", f"/videos/{video_id}", key, None)

    async def _forward(
        self, request: web.Request, method: str, path: str, key: str, body: bytes | None
    ) -> web.StreamResponse:
        url = self.settings.api_base_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with self._session().request(method, url, data=body, headers=headers) as up:
                if up.status == 401:
                    # Провайдер отклонил ключ из пула. Отдать 401 нельзя: сайт решит,
                    # что истекла сессия, и попросит войти заново (как в gateway.py)
                    logger.error("Ключ провайдера отклонён (%s) на %s", up.status, path)
                    return api_error(
                        503,
                        "provider_key_error",
                        "Доступ временно недоступен, мы уже исправляем. Напишите в поддержку, "
                        "если ошибка не пропадёт",
                    )
                extra = {h: up.headers[h] for h in PASS_HEADERS if h in up.headers}
                if up.content_type == "text/event-stream":
                    response = web.StreamResponse(
                        status=up.status,
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                            **extra,
                        },
                    )
                    await response.prepare(request)
                    try:
                        async for chunk in up.content.iter_any():
                            await response.write(chunk)
                    except ConnectionResetError:
                        logger.info("Клиент закрыл поток %s", path)
                        return response
                    except (aiohttp.ClientError, TimeoutError) as e:
                        # Ответ уже начат, статус ошибки не отправить. Сообщаем об обрыве
                        # событием в потоке: чат покажет его как ошибку, а не как готовый ответ
                        logger.warning("Провайдер оборвал поток %s: %s", path, e)
                        with contextlib.suppress(ConnectionResetError):
                            await response.write(STREAM_BROKEN)
                        return response
                    await response.write_eof()
                    return response
                data = await up.read()
                return web.Response(
                    status=up.status,
                    body=data,
                    content_type=up.content_type or "application/json",
                    headers=extra,
                )
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("API моделей недоступен: %s", e)
            return api_error(
                502, "upstream_error", "Сервис моделей не отвечает, попробуйте ещё раз"
            )


async def _security_headers(_: web.Request, response: web.StreamResponse) -> None:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)


def _is_https(request: web.Request) -> bool:
    """Сервер слушает 127.0.0.1 за Caddy: о HTTPS он узнаёт из X-Forwarded-Proto.
    Подделка заголовка лишь делает cookie строже, поэтому ему можно верить."""
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


async def _json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
