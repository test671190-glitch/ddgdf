"""Мини-клиент Telegram Bot API на стандартной библиотеке Python, без asyncio.

aiogram с pydantic держал ~170 МБ. Здесь нет и asyncio — он один стоит ~6 МБ:
блокирующие TLS-сокеты, keep-alive пул соединений и рабочие потоки, которые
создаются по мере нужды. Вместо сотен классов — обычные dict из JSON Bot API.

Апдейты одного пользователя обрабатываются строго по очереди (Serial): иначе два
быстрых сообщения читали бы историю диалога одновременно и второе не помнило бы первое.
"""

import json
import signal
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable

from models_bot import log

HOST = "api.telegram.org"
POOL_SIZE = 4  # простаивающих соединений держим открытыми
REQUEST_TIMEOUT = 30
POLL_TIMEOUT = 50  # столько секунд Telegram держит getUpdates без новых апдейтов
MAX_PENDING = 20  # апдейтов в очереди одного пользователя, дальше — флуд, отбрасываем
MAX_WORKERS = 16  # потоков-обработчиков, создаются по мере нужды
LINE_LIMIT = 65536

# socket.timeout и ssl.SSLError — это OSError; битый ответ — ValueError
NETWORK_ERRORS = (OSError, ValueError)


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: float | None = None) -> None:
        super().__init__(f"{code} {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after


class Stop(BaseException):
    """Сигнал остановки прерывает ожидание getUpdates (BaseException: не путать с ошибкой)."""


def _line(stream) -> bytes:
    line = stream.readline(LINE_LIMIT)
    if not line:
        raise ConnectionError("соединение закрыто")
    return line


def _exactly(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ConnectionError("ответ оборван")
    return data


def read_response(stream) -> tuple[bool, bytes]:
    """Прочитать HTTP-ответ. Возвращает (можно ли переиспользовать соединение, тело)."""
    status = _line(stream)
    headers = {}
    while (line := _line(stream)) not in (b"\r\n", b"\n"):
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip().lower()
    keep = status.startswith(b"HTTP/1.1") and headers.get("connection") != "close"
    if headers.get("transfer-encoding") == "chunked":
        chunks = []
        while size := int(_line(stream).split(b";")[0], 16):
            chunks.append(_exactly(stream, size))
            _exactly(stream, 2)
        while _line(stream) not in (b"\r\n", b"\n"):
            pass  # заголовки-трейлеры
        return keep, b"".join(chunks)
    if "content-length" in headers:
        return keep, _exactly(stream, int(headers["content-length"]))
    return False, stream.read()  # тело до закрытия соединения


def _close(sock, stream) -> None:
    stream.close()
    sock.close()


class Api:
    """Вызов метода: api("sendMessage", chat_id=1, text="..."). Можно из любого потока.
    Параметры со значением None не отправляются."""

    def __init__(self, token: str, host: str = HOST, port: int = 443, tls: bool = True) -> None:
        self.path = f"/bot{token}/"
        self.host, self.port = host, port
        self.tls = ssl.create_default_context() if tls else None
        self.idle: list = []  # (сокет, буферизованное чтение)
        self.lock = threading.Lock()

    def __call__(self, method: str, _timeout: float = REQUEST_TIMEOUT, **params):
        body = json.dumps(
            {k: v for k, v in params.items() if v is not None}, ensure_ascii=False
        ).encode()
        for attempt in range(3):
            try:
                answer = self._request(method, body, _timeout)
            except NETWORK_ERRORS as e:
                # Сеть моргнула или keep-alive соединение закрылось на стороне Telegram
                self.close()  # остальные простаивающие соединения, скорее всего, тоже мертвы
                if attempt == 2:
                    raise
                log.warning("%s: ошибка сети, повторяю: %r", method, e)
                time.sleep(attempt)
                continue
            if answer.get("ok"):
                return answer.get("result")
            retry_after = (answer.get("parameters") or {}).get("retry_after")
            if answer.get("error_code") == 429 and retry_after and attempt < 2:
                time.sleep(retry_after)
                continue
            raise TelegramError(
                answer.get("error_code", 0), answer.get("description", ""), retry_after
            )
        raise AssertionError("unreachable")

    def _connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=REQUEST_TIMEOUT)
        if self.tls:
            try:
                sock = self.tls.wrap_socket(sock, server_hostname=self.host)
            except BaseException:
                sock.close()
                raise
        return sock, sock.makefile("rb")

    def _request(self, method: str, body: bytes, timeout: float) -> dict:
        with self.lock:
            conn = self.idle.pop() if self.idle else None
        sock, stream = conn or self._connect()
        try:
            sock.settimeout(timeout)
            sock.sendall(
                f"POST {self.path}{method} HTTP/1.1\r\nHost: {self.host}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            keep, data = read_response(stream)
        except BaseException:
            _close(sock, stream)
            raise
        with self.lock:
            if keep and len(self.idle) < POOL_SIZE:
                self.idle.append((sock, stream))
                keep = None
        if keep is not None:
            _close(sock, stream)
        return json.loads(data)

    def close(self) -> None:
        with self.lock:
            idle, self.idle = self.idle, []
        for sock, stream in idle:
            _close(sock, stream)


def update_user(update: dict) -> int | None:
    event = (
        update.get("message")
        or update.get("callback_query")
        or update.get("pre_checkout_query")
        or {}
    )
    return (event.get("from") or {}).get("id")


class Serial:
    """Обработка апдейтов: разные пользователи — параллельно, один — по очереди."""

    def __init__(self, handle: Callable[[dict], None], max_workers: int = MAX_WORKERS) -> None:
        self.handle = handle
        self.max_workers = max_workers
        self.lock = threading.Lock()
        self.wakeup = threading.Condition(self.lock)  # для простаивающих потоков
        self.emptied = threading.Condition(self.lock)  # для drain
        self.queues: dict[int, deque] = {}  # пользователь → его необработанные апдейты
        self.ready: deque[int] = deque()  # пользователи, которых ещё никто не взял
        self.workers = 0
        self.idle = 0

    def submit(self, update: dict) -> None:
        user_id = update_user(update)
        if user_id is None:
            return
        with self.lock:
            queue = self.queues.get(user_id)
            if queue is not None:
                if len(queue) < MAX_PENDING:
                    queue.append(update)
                return
            self.queues[user_id] = deque([update])
            self.ready.append(user_id)
            if self.idle == 0 and self.workers < self.max_workers:
                self.workers += 1
                threading.Thread(
                    target=self._work, name=f"worker-{self.workers}", daemon=True
                ).start()
            else:
                self.wakeup.notify()

    def _work(self) -> None:
        while True:
            with self.lock:
                while not self.ready:
                    self.idle += 1
                    self.wakeup.wait()
                    self.idle -= 1
                user_id = self.ready.popleft()
            self._serve(user_id)

    def _serve(self, user_id: int) -> None:
        while True:
            with self.lock:
                queue = self.queues[user_id]
                if not queue:
                    del self.queues[user_id]
                    self.emptied.notify_all()
                    return
                update = queue.popleft()
            try:
                self.handle(update)
            except Exception:
                log.exception("Апдейт %s не обработан", update.get("update_id"))

    def drain(self, timeout: float) -> bool:
        """Дождаться обработки всех полученных апдейтов. False — не успели за timeout."""
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.queues:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.emptied.wait(left)
        return True


def poll(api: Api, serial: Serial, stop: threading.Event) -> None:
    """Long polling до SIGTERM/SIGINT или stop. Вызывать из главного потока.
    Полученные апдейты дорабатываются, а затем подтверждаются в Telegram."""
    waiting = False

    def on_signal(signum, frame) -> None:
        stop.set()
        if waiting:
            raise Stop  # прерываем ожидание getUpdates, а не ждём до 50 секунд

    handlers = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    offset = None
    delay = 1
    try:
        while not stop.is_set():
            try:
                waiting = True
                updates = api(
                    "getUpdates",
                    _timeout=POLL_TIMEOUT + 15,
                    offset=offset,
                    timeout=POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query", "pre_checkout_query"],
                )
            except TelegramError as e:
                # 409 — где-то запущен второй экземпляр бота с тем же токеном
                log.error("getUpdates: %s", e)
                stop.wait(e.retry_after or delay)
                delay = min(delay * 2, 30)
                continue
            except Exception as e:
                log.warning("getUpdates: нет связи с Telegram: %r", e)
                stop.wait(delay)
                delay = min(delay * 2, 30)
                continue
            finally:
                waiting = False
            delay = 1
            for update in updates:
                offset = update["update_id"] + 1
                serial.submit(update)
    except Stop:
        pass
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    if not serial.drain(timeout=10):
        log.warning("Не все апдейты обработаны до остановки")
    if offset is not None:
        # Подтверждаем полученные апдейты, иначе после перезапуска они придут снова
        try:
            api("getUpdates", _timeout=10, offset=offset, timeout=0, limit=1)
        except (TelegramError, *NETWORK_ERRORS) as e:
            log.warning("Не удалось подтвердить апдейты: %r", e)
