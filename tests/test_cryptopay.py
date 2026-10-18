"""Клиент Crypto Pay: ошибки сервиса превращаются в CryptoPayError."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.services.cryptopay import CryptoPayClient, CryptoPayError


async def test_invoice_without_pay_url_is_crypto_pay_error():
    """Без ссылки на оплату кнопка «Оплатить» получила бы пустой url, и Telegram отклонил бы
    сообщение — обработчик падал, а клиент не видел ничего."""

    async def no_url(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": {"invoice_id": 7, "status": "active"}})

    app = web.Application()
    app.router.add_post("/api/{method}", no_url)
    server = TestServer(app)
    await server.start_server()
    client = CryptoPayClient("token", str(server.make_url("/api")))
    try:
        with pytest.raises(CryptoPayError):
            await client.create_invoice(33, "x", "order:1", 3600)
    finally:
        await client.close()
        await server.close()


async def test_non_json_answer_is_crypto_pay_error():
    """Crypto Pay за прокси иногда отвечает HTML-страницей 502: это ошибка сервиса,
    а не падение обработчика."""

    async def bad_gateway(_: web.Request) -> web.Response:
        return web.Response(status=502, text="<html>Bad Gateway</html>", content_type="text/html")

    app = web.Application()
    app.router.add_post("/api/{method}", bad_gateway)
    server = TestServer(app)
    await server.start_server()
    client = CryptoPayClient("token", str(server.make_url("/api")))
    try:
        with pytest.raises(CryptoPayError):
            await client.get_me()
    finally:
        await client.close()
        await server.close()
