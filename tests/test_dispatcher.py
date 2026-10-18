"""Апдейты через настоящий Dispatcher (те же middleware и роутеры, что в __main__.py)
и фейковую сессию Bot API без сети."""

import itertools
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageAutoDeleteTimerChanged,
    SuccessfulPayment,
    Update,
    User,
)

from bot.config import Settings
from bot.db import Database, utcnow
from bot.handlers import get_routers
from bot.keyboards.inline import CryptoCheck, Menu, RotateKey
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.user import UserMiddleware
from bot.services.crypto import CryptoPayments

BOT_USER = User(id=4242, is_bot=True, first_name="shop", username="shopbot")
GATEWAY = {"public_api_url": "https://x.sslip.io/v1"}
_ids = itertools.count(1)


class FakeSession(BaseSession):
    """Bot API без сети: запоминает вызовы, hooks могут бросить ошибку Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.hooks: list = []

    async def make_request(self, bot, method, timeout=None):
        for hook in self.hooks:
            hook(method)
        self.calls.append(method)
        name = type(method).__name__
        if name in ("AnswerCallbackQuery", "DeleteMessage"):
            return True
        if name in ("SendMessage", "EditMessageText"):
            return Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=getattr(method, "chat_id", None) or 42, type="private"),
                from_user=BOT_USER,
                text=method.text,
            )
        raise NotImplementedError(name)

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError
        yield b""

    def named(self, name: str) -> list:
        return [c for c in self.calls if type(c).__name__ == name]


_dispatcher: Dispatcher | None = None


def dispatcher() -> Dispatcher:
    # роутеры модульные: подключить их можно только к одному Dispatcher
    global _dispatcher
    if _dispatcher is None:
        dp = Dispatcher()
        dp.update.outer_middleware(LoggingMiddleware())
        dp.update.outer_middleware(UserMiddleware())
        dp.message.filter(F.chat.type == ChatType.PRIVATE)
        dp.include_routers(*get_routers())
        _dispatcher = dp
    return _dispatcher


def settings(**kwargs) -> Settings:
    return Settings(_env_file=None, bot_token="1:x", admin_ids=[1000], **kwargs)


def user(uid: int = 42) -> User:
    return User(id=uid, is_bot=False, first_name="Neo")


def message_update(text=None, uid=42, chat_type="private", **extra) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=uid if chat_type == "private" else -100, type=chat_type),
        from_user=user(uid),
        text=text,
        **extra,
    )
    return Update(update_id=next(_ids), message=message)


def callback_update(data: str, uid: int = 42) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=uid, type="private"),
        from_user=BOT_USER,
        text="old text",
    )
    query = CallbackQuery(
        id=str(next(_ids)), from_user=user(uid), chat_instance="ci", data=data, message=message
    )
    return Update(update_id=next(_ids), callback_query=query)


@pytest.fixture
def tg():
    session = FakeSession()
    bot = Bot(
        token="4242:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    return bot, session


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


async def feed(bot, update, db, st=None, crypto=None):
    return await dispatcher().feed_update(
        bot, update, db=db, settings=st or settings(), crypto=crypto
    )


async def test_payment_survives_failed_user_upsert(tg, db):
    """Запись в users падала раньше обработчика — и вместе с ней выбрасывался апдейт
    с оплатой: Stars списаны, а ни админ, ни клиент ничего не узнавали."""
    bot, session = tg
    await db.close()  # любая запись в базу теперь падает
    payment = SuccessfulPayment(
        currency="XTR",
        total_amount=2541,
        invoice_payload="tariff:pro",
        telegram_payment_charge_id="ch-77",
        provider_payment_charge_id="",
    )
    await feed(bot, message_update(uid=7, successful_payment=payment), db)
    sent = {(m.chat_id, "ch-77" in m.text) for m in session.named("SendMessage")}
    assert (1000, True) in sent  # админ получил charge_id
    assert any(chat == 7 for chat, _ in sent)  # клиент получил ответ
    await db.connect()  # фикстура закроет базу ещё раз


async def test_group_members_are_not_counted_as_users(tg, db):
    bot, _ = tg
    for uid in (1, 2, 3):
        await feed(bot, message_update("привет", uid=uid, chat_type="supergroup"), db)
    assert (await db.stats())["users"] == 0


async def test_fallback_menu_knows_about_subscription(tg, db):
    """Подписчику на непонятное сообщение показывалось «Купить подписку»."""
    bot, session = tg
    await db.grant(42, "pro", 30)
    await feed(bot, message_update("что-то непонятное"), db)
    (reply,) = session.named("SendMessage")
    labels = [b.text for row in reply.reply_markup.inline_keyboard for b in row]
    assert "Купить подписку" not in labels


async def test_service_message_gets_no_reply(tg, db):
    """Служебное сообщение (клиент включил автоудаление) — не вопрос к боту."""
    bot, session = tg
    timer = MessageAutoDeleteTimerChanged(message_auto_delete_time=86400)
    await feed(bot, message_update(message_auto_delete_timer_changed=timer), db)
    assert session.named("SendMessage") == []


async def test_button_is_answered_when_telegram_fails(tg, db):
    """Сбой Telegram при редактировании сообщения оставлял кнопку крутиться."""
    bot, session = tg

    def flood(method) -> None:
        if type(method).__name__ == "EditMessageText":
            raise TelegramRetryAfter(method=method, message="flood", retry_after=5)

    session.hooks.append(flood)
    with pytest.raises(TelegramRetryAfter):
        await feed(bot, callback_update(Menu(section="faq").pack()), db)
    assert len(session.named("AnswerCallbackQuery")) == 1


async def test_forged_rotate_without_subscription_does_nothing(tg, db):
    """Поддельная кнопка перевыпуска без подписки создавала ключ и писала
    «Ключ перевыпущен», хотя доступа нет."""
    bot, session = tg
    await feed(bot, callback_update(RotateKey(confirm=1).pack()), db, settings(**GATEWAY))
    assert await db.get_client_key(42) is None
    (answer,) = session.named("AnswerCallbackQuery")
    assert "активной подписке" in answer.text


async def test_paid_crypto_check_shows_key(tg):
    """«Проверить оплату» для оплаченного счёта говорила «ключ уже в чате», даже если
    сообщение с ключом не дошло. Теперь показывает профиль с ключом."""
    bot, session = tg
    db = Database(":memory:", hide_keys=True)
    await db.connect()
    st = settings(**GATEWAY)
    await db.add_keys("pro", ["up-1"])
    order = await db.create_crypto_order(42, "pro")
    await db.set_crypto_invoice(order.id, 1, "https://t.me/pay", utcnow())
    await db.approve_crypto_order(order.id, 30)
    crypto = CryptoPayments(None, db, bot, st)
    await feed(bot, callback_update(CryptoCheck(order_id=order.id).pack()), db, st, crypto)
    (edit,) = session.named("EditMessageText")
    assert await db.get_client_key(42) in edit.text
    await db.close()
