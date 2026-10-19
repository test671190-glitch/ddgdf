"""Апдейты через настоящий Dispatcher (те же middleware и роутеры, что в __main__.py)
и фейковую сессию Bot API без сети."""

import itertools
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageAutoDeleteTimerChanged,
    MessageId,
    PhotoSize,
    SuccessfulPayment,
    Update,
    User,
)

from bot.config import Settings
from bot.db import Database, utcnow
from bot.handlers import get_routers
from bot.keyboards.inline import BuyTariff, CryptoCheck, Menu, RotateKey, SupportWrite
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
        if name == "CopyMessage":
            return MessageId(message_id=next(_ids))
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
    # Dispatcher общий на все тесты: состояние «пишет в поддержку» не должно переходить
    # из теста в тест
    dispatcher().fsm.storage = MemoryStorage()
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


def reply_update(reply_to_id: int, uid: int = 1000, **extra) -> Update:
    """Админ отвечает реплаем на сообщение бота с id reply_to_id."""
    replied = Message(
        message_id=reply_to_id,
        date=datetime.now(),
        chat=Chat(id=uid, type="private"),
        from_user=BOT_USER,
        text="обращение",
    )
    return message_update(uid=uid, reply_to_message=replied, **extra)


def sent_to(session: FakeSession, chat_id: int) -> list:
    return [
        m
        for m in session.calls
        if type(m).__name__ in ("SendMessage", "CopyMessage") and m.chat_id == chat_id
    ]


async def ask_support(bot, session, db, text="Не пришёл ключ после оплаты"):
    """Клиент 42 нажимает «Написать в поддержку» и отправляет вопрос."""
    await feed(bot, callback_update(SupportWrite().pack()), db)
    await feed(bot, message_update(text), db)
    card, copy = sent_to(session, 1000)
    return card, copy


async def test_support_question_reaches_admin_and_answer_reaches_client(tg, db):
    """Раньше поддержки не было вовсе: без SUPPORT_USERNAME бот писал «контакт скоро
    появится». Теперь вопрос приходит админу, а ответ реплаем — клиенту."""
    bot, session = tg
    await db.grant(42, "pro", 30)
    card, copy = await ask_support(bot, session, db)
    assert "Обращение в поддержку" in card.text and "<code>42</code>" in card.text
    assert copy.from_chat_id == 42  # само сообщение клиента скопировано админу
    assert "отправлено в поддержку" in sent_to(session, 42)[-1].text

    session.calls.clear()
    card_id = (await db._one("SELECT message_id FROM support_messages LIMIT 1"))["message_id"]
    await feed(bot, reply_update(card_id, text="Ключ выдан, проверьте профиль"), db)
    (answer,) = sent_to(session, 42)
    assert "Ответ поддержки" in answer.text and "проверьте профиль" in answer.text
    labels = [b.text for row in answer.reply_markup.inline_keyboard for b in row]
    assert "Ответить" in labels
    assert "отправлен клиенту" in sent_to(session, 1000)[-1].text


async def test_admin_photo_answer_is_copied(tg, db):
    bot, session = tg
    await ask_support(bot, session, db)
    session.calls.clear()
    copy_id = (await db._all("SELECT message_id FROM support_messages ORDER BY message_id"))[-1]
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    await feed(bot, reply_update(copy_id["message_id"], photo=photo, caption="вот так"), db)
    header, copied = sent_to(session, 42)
    assert "Ответ поддержки" in header.text
    assert type(copied).__name__ == "CopyMessage" and copied.from_chat_id == 1000


async def test_admin_reply_to_other_message_is_not_forwarded(tg, db):
    bot, session = tg
    await feed(bot, reply_update(999_999, text="просто реплай"), db)
    assert sent_to(session, 42) == []


async def test_cancelled_support_does_not_forward_next_message(tg, db):
    bot, session = tg
    await feed(bot, callback_update(SupportWrite().pack()), db)
    await feed(bot, callback_update(SupportWrite(cancel=True).pack()), db)
    await feed(bot, message_update("привет"), db)
    assert sent_to(session, 1000) == []


async def test_start_cancels_support(tg, db):
    bot, session = tg
    await feed(bot, callback_update(SupportWrite().pack()), db)
    await feed(bot, message_update("/start"), db)
    await feed(bot, message_update("привет"), db)
    assert sent_to(session, 1000) == []


async def test_unknown_command_is_not_sent_to_support(tg, db):
    bot, session = tg
    await feed(bot, callback_update(SupportWrite().pack()), db)
    await feed(bot, message_update("/whatever"), db)
    assert sent_to(session, 1000) == []


