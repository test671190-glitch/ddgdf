"""Собственный эндпоинт магазина: OpenAI- и Anthropic-совместимый прокси.

Клиент шлёт запрос на https://<наш адрес>/v1/... со своим ключом sk-us-...
Мы находим владельца ключа, проверяем активную подписку, подставляем закреплённый
за ним ключ провайдера и пересылаем запрос как есть. Ответ (в том числе поток SSE)
отдаётся клиенту по мере поступления.

Наружу сервер смотрит через Caddy (HTTPS), сам слушает только 127.0.0.1.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Awaitable, Callable

import aiohttp
from aiohttp import web
from yarl import URL

from bot.catalog import MAX_CONCURRENT_REQUESTS, MAX_REQUESTS_PER_SECOND, SHOP_NAME
from bot.config import Settings
from bot.db import CLIENT_KEY_PREFIX, Database
from bot.ratelimit import RateLimiter, ip_key

Alert = Callable[[str], Awaitable[None]]

logger = logging.getLogger(__name__)

# Картинки-референсы приходят в base64. 25 МБ хватает на десяток фото; больше на сервере
# с 1 ГБ памяти опасно: 20 одновременных запросов по 50 МБ съедали всю память
MAX_BODY = 25 * 1024 * 1024
# Тела больше этого читаем не больше чем в BIG_BODY_SLOTS запросах сразу на весь эндпоинт
BIG_BODY = 2 * 1024 * 1024
BIG_BODY_SLOTS = 6
# Что можно проксировать: метод и путь после /v1/. Только запросы к моделям — без списков,
# чтения и удаления сохранённых данных: ключи провайдера переходят от клиента к клиенту,
# и иначе новый покупатель мог бы читать или удалять данные прежнего
ROUTES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("POST", r"chat/completions"),
        ("POST", r"completions"),
        ("POST", r"responses"),
        ("POST", r"messages"),
        ("POST", r"messages/count_tokens"),
        ("GET", r"models"),
        # id модели не начинается с точки: иначе «..» уводил запрос на корень API провайдера
        ("GET", r"models/[A-Za-z0-9_:-][A-Za-z0-9._:-]*"),
        ("POST", r"images/generations"),
        ("POST", r"images/edits"),
        ("POST", r"videos"),
        ("GET", r"videos/[A-Za-z0-9_-]+"),
        ("GET", r"videos/[A-Za-z0-9_-]+/content"),
    )
)
BAD_KEY_ALERT_INTERVAL = 3600  # не чаще раза в час на один ключ
# Одновременных запросов к провайдеру. По умолчанию у aiohttp 100, и ожидание слота ничем
# не ограничено: сотня долгих запросов одного клиента вешала эндпоинт всем остальным
UPSTREAM_CONNECTIONS = 400
UPSTREAM_SLOT_WAIT = 60  # сколько ждать свободного соединения, прежде чем ответить 502
# Одновременных запросов на одного клиента: агентам в IDE хватает с запасом, а один ключ
# не может занять все соединения (UPSTREAM_CONNECTIONS) и оставить без ответа остальных
MAX_CONCURRENT_PER_CLIENT = MAX_CONCURRENT_REQUESTS
# Запросов в секунду с одного IP — до проверки ключа: флуд, в том числе с чужими или
# выдуманными ключами, не доходит до базы и провайдера
MAX_REQUESTS_PER_IP = MAX_REQUESTS_PER_SECOND
LOCAL_PROXIES = {"127.0.0.1", "::1"}
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
# Заголовки ответа провайдера, которые отдаём клиенту. Остальное (организация и аккаунт
# провайдера, Via, Location, cookies) клиенту знать незачем
FORWARD_RESPONSE_HEADERS = {
    "content-type",
    "content-encoding",
    "content-disposition",
    "cache-control",
    "retry-after",
    "x-request-id",
    "request-id",
    "openai-processing-ms",
}
FORWARD_RESPONSE_PREFIXES = ("x-ratelimit-", "anthropic-ratelimit-", "x-plan-")


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
            return api_error(413, "request_too_large", "Тело запроса больше 25 МБ")
        return api_error(e.status, f"http_{e.status}", e.reason)


def is_allowed(method: str, path: str) -> bool:
    """Только запросы к моделям из ROUTES. fullmatch по символам [A-Za-z0-9._:-] между «/»
    не пропускает «..», пустые сегменты и закодированные символы."""
    return any(m == method and p.fullmatch(path) for m, p in ROUTES)


def forward_response_header(name: str) -> bool:
    lower = name.lower()
    return lower in FORWARD_RESPONSE_HEADERS or lower.startswith(FORWARD_RESPONSE_PREFIXES)


def client_ip(request: web.Request) -> str:
    """Адрес клиента. Наружу смотрит Caddy: он кладёт настоящий адрес в X-Forwarded-For
    (присланный клиентом заголовок не доверяет и перезаписывает). Напрямую — адрес сокета."""
    remote = request.remote or ""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and remote in LOCAL_PROXIES:
        return forwarded.split(",")[-1].strip() or remote
    return remote


async def is_public_host(url: URL) -> bool:
    """Чужой хост редиректа — только по https и только на публичные адреса: иначе ответ
    провайдера (или того, кто его подменил) мог бы отправить наш запрос на localhost сервера
    (админка Caddy) или во внутреннюю сеть хостера."""
    if url.scheme != "https" or not url.host:
        return False
    try:
        return ipaddress.ip_address(url.host).is_global
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            url.host, url.port or 443, type=socket.SOCK_STREAM
        )
    except OSError:
        return False
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:  # адрес с зоной (fe80::1%eth0) — точно не публичный
            return False
    return bool(addresses) and all(a.is_global for a in addresses)


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
        self._per_ip = RateLimiter(MAX_REQUESTS_PER_IP)
        self._big_bodies = asyncio.Semaphore(BIG_BODY_SLOTS)
        # только для тестов: редирект на локальный «CDN» без https
        self.allow_private_redirects = False

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
        base_path = base.path.rstrip("/") + "/"
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
            if up.status == 303 or (up.status in (301, 302) and method == "POST"):
                method, body = "GET", None
            if target_host == provider and keyed:
                # Тот же провайдер и ещё с ключом: путь цели тоже должен быть из ROUTES,
                # иначе редирект увёл бы ключ на настройки аккаунта, биллинг и т. п.
                rest = target.path[len(base_path) :] if target.path.startswith(base_path) else ""
                if not is_allowed(method, rest):
                    raise aiohttp.InvalidUrlRedirectClientError(location, "path not allowed")
            else:
                if not self.allow_private_redirects and not await is_public_host(target):
                    raise aiohttp.InvalidUrlRedirectClientError(location, "private target")
                # Чужой хост (CDN с файлом): без ключа провайдера и без тела клиента
                headers = {
                    k: v
                    for k, v in headers.items()
                    if k not in PROVIDER_AUTH_HEADERS and k != "content-type"
                }
                keyed = False
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
        if not self._per_ip.allow(ip_key(client_ip(request))):
            response = api_error(
                429,
                "rate_limited",
                f"Не больше {MAX_REQUESTS_PER_IP} запросов в секунду с одного адреса",
                kind="rate_limit_error",
            )
            response.headers["Retry-After"] = "1"
            return response
        path = request.match_info["path"]
        if not is_allowed(request.method, path):
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
            # Тело без Content-Length (chunked) тоже может быть до MAX_BODY: раньше оно шло мимо
            # слотов, и один ключ двадцатью такими запросами занимал память до MemoryMax
            unknown_size = request.content_length is None and request.body_exists
            if unknown_size or (request.content_length or 0) > BIG_BODY:
                # большие тела (картинки) — не больше BIG_BODY_SLOTS сразу на весь эндпоинт,
                # иначе несколько клиентов могли бы занять всю память сервера
                async with self._big_bodies:
                    return await self._forward(request, path, user_id, upstream_key)
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
                    if forward_response_header(name):
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
