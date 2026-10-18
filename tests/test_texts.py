from datetime import timedelta
from html.parser import HTMLParser

import pytest

from bot import texts
from bot.catalog import FAQ, MODEL_CATEGORIES, TARIFFS
from bot.config import Settings
from bot.db import Activation, CryptoOrder, Subscription, utcnow

# Теги, которые понимает Telegram HTML
ALLOWED = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}


class TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        assert tag in ALLOWED, f"Telegram не поддерживает <{tag}>"
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, f"Незакрытый тег перед </{tag}>"


def check(html: str) -> None:
    parser = TagChecker()
    parser.feed(html)
    assert not parser.stack, f"Не закрыты: {parser.stack}"
    assert len(html) < 4096


@pytest.fixture
def settings() -> Settings:
    return Settings(
        bot_token="1:x", support_username="help", public_api_url="https://1-2-3-4.sslip.io/v1"
    )


def all_texts(settings: Settings) -> list[str]:
    sub = Subscription(1, "pro", utcnow() + timedelta(days=12))
    order = CryptoOrder(7, 1, "pro", "pending", 99, "https://t.me/x", sub.expires_at)
    act = Activation("pro", sub.expires_at, "sk-<test>&", None, False)
    result = [
        texts.welcome("<Имя>", None),
        texts.welcome("Имя", sub),
        texts.tariffs_overview(settings),
        texts.profile(1, None, None, settings),
        texts.profile(1, sub, "sk-us-1", settings),
        texts.rotate_confirm(),
        texts.models_overview(),
        texts.api_info(settings),
        texts.faq_overview(),
        texts.support(settings),
        texts.paysupport(settings),
        texts.terms(settings),
        texts.reminder(sub),
        texts.expired(sub),
        texts.activation(TARIFFS["pro"], act, settings, paid=True, key="sk-us-<x>&"),
        texts.key_issued("Pro", "sk-1", settings),
        texts.crypto_invoice(TARIFFS["pro"], order),
        texts.crypto_expired(order),
        texts.web_login_confirm(),
    ]
    result += [texts.tariff_details(t, settings, sub) for t in TARIFFS.values()]
    result += [texts.models_list(c) for c in MODEL_CATEGORIES]
    result += [texts.faq_answer(q, a) for q, a in FAQ]
    return result


def test_all_texts_are_valid_telegram_html(settings):
    for html in all_texts(settings):
        check(html)


@pytest.mark.parametrize(
    ("left", "days"),
    [
        (timedelta(days=30) - timedelta(seconds=1), 30),  # только что купил — 30, а не 29
        (timedelta(hours=23), 1),  # подписка ещё работает — не «0 дн.»
        (timedelta(days=2), 2),
        (-timedelta(hours=1), 0),
    ],
)
def test_days_left_rounds_up(left, days):
    sub = Subscription(1, "pro", utcnow() + left)
    assert texts.days_left(sub) == days


def test_support_username_from_link():
    """Ссылку целиком превращали в https://t.me/https://t.me/help_bot."""
    for value in ("https://t.me/help_bot", "t.me/help_bot/", "@help_bot", " help_bot "):
        settings = Settings(_env_file=None, bot_token="1:x", support_username=value)
        assert settings.support_username == "help_bot"


def test_blank_public_api_url_keeps_gateway_off():
    settings = Settings(_env_file=None, bot_token="1:x", public_api_url="   ")
    assert not settings.uses_gateway


def test_progress_bar():
    assert texts.progress_bar(15, 30) == "▰▰▰▰▰▱▱▱▱▱"
    assert texts.progress_bar(40, 30) == "▰" * 10
    assert texts.progress_bar(0, 30) == "▱" * 10
