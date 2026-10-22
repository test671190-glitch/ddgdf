"""Каркас бота моделей (models_bot): меню, настройки и оформление без эмодзи."""

import itertools
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from models_bot import ui
from models_bot.catalog import MODELS, OTHER_MODELS, TOP_MODELS
from models_bot.handlers import router
from models_bot.store import Prefs, Store

BOT_USER = User(id=4242, is_bot=True, first_name="models", username="modelsbot")
_ids = itertools.count(1)

# Все символы оформления. Ни у одного нет свойства Emoji в Unicode (emoji-data.txt),
# поэтому Telegram не заменит их на цветные эмодзи
DECOR = set("✻⌬✧⁂⛭⌂ⓘ→←↓✓·—")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == "AnswerCallbackQuery":
            return True
        if name in ("SendMessage", "EditMessageText"):
            return Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=42, type="private"),
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


# Роутер модульный: подключить его можно только к одному Dispatcher
DP = Dispatcher()
DP.include_router(router)


@pytest.fixture
def tg(tmp_path):
    session = FakeSession()
    bot = Bot("4242:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, session, Store(tmp_path / "settings.db")


def callback(data: str) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=42, type="private"),
        from_user=BOT_USER,
        text="old",
    )
    query = CallbackQuery(
        id=str(next(_ids)),
        from_user=User(id=42, is_bot=False, first_name="Neo"),
        chat_instance="ci",
        data=data,
        message=message,
    )
    return Update(update_id=next(_ids), callback_query=query)


def buttons(markup) -> list:
    return [b for row in markup.inline_keyboard for b in row]


def all_screens() -> list[tuple[str, object]]:
    prefs = Prefs()
    model = MODELS[prefs.model]
    return [
        (ui.welcome("Neo", prefs), ui.main_keyboard()),
        (ui.models_text(), ui.models_keyboard()),
        (ui.other_text(), ui.other_keyboard()),
        (ui.model_card(model, prefs), ui.card_keyboard(model, prefs, "other")),
        (ui.settings_text(prefs), ui.settings_keyboard(prefs)),
        (ui.picker_text(), ui.picker_keyboard(prefs)),
        (ui.HELP, None),
    ]


def test_main_menu_is_models_and_settings_in_one_row():
    rows = ui.main_keyboard().inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["⁂ Модели", "⛭ Настройки"]]


def test_models_menu_lists_claude_then_gpt_then_other():
    texts = [b.text for b in buttons(ui.models_keyboard())]
    assert texts[:3] == ["✻ Claude Opus 5.5", "✻ Claude Sonnet 5.5", "✻ Claude Haiku 5.5"]
    assert texts[3:6] == ["⌬ GPT-6 Sol", "⌬ GPT-6 Astra", "⌬ GPT-6 Luna"]
    assert texts[6:] == ["Остальные модели →", "← В меню"]


def test_back_leads_one_screen_up():
    def back(markup) -> str:
        return ui.Nav.unpack(buttons(markup)[-1].callback_data).to

    model, prefs = MODELS["gemini-4-argon"], Prefs()
    assert back(ui.models_keyboard()) == "main"
    assert back(ui.other_keyboard()) == "models"
    assert back(ui.card_keyboard(model, prefs, "other")) == "other"
    assert back(ui.card_keyboard(model, prefs, "models")) == "models"
    assert back(ui.settings_keyboard(prefs)) == "main"
    assert back(ui.picker_keyboard(prefs)) == "settings"


def test_other_models_are_gpt_6_1_and_gemini_only():
    assert [m.vendor for m in OTHER_MODELS] == ["OpenAI"] + ["Google"] * 5
    assert {m.vendor for m in TOP_MODELS} == {"Anthropic", "OpenAI"}


def test_decor_is_symbols_not_emoji():
    for text, markup in all_screens():
        strings = [text] + ([b.text for b in buttons(markup)] if markup else [])
        strings += [c.description for c in ui.COMMANDS]
        for s in strings:
            odd = {c for c in s if ord(c) > 127 and not c.isalpha() and c not in DECOR}
            assert not odd, (s, odd)
            assert "️" not in s


def test_callback_data_fits_telegram_limit():
    for model_id in MODELS:
        for src in ("main", "other", "picker"):
            assert len(ui.UseModel(model_id=model_id, src=src).pack().encode()) <= 64


def test_store_keeps_prefs_and_forgets_removed_models(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get(1) == Prefs()
    store.update(1, model="gemini-4-argon", lang="en")
    assert Store(tmp_path / "s.db").get(1) == Prefs("gemini-4-argon", "en", "full")
    store.update(1, model="grok-4.7")  # такой модели в каталоге больше нет
    assert store.get(1).model == Prefs().model


async def test_make_model_default_from_card(tg):
    bot, session, store = tg
    await DP.feed_update(bot, callback(ui.UseModel(model_id="gpt-6-sol").pack()), store=store)
    assert store.get(42).model == "gpt-6-sol"
    edit = session.named("EditMessageText")[-1]
    assert "✓ Основная модель" in edit.text
    assert session.named("AnswerCallbackQuery")[-1].text == "✓ Основная модель · GPT-6 Sol"


async def test_picker_returns_to_settings_with_new_model(tg):
    bot, session, store = tg
    data = ui.UseModel(model_id="gemini-3.8-flash", src="picker").pack()
    await DP.feed_update(bot, callback(data), store=store)
    edit = session.named("EditMessageText")[-1]
    assert "Модель — ✧ Gemini 3.8 Flash" in edit.text
    assert buttons(edit.reply_markup)[0].text == "Модель · Gemini 3.8 Flash"


async def test_toggles_cycle_language_and_detail(tg):
    bot, session, store = tg
    await DP.feed_update(bot, callback(ui.Toggle(key="lang").pack()), store=store)
    await DP.feed_update(bot, callback(ui.Toggle(key="detail").pack()), store=store)
    assert store.get(42) == Prefs(lang="en", detail="short")
    await DP.feed_update(bot, callback(ui.Toggle(key="lang").pack()), store=store)
    assert store.get(42).lang == "ru"


async def test_every_screen_opens(tg):
    bot, session, store = tg
    screens = ("main", "models", "other", "settings", "picker")
    for to in screens:
        await DP.feed_update(bot, callback(ui.Nav(to=to).pack()), store=store)
    for model_id in MODELS:
        await DP.feed_update(bot, callback(ui.Card(model_id=model_id).pack()), store=store)
    assert len(session.named("EditMessageText")) == len(screens) + len(MODELS)
    assert len(session.named("AnswerCallbackQuery")) == len(screens) + len(MODELS)


@pytest.mark.parametrize(
    ("text", "first_button"),
    [
        ("/start", "⁂ Модели"),
        ("/models", "✻ Claude Opus 5.5"),
        ("/other", "⌬ GPT-6.1 Sol"),
        ("/settings", "Модель · Claude Opus 5.5"),
        ("просто текст", "⁂ Модели"),
    ],
)
async def test_commands_and_text_get_a_screen(tg, text, first_button):
    bot, session, store = tg
    update = Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids),
            date=datetime.now(),
            chat=Chat(id=42, type="private"),
            from_user=User(id=42, is_bot=False, first_name="Neo"),
            text=text,
        ),
    )
    await DP.feed_update(bot, update, store=store)
    sent = session.named("SendMessage")[-1]
    assert buttons(sent.reply_markup)[0].text == first_button
