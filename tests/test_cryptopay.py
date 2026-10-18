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


async def serve_json(payload) -> tuple[TestServer, CryptoPayClient]:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post("/api/{method}", handler)
    server = TestServer(app)
    await server.start_server()
    return server, CryptoPayClient("token", str(server.make_url("/api")))


@pytest.mark.parametrize("payload", [None, [1, 2]])
async def test_json_that_is_not_object_is_crypto_pay_error(payload):
    """`null` или список вместо объекта: раньше падало AttributeError на data.get."""
    server, client = await serve_json(payload)
    try:
        with pytest.raises(CryptoPayError):
            await client.get_me()
    finally:
        await client.close()
        await server.close()


async def test_invoice_without_id_is_crypto_pay_error():
    server, client = await serve_json({"ok": True, "result": {"status": "active"}})
    try:
        with pytest.raises(CryptoPayError):
            await client.create_invoice(33, "x", "order:1", 3600)
    finally:
        await client.close()
        await server.close()


async def test_broken_invoice_does_not_hide_paid_ones():
    """Один битый счёт в getInvoices ронял разбор всего ответа: ни одна оплата не
    засчитывалась, пока он не пропадёт из списка."""
    items = [{"status": "paid"}, {"invoice_id": 2, "status": "paid", "payload": "order:2"}]
    server, client = await serve_json({"ok": True, "result": {"items": items}})
    try:
        invoices = await client.get_invoices([1, 2])
        assert [(i.invoice_id, i.status) for i in invoices] == [(2, "paid")]
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
