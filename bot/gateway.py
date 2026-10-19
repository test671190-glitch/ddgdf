"""Собственный эндпоинт магазина: OpenAI- и Anthropic-совместимый прокси.

Клиент шлёт запрос на https://<наш адрес>/v1/... со своим ключом sk-us-...
Мы находим владельца ключа, проверяем активную подписку, подставляем закреплённый
за ним ключ провайдера и пересылаем запрос как есть. Ответ (в том числе поток SSE)
отдаётся клиенту по мере поступления.

Наружу сервер смотрит через Caddy (HTTPS), сам слушает только 127.0.0.1.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

import aiohttp
from aiohttp import web
from yarl import URL

from bot.catalog import MAX_CONCURRENT_REQUESTS, SHOP_NAME
from bot.config import Settings
from bot.db import CLIENT_KEY_PREFIX, Database

Alert = Callable[[str], Awaitable[None]]

logger = logging.getLogger(__name__)

MAX_BODY = 50 * 1024 * 1024  # картинки-референсы приходят в base64
# Первые части путей, которые можно проксировать (/v1/<root>/...)
ALLOWED_ROOTS = {"chat", "completions", "messages", "models", "images", "videos", "responses"}
SAFE_PATH = re.compile(r"[A-Za-z0-9._\-/:]+")
BAD_KEY_ALERT_INTERVAL = 3600  # не чаще раза в час на один ключ
# Одновременных запросов к провайдеру. По умолчанию у aiohttp 100, и ожидание слота ничем
# не ограничено: сотня долгих запросов одного клиента вешала эндпоинт всем остальным
UPSTREAM_CONNECTIONS = 400
UPSTREAM_SLOT_WAIT = 60  # сколько ждать свободного соединения, прежде чем ответить 502
# Одновременных запросов на одного клиента: агентам в IDE хватает с запасом, а один ключ
# не может занять все соединения (UPSTREAM_CONNECTIONS) и оставить без ответа остальных
MAX_CONCURRENT_PER_CLIENT = MAX_CONCURRENT_REQUESTS
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
PROVIDER_AUTH_HEADERS = ("Authorization", "x-api-key")
# Заголовки запроса клиента, которые передаём провайдеру
FORWARD_REQUEST_HEADERS = (
    "content-type",
    "accept",
    "anthropic-version",
    "anthropic-beta",
    "user-agent",
    "openai-beta",
)
# Заголовки, которые нельзя пересылать через прокси
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "server",
    "date",
    "set-cookie",
}


def api_error(status: int, code: str, message: str, kind: str = "invalid_request_error"):
    return web.json_response(
        {"error": {"message": message, "type": kind, "code": code}}, status=status
    )


@web.middleware
async def json_errors(request: web.Request, handler) -> web.StreamResponse:
    """Ошибки самого aiohttp (413 — тело больше лимита, 404 на /v1, 405) отдаём в формате
    OpenAI, как обещает документация: SDK клиентов разбирают именно его."""
    try:
        return await handler(request)
    except web.HTTPException as e:
        if e.status < 400:
            raise
        if e.status == 404:
            return api_error(404, "not_found", "Такого эндпоинта нет")
        if e.status == 413:
            return api_error(413, "request_too_large", "Тело запроса больше 50 МБ")
        return api_error(e.status, f"http_{e.status}", e.reason)


def is_allowed_path(path: str) -> bool:
    """Пропускаем только эндпоинты моделей и без «..»: иначе через наш ключ провайдера
    можно было бы дотянуться до других адресов провайдера (настройки аккаунта и т. п.)."""
    if not SAFE_PATH.fullmatch(path):
        return False
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return False
    return segments[0] in ALLOWED_ROOTS


def client_key_from(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("x-api-key", "").strip() or None


class Gateway:
    def __init__(self, db: Database, settings: Settings, alert: Alert | None = None) -> None:
        self.db = db
        self.settings = settings
        self.alert = alert  # уведомление админам (в боте — notify_admins)
        self._http: aiohttp.ClientSession | None = None
        self._alerted: dict[str, float] = {}
        self._alerts: set[asyncio.Task] = set()  # ссылки, чтобы задачи не собрал GC
        self._in_flight: dict[int, int] = {}  # user_id -> запросов к провайдеру прямо сейчас

    def _report_bad_upstream_key(self, user_id: int, key: str, status: int) -> None:
        logger.error("Ключ провайдера пользователя %s отклонён (%s)", user_id, status)
        now = time.monotonic()
        if self.alert is None or now - self._alerted.get(key, -1e9) < BAD_KEY_ALERT_INTERVAL:
            return
        self._alerted[key] = now
        # В фоне: рассылка админам идёт через Telegram и может занять секунды,
        # клиент не должен ждать её, чтобы получить ответ
        task = asyncio.create_task(
            self.alert(
                f"⚠️ Провайдер отклонил ключ пользователя <code>{user_id}</code> "
                f"(HTTP {status}).\n"
                f"Ключ: <code>…{key[-6:]}</code>\n"
                f"Выдайте рабочий ключ: <code>/givekey {user_id} KEY</code>"
            )
        )
        self._alerts.add(task)
        task.add_done_callback(self._alerts.discard)

    def build(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY, middlewares=[json_errors])
        app.router.add_get("/", self.index)
        app.router.add_get("/health", self.health)
        app.router.add_route("*", "/v1/{path:.*}", self.proxy)
        app.on_cleanup.append(self._close)
        return app

    async def _close(self, _: web.Application) -> None:
        if self._http is not None:
            await self._http.close()

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=UPSTREAM_CONNECTIONS),
                # Сессия общая на всех клиентов: cookies провайдера одного клиента
                # не должны уходить с запросами другого
                cookie_jar=aiohttp.DummyCookieJar(),
                # сжатый ответ провайдера пересылаем как есть, не распаковывая
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(
                    total=None, connect=UPSTREAM_SLOT_WAIT, sock_connect=20, sock_read=900
                ),
            )
        return self._http

    async def _request(
        self, method: str, url: str, params, body: bytes | None, headers: dict[str, str]
    ) -> tuple[aiohttp.ClientResponse, bool]:
        """Запрос к провайдеру. Редиректы проходим сами: при переходе на другой хост
        (например, видео на CDN) aiohttp убирает только Authorization, а x-api-key с ключом
        провайдера ушёл бы постороннему серверу.

        Второй элемент — ушёл ли последний запрос с ключом провайдера. Без ключа ответ
        401/403 говорит не о сломанном ключе, а о чужом хосте или подписанной ссылке.
        """
        session = self._session()
        base = URL(self.settings.api_base_url)
        provider = (base.scheme, base.host, base.port)  # port учитывает порт по умолчанию
        keyed = True
        up = await session.request(
            method, url, params=params, data=body, headers=headers, allow_redirects=False
        )
        for _ in range(MAX_REDIRECTS + 1):
            location = up.headers.get("Location")
            if up.status not in REDIRECT_STATUSES or not location:
                return up, keyed
            up.release()
            try:
                target = up.url.join(URL(location))
                target_host = (target.scheme, target.host, target.port)
            except ValueError as e:  # битый Location — ошибка провайдера, клиенту 502
                raise aiohttp.InvalidUrlRedirectClientError(location, str(e)) from e
            if keyed and target_host != provider:
                headers = {k: v for k, v in headers.items() if k not in PROVIDER_AUTH_HEADERS}
                keyed = False
            if up.status == 303 or (up.status in (301, 302) and method == "POST"):
                method, body = "GET", None
            up = await session.request(
                method, target, data=body, headers=headers, allow_redirects=False
            )
        # Бесконечные редиректы: сырой 3xx отдал бы клиенту адрес провайдера
        up.release()
        raise aiohttp.TooManyRedirects(up.request_info, (), status=up.status)

    async def index(self, _: web.Request) -> web.Response:
        return web.json_response(
            {"name": f"{SHOP_NAME} API", "base_url": self.settings.client_api_url}
        )

    async def health(self, _: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _upstream_key(self, request: web.Request) -> tuple[int, str] | web.Response:
        key = client_key_from(request)
        if not key or not key.startswith(CLIENT_KEY_PREFIX):
            return api_error(401, "invalid_api_key", "Ключ не передан или недействителен")
        user_id = await self.db.user_by_client_key(key)
        if user_id is None:
            return api_error(401, "invalid_api_key", "Ключ не передан или недействителен")
        sub = await self.db.get_subscription(user_id)
        if sub is None or not sub.is_active:
            return api_error(
                402, "subscription_required", "Нет активного тарифа. Продлите подписку в боте"
            )
        upstream = await self.db.get_active_key(user_id)
        if upstream is None:
            return api_error(
                503,
                "key_pending",
                "Доступ ещё готовится, бот пришлёт уведомление. Попробуйте чуть позже",
                kind="server_error",
            )
        return user_id, upstream.key

    async def proxy(self, request: web.Request) -> web.StreamResponse:
        path = request.match_info["path"]
        if not is_allowed_path(path):
            return api_error(404, "not_found", "Такого эндпоинта нет")
        auth = await self._upstream_key(request)
        if isinstance(auth, web.Response):
            return auth
        user_id, upstream_key = auth

        # Лимит до чтения тела: иначе один клиент сотней долгих запросов занимает все
        # соединения к провайдеру (и память под тела до 50 МБ), а остальные ждут
        if self._in_flight.get(user_id, 0) >= MAX_CONCURRENT_PER_CLIENT:
            response = api_error(
                429,
                "too_many_concurrent_requests",
                f"Не больше {MAX_CONCURRENT_PER_CLIENT} одновременных запросов на ключ. "
                "Дождитесь ответов на текущие",
                kind="rate_limit_error",
            )
            response.headers["Retry-After"] = "1"
            return response
        self._in_flight[user_id] = self._in_flight.get(user_id, 0) + 1
        try:
            return await self._forward(request, path, user_id, upstream_key)
        finally:
            self._in_flight[user_id] -= 1
            if not self._in_flight[user_id]:
                del self._in_flight[user_id]

    async def _forward(
        self, request: web.Request, path: str, user_id: int, upstream_key: str
    ) -> web.StreamResponse:
        url = f"{self.settings.api_base_url.rstrip('/')}/{path}"
        headers = {h: request.headers[h] for h in FORWARD_REQUEST_HEADERS if h in request.headers}
        # Сжатие — только если клиент сам его просил: иначе aiohttp попросит gzip сам,
        # а мы перешлём клиенту сжатые байты, которые тот не ждёт
        headers["Accept-Encoding"] = request.headers.get("Accept-Encoding", "identity")
        headers["Authorization"] = f"Bearer {upstream_key}"
        headers["x-api-key"] = upstream_key
        body = await request.read() if request.can_read_body else None

        started = time.monotonic()
        try:
            up, keyed = await self._request(request.method, url, request.query, body, headers)
            async with up:
                # (только на запрос с ключом провайдера: после редиректа на CDN ключа нет)
                if up.status in (401, 402, 403) and keyed:
                    # ключ провайдера из пула не работает (отозван, у аккаунта кончился тариф) —
                    # это наша проблема, не клиента: его подписку мы уже проверили выше
                    self._report_bad_upstream_key(user_id, upstream_key, up.status)
                    return api_error(
                        503,
                        "provider_key_error",
                        "Доступ временно недоступен, мы уже исправляем. Напишите в поддержку, "
                        "если ошибка не пропадёт",
                        kind="server_error",
                    )
                response = web.StreamResponse(status=up.status, reason=up.reason)
                for name, value in up.headers.items():
                    if name.lower() not in HOP_BY_HOP:
                        response.headers.add(name, value)
                if up.content_type == "text/event-stream":
                    response.headers["Cache-Control"] = "no-cache"
                    response.headers["X-Accel-Buffering"] = "no"
                await response.prepare(request)
                try:
                    async for chunk in up.content.iter_any():
                        await response.write(chunk)
                except (ConnectionResetError, aiohttp.ClientError) as e:
                    # Ответ уже начат — новую ошибку не отправить. Рвём соединение, не дописав
                    # ответ: иначе aiohttp сам корректно его завершит, и клиент примет
                    # обрезанный JSON или поток без [DONE] за полный
                    logger.info("user %s: поток /v1/%s прерван (%s)", user_id, path, e)
                    if request.transport is not None:
                        request.transport.close()
                    return response
                await response.write_eof()
                logger.info(
                    "user %s %s /v1/%s -> %s за %.1f с",
                    user_id,
                    request.method,
                    path,
                    up.status,
                    time.monotonic() - started,
                )
                return response
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("Провайдер недоступен (%s): %s", path, e)
            return api_error(
                502,
                "upstream_error",
                "Сервис моделей не отвечает, повторите запрос",
                "server_error",
            )
