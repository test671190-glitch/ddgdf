from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[list[int], NoDecode] = []
    log_level: str = "INFO"
    # Прокси до api.telegram.org, если он заблокирован: socks5://user:pass@host:port или http://...
    proxy: str = ""

    db_path: str = "data/bot.db"
    support_username: str = ""
    # Сколько Telegram Stars стоит 1 доллар цены тарифа
    stars_per_usd: float = 77.0
    subscription_days: int = 30
    remind_days_before: int = 3

    api_base_url: str = "https://useitai.top/api/v1"
    # Сайт магазина (docs/dist на Netlify): раздел «Сайт» в боте
    site_url: str = "https://unknown-shop.netlify.app"
    # Документация API. Пусто — страница API на сайте
    docs_url: str = ""

    # Crypto Pay API (@CryptoBot → Crypto Pay → My Apps). Пусто — оплата криптой выключена
    crypto_pay_token: SecretStr | None = None
    crypto_pay_url: str = "https://pay.crypt.bot/api"
    crypto_invoice_minutes: int = 60

    # Собственный эндпоинт: публичный адрес, например https://154-83-140-29.sslip.io/v1.
    # Пусто — эндпоинт выключен и клиенты получают ключи провайдера напрямую.
    public_api_url: str = ""
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8081

    # Сайт: запускается вместе с ботом
    web_enabled: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    # Ставьте true, когда сайт работает по HTTPS: cookie сессии станет Secure
    web_secure_cookies: bool = False

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if isinstance(value, str):
            return [int(x) for x in value.split(",") if x.strip()]
        return value

    @field_validator("support_username")
    @classmethod
    def strip_at(cls, value: str) -> str:
        # Часто вставляют ссылку целиком: кнопка тогда вела на https://t.me/https://t.me/...
        value = value.strip().removeprefix("https://").removeprefix("http://")
        value = value.removeprefix("t.me/").removeprefix("telegram.me/")
        return value.strip("/").lstrip("@")

    @field_validator("public_api_url", "api_base_url", "docs_url", "site_url")
    @classmethod
    def strip_spaces(cls, value: str) -> str:
        # PUBLIC_API_URL из одних пробелов включал эндпоинт с пустым адресом
        return value.strip()

    @property
    def docs_link(self) -> str:
        if self.docs_url:
            return self.docs_url
        return f"{self.site_url.removesuffix('/')}/api.html" if self.site_url else ""

    @property
    def anthropic_base_url(self) -> str:
        return self.api_base_url.removesuffix("/").removesuffix("/v1")

    # --- собственный эндпоинт магазина ---

    @property
    def uses_gateway(self) -> bool:
        return bool(self.public_api_url)

    @property
    def client_api_url(self) -> str:
        """Base URL, который видят клиенты: наш эндпоинт или напрямую провайдер."""
        return (self.public_api_url or self.api_base_url).removesuffix("/")

    @property
    def client_anthropic_url(self) -> str:
        return self.client_api_url.removesuffix("/v1")


def get_settings() -> Settings:
    return Settings()
