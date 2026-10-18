"""Клиент Crypto Pay API (@CryptoBot): счета в долларах с оплатой любой криптовалютой.

Документация: https://help.crypt.bot/crypto-pay-api
"""

import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)


class CryptoPayError(Exception):
    pass


@dataclass(frozen=True)
class Invoice:
    invoice_id: int
    status: str  # active | paid | expired
    pay_url: str
    payload: str


class CryptoPayClient:
    def __init__(self, token: str, base_url: str = "https://pay.crypt.bot/api") -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _call(self, method: str, **params) -> dict | list:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Crypto-Pay-API-Token": self._token},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        try:
            async with self._session.post(f"{self._base_url}/{method}", json=params) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise CryptoPayError(f"{method}: нет связи с Crypto Pay ({e})") from e
        except ValueError as e:  # ответ не JSON, например HTML-страница 502
            raise CryptoPayError(f"{method}: Crypto Pay вернул не JSON ({e})") from e
        if not data.get("ok"):
            raise CryptoPayError(f"{method}: {data.get('error')}")
        return data["result"]

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def get_me(self) -> dict:
        return await self._call("getMe")

    async def create_invoice(
        self, amount_usd: float, description: str, payload: str, expires_in: int
    ) -> Invoice:
        result = await self._call(
            "createInvoice",
            currency_type="fiat",
            fiat="USD",
            amount=f"{amount_usd:.2f}",
            description=description[:1024],
            payload=payload,
            expires_in=expires_in,
            allow_comments=False,
        )
        invoice = _invoice(result)
        if not invoice.pay_url:
            # без ссылки кнопку «Оплатить» не построить — считаем это ошибкой сервиса
            raise CryptoPayError(f"createInvoice: в ответе нет ссылки на оплату ({result})")
        return invoice

    async def get_invoices(self, invoice_ids: list[int]) -> list[Invoice]:
        if not invoice_ids:
            return []
        result = await self._call(
            "getInvoices", invoice_ids=",".join(map(str, invoice_ids)), count=1000
        )
        items = result.get("items", []) if isinstance(result, dict) else result
        return [_invoice(item) for item in items]


def _invoice(data: dict) -> Invoice:
    return Invoice(
        invoice_id=int(data["invoice_id"]),
        status=data["status"],
        pay_url=data.get("bot_invoice_url") or data.get("pay_url") or "",
        payload=data.get("payload") or "",
    )