async def test_support_without_admins_says_unavailable(tg, db):
    bot, session = tg
    await feed(
        bot, callback_update(SupportWrite().pack()), db, Settings(_env_file=None, bot_token="1:x")
    )
    (answer,) = session.named("AnswerCallbackQuery")
    assert "недоступна" in answer.text


async def test_site_section_links_to_site_and_docs(tg, db):
    """В боте не было ничего о сайте."""
    bot, session = tg
    await feed(bot, callback_update(Menu(section="site").pack()), db)
    (edit,) = session.named("EditMessageText")
    assert "unknown-shop.netlify.app" in edit.text
    urls = [b.url for row in edit.reply_markup.inline_keyboard for b in row if b.url]
    assert urls == ["https://unknown-shop.netlify.app", "https://unknown-shop.netlify.app/api.html"]


async def test_grant_of_other_tariff_needs_force(tg, db):
    """/grant 42 pro 3 клиенту с оплаченным Unlimited молча сжигал ему остаток дней."""
    bot, session = tg
    await db.grant(42, "unlimited", 60)
    before = (await db.get_subscription(42)).expires_at
    await feed(bot, message_update("/grant 42 pro 3", uid=1000), db)
    (warning,) = sent_to(session, 1000)
    assert "сожжёт остаток" in warning.text and "force" in warning.text
    sub = await db.get_subscription(42)
    assert sub.tariff == "unlimited" and sub.expires_at == before

    session.calls.clear()
    await feed(bot, message_update("/grant 42 pro 3 force", uid=1000), db)
    assert (await db.get_subscription(42)).tariff == "pro"


async def test_grant_warns_about_unknown_user(tg, db):
    bot, session = tg
    await feed(bot, message_update("/grant 555 pro", uid=1000), db)
    assert "нет в базе" in sent_to(session, 1000)[-1].text


async def test_button_under_payment_receipt_keeps_receipt(tg, db):
    """«Как подключить» под сообщением «Оплата прошла!» заменяло собой квитанцию с ключом."""
    bot, session = tg
    receipt = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=42, type="private"),
        from_user=BOT_USER,
        text="🎉 Оплата прошла!\nТариф Pro активен до ...",
    )
    query = CallbackQuery(
        id="q",
        from_user=user(42),
        chat_instance="ci",
        data=Menu(section="api").pack(),
        message=receipt,
    )
    await feed(bot, Update(update_id=next(_ids), callback_query=query), db)
    assert session.named("EditMessageText") == []
    assert "Подключение" in session.named("SendMessage")[0].text


async def test_expired_subscription_is_not_called_absent(tg, db):
    bot, session = tg
    await db.grant(42, "pro", 1)
    await db.conn.execute("UPDATE subscriptions SET expires_at='2026-01-01T00:00:00+00:00'")
    await db.conn.commit()
    await feed(bot, message_update("/start"), db)
    assert "Подписка закончилась" in session.named("SendMessage")[0].text


async def test_legacy_tariff_is_not_offered_renewal(tg, db):
    """Клиенту снятого с продажи тарифа предлагали «продлить заранее» — и он терял дни."""
    bot, session = tg
    await db.grant(42, "start", 30)
    await feed(bot, message_update("/start"), db)
    labels = [
        b.text for row in session.named("SendMessage")[0].reply_markup.inline_keyboard for b in row
    ]
    assert "Сменить тариф" in labels and "Продлить подписку" not in labels


async def test_stars_invoice_failure_answers_button(tg, db):
    bot, session = tg

    def reject(method) -> None:
        if type(method).__name__ == "SendInvoice":
            raise TelegramBadRequest(method=method, message="Bad Request: CURRENCY_INVALID")

    session.hooks.append(reject)
    await feed(bot, callback_update(BuyTariff(code="pro").pack()), db)
    (answer,) = session.named("AnswerCallbackQuery")
    assert "Не получилось выставить счёт" in answer.text


async def test_expired_crypto_invoice_offers_new_invoice(tg):
    bot, session = tg
    database = Database(":memory:")
    await database.connect()
    order = await database.create_crypto_order(42, "pro")
    await database.set_crypto_invoice(order.id, 1, "https://t.me/pay", utcnow())
    await database.expire_crypto_order(order.id)
    crypto = CryptoPayments(None, database, bot, settings())
    await feed(bot, callback_update(CryptoCheck(order_id=order.id).pack()), database, None, crypto)
    (edit,) = session.named("EditMessageText")
    labels = [b.text for row in edit.reply_markup.inline_keyboard for b in row]
    assert labels[0] == "Новый счёт" and "Продлить" not in labels
    await database.close()
