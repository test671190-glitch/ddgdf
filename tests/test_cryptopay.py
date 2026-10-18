"""Клиент Crypto Pay: ошибки сервиса превращаются в CryptoPayError."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.services.cryptopay import CryptoPayClient, CryptoPayError


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
