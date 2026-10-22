"""Мини-клиент Telegram Bot API на стандартной библиотеке Python.

aiogram вместе с pydantic держал в памяти ~170 МБ. Этому клиенту хватает ~20 МБ:
HTTP/1.1 поверх asyncio с TLS, keep-alive и небольшим пулом соединений, а вместо
сотен классов — обычные dict из JSON Bot API.

Апдейты одного пользователя обрабатываются строго по очереди (Serial): иначе два
быстрых сообщения читали бы историю диалога одновременно и второе не помнило бы первое.
"""

import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable

HOST = "api.telegram.org"
POOL_SIZE = 4  # открытых соединений для запросов, кроме long polling
REQUEST_TIMEOUT = 30
POLL_TIMEOUT = 50  # столько секунд Telegram держит getUpdates без новых апдейтов
MAX_PENDING = 20  # апдейтов в очереди одного пользователя, дальше — флуд, отбрасываем

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int | None = None) -> None:
        super().__init__(f"{code} {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after


async def read_response(reader: asyncio.StreamReader) -> tuple[bool, bytes]:
    """Прочитать HTTP-ответ. Возвращает (можно ли переиспользовать соединение, тело)."""
    status = await reader.readuntil(b"\r\n")
    headers = {}
    while (line := await reader.readuntil(b"\r\n")) != b"\r\n":
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip().lower()
    keep = status.startswith(b"HTTP/1.1") and headers.get("connection") != "close"
    if headers.get("transfer-encoding") == "chunked":
        chunks = []
        while size := int((await reader.readuntil(b"\r\n")).split(b";")[0], 16):
            chunks.append(await reader.readexactly(size))
            await reader.readexactly(2)
        while await reader.readuntil(b"\r\n") != b"\r\n":
            pass  # заголовки-трейлеры
        return keep, b"".join(chunks)
    if "content-length" in headers:
        return keep, await reader.readexactly(int(headers["content-length"]))
    return False, await reader.read()  # тело до закрытия соединения


class Api:
    """Вызов метода: await api("sendMessage", chat_id=1, text="...").
    Параметры со значением None не отправляются."""

    def __init__(self, token: str, host: str = HOST, port: int = 443, tls: bool = True) -> None:
        self.path = f"/bot{token}/"
        self.host, self.port = host, port
        self.tls = ssl.create_default_context() if tls else None
        self.idle: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

    async def __call__(self, method: str, _timeout: float = REQUEST_TIMEOUT, **params):
        body = json.dumps(
            {k: v for k, v in params.items() if v is not None}, ensure_ascii=False
        ).encode()
        for attempt in range(3):
            try:
                answer = await self._request(method, body, _timeout)
            except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as e:
                # Сеть моргнула или keep-alive соединение закрылось на стороне Telegram
                self.close()  # остальные простаивающие соединения, скорее всего, тоже мертвы
                if attempt == 2:
                    raise
                log.warning("%s: ошибка сети, повторяю: %r", method, e)
                await asyncio.sleep(attempt)
                continue
            if answer.get("ok"):
                return answer.get("result")
            retry_after = (answer.get("parameters") or {}).get("retry_after")
            if answer.get("error_code") == 429 and retry_after and attempt < 2:
                await asyncio.sleep(retry_after)
                continue
            raise TelegramError(
                answer.get("error_code", 0), answer.get("description", ""), retry_after
            )
        raise AssertionError("unreachable")

    async def _request(self, method: str, body: bytes, timeout: float) -> dict:
        if self.idle:
            reader, writer = self.idle.pop()
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=self.tls), REQUEST_TIMEOUT
            )
        try:
            writer.write(
                f"POST {self.path}{method} HTTP/1.1\r\nHost: {self.host}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            keep, data = await asyncio.wait_for(read_response(reader), timeout)
        except BaseException:
            writer.close()
            raise
        if keep and len(self.idle) < POOL_SIZE:
            self.idle.append((reader, writer))
        else:
            writer.close()
        return json.loads(data)

    def close(self) -> None:
        while self.idle:
            self.idle.pop()[1].close()


def update_user(update: dict) -> int | None:
    event = update.get("message") or update.get("callback_query") or {}
    return (event.get("from") or {}).get("id")


class Serial:
    """Обработка апдейтов: разные пользователи — параллельно, один — по очереди."""

    def __init__(self, handle: Callable[[dict], Awaitable[None]]) -> None:
        self.handle = handle
        self.queues: dict[int, list[dict]] = {}
        self.tasks: set[asyncio.Task] = set()

    def submit(self, update: dict) -> None:
        user_id = update_user(update)
        if user_id is None:
            return
        queue = self.queues.get(user_id)
        if queue is not None:
            if len(queue) < MAX_PENDING:
                queue.append(update)
            return
        self.queues[user_id] = [update]
        task = asyncio.create_task(self._work(user_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _work(self, user_id: int) -> None:
        queue = self.queues[user_id]
        try:
            while queue:
                update = queue.pop(0)
                try:
                    await self.handle(update)
                except Exception:
                    log.exception("Апдейт %s не обработан", update.get("update_id"))
        finally:
            del self.queues[user_id]

    async def drain(self, timeout: float) -> None:
        if self.tasks:
            await asyncio.wait(set(self.tasks), timeout=timeout)


async def poll(api: Api, serial: Serial, stop: asyncio.Event) -> None:
    """Long polling до stop. Апдейты, полученные до остановки, будут доработаны."""
    offset = None
    delay = 1
    while not stop.is_set():
        request = asyncio.ensure_future(
            api(
                "getUpdates",
                _timeout=POLL_TIMEOUT + 15,
                offset=offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=["message", "callback_query"],
            )
        )
        stopping = asyncio.ensure_future(stop.wait())
        done, _ = await asyncio.wait({request, stopping}, return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        if request not in done:
            request.cancel()
            break
        try:
            updates = request.result()
        except TelegramError as e:
            # 409 — где-то запущен второй экземпляр бота с тем же токеном
            log.error("getUpdates: %s", e)
            await asyncio.sleep(e.retry_after or delay)
            delay = min(delay * 2, 30)
            continue
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as e:
            log.warning("getUpdates: нет связи с Telegram: %r", e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        delay = 1
        for update in updates:
            offset = update["update_id"] + 1
            serial.submit(update)
    await serial.drain(timeout=10)
    if offset is not None:
        # Подтверждаем полученные апдейты, иначе после перезапуска они придут снова
        try:
            await api("getUpdates", offset=offset, timeout=0, limit=1)
        except (TelegramError, OSError, TimeoutError, ValueError) as e:
            log.warning("Не удалось подтвердить апдейты: %r", e)
