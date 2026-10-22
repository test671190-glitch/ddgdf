"""Каркас бота моделей (models_bot): меню, настройки, тестовые ответы и оформление
без эмодзи. Апдейты идут через настоящий Dispatcher и фейковую сессию Bot API."""

import itertools
import json
import sqlite3
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from models_bot import answers, notify, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, OTHER_MODELS, ROLES, TOP_MODELS
from models_bot.handlers import router
from models_bot.store import HISTORY_LIMIT, Prefs, Store, Turn

BOT_USER = User(id=4242, is_bot=True, first_name="models", username="modelsbot")
_ids = itertools.count(1)

# Все символы оформления. Ни у одного нет свойства Emoji в Unicode (emoji-data.txt),
# поэтому Telegram не заменит их на цветные эмодзи
DECOR = set("✻⌬✧⁂⛭⌂ⓘ✎↺→←↓↳✓·—«»…")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("AnswerCallbackQuery", "SendChatAction", "EditMessageReplyMarkup"):
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

    def sent(self) -> list[str]:
        return [c.text for c in self.named("SendMessage")]


# Роутер модульный: подключить его можно только к одному Dispatcher
DP = Dispatcher()
DP.include_router(router)


@pytest.fixture
def tg(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "TYPING_SECONDS", 0)
    monkeypatch.setattr(notify, "SEND_PAUSE", 0)
    DP.fsm.storage = MemoryStorage()  # ввод своей роли не переходит из теста в тест
    session = FakeSession()
    bot = Bot("4242:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, session, Store(tmp_path / "settings.db")


def neo(uid: int = 42) -> User:
    return User(id=uid, is_bot=False, first_name="Neo")


def callback(data: str, uid: int = 42) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=uid, type="private"),
        from_user=BOT_USER,
        text="old",
    )
    query = CallbackQuery(
        id=str(next(_ids)), from_user=neo(uid), chat_instance="ci", data=data, message=message
    )
    return Update(update_id=next(_ids), callback_query=query)


def message(text: str | None = None, uid: int = 42, **extra) -> Update:
    msg = Message(
        message_id=next(_ids),
        date=datetime.now(),
        chat=Chat(id=uid, type="private"),
        from_user=neo(uid),
        text=text,
        **extra,
    )
    return Update(update_id=next(_ids), message=msg)


async def say(tg, *texts: str) -> None:
    bot, _, store = tg
    for text in texts:
        await DP.feed_update(bot, message(text), store=store)


async def press(tg, data) -> None:
    bot, _, store = tg
    await DP.feed_update(bot, callback(data.pack()), store=store)


def buttons(markup) -> list:
    return [b for row in markup.inline_keyboard for b in row] if markup else []


def all_screens() -> list[tuple[str, object]]:
    prefs = Prefs()
    custom = Prefs(role=CUSTOM_ROLE, role_text="Отвечай как юрист", memory=False)
    model = MODELS[prefs.model]
    screens = [
        (ui.welcome("Neo", prefs), ui.main_keyboard()),
        (ui.models_text(), ui.models_keyboard()),
        (ui.other_text(), ui.other_keyboard()),
        (ui.model_card(model, prefs), ui.card_keyboard(model, prefs, "other")),
        (ui.settings_text(prefs, 1234), ui.settings_keyboard(prefs)),
        (ui.picker_text(), ui.picker_keyboard(prefs)),
        (ui.role_text(custom, note="✓ Своя роль сохранена"), ui.role_keyboard(custom)),
        (ui.role_input_text(), ui.role_input_keyboard()),
        (ui.notify_text(), ui.notify_keyboard(prefs)),
        (ui.new_models_text(OTHER_MODELS), ui.new_models_keyboard(OTHER_MODELS)),
        (ui.HELP, ui.answer_keyboard(prefs)),
        (answers.limit_reached(), None),
        (answers.limit_notices(0, 16_000)[0], None),
    ]
    history = [Turn("user", "Как дела?", 3), Turn("bot", "Хорошо", 2)]
    for role in [*ROLES, CUSTOM_ROLE]:
        for lang in ("ru", "en"):
            p = Prefs(role=role, role_text="Будь краток", lang=lang)
            reply = answers.compose(model, p, "Привет", history)
            screens.append((reply.html + answers.usage_line(reply, 100), None))
    return screens


# Меню и оформление


def test_main_menu_is_models_and_settings_in_one_row():
    rows = ui.main_keyboard().inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["⁂ Модели", "⛭ Настройки"]]


def test_models_menu_lists_claude_then_gpt_then_other():
    texts = [b.text for b in buttons(ui.models_keyboard())]
    assert texts[:3] == ["✻ Claude Opus 5.5", "✻ Claude Sonnet 5.5", "✻ Claude Haiku 5.5"]
    assert texts[3:6] == ["⌬ GPT-6 Sol", "⌬ GPT-6 Astra", "⌬ GPT-6 Luna"]
    assert texts[6:] == ["Остальные модели →", "← В меню"]


def test_settings_lists_every_setting():
    texts = [b.text for b in buttons(ui.settings_keyboard(Prefs()))]
    assert texts == [
        "Модель · Claude Opus 5.5",
        "Роль · Без роли",
        "Память · вкл",
        "Расход · вкл",
        "Уведомления →",
        "Язык · русский",
        "Ответы · подробные",
        "← В меню",
    ]


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
    assert back(ui.role_keyboard(prefs)) == "settings"
    assert back(ui.notify_keyboard(prefs)) == "settings"
    assert back(ui.role_input_keyboard()) == "role"


def test_other_models_are_gpt_6_1_and_gemini_only():
    assert [m.vendor for m in OTHER_MODELS] == ["OpenAI"] + ["Google"] * 5
    assert {m.vendor for m in TOP_MODELS} == {"Anthropic", "OpenAI"}


def test_decor_is_symbols_not_emoji():
    commands = [c.description for c in ui.COMMANDS]
    for text, markup in all_screens():
        for s in [text, *commands, *(b.text for b in buttons(markup))]:
            odd = {c for c in s if ord(c) > 127 and not c.isalpha() and c not in DECOR}
            assert not odd, (s, odd)
            assert "️" not in s


def test_callback_data_fits_telegram_limit():
    for model_id in MODELS:
        for src in ("models", "other", "picker"):
            assert len(ui.UseModel(model_id=model_id, src=src).pack().encode()) <= 64
    for key in [*ROLES, CUSTOM_ROLE]:
        assert len(ui.SetRole(key=key).pack().encode()) <= 64


# Хранилище


def test_store_keeps_prefs_and_forgets_removed_models(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get(1) == Prefs()
    store.update(1, model="gemini-4-argon", lang="en", memory=False)
    assert Store(tmp_path / "s.db").get(1) == Prefs("gemini-4-argon", "en", memory=False)
    store.update(1, model="grok-4.7", role="poet")  # такой модели и роли больше нет
    assert store.get(1).model == Prefs().model
    assert store.get(1).role == "none"


def test_store_upgrades_old_settings_table(tmp_path):
    """На сервере уже есть база с тремя настройками: новые столбцы добавляются,
    прежние значения остаются."""
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute(
        "CREATE TABLE prefs (user_id INTEGER PRIMARY KEY, model TEXT, lang TEXT, detail TEXT)"
    )
    db.execute("INSERT INTO prefs VALUES (7, 'gpt-6-sol', 'en', 'short')")
    db.commit()
    db.close()
    store = Store(tmp_path / "old.db")
    assert store.get(7) == Prefs(model="gpt-6-sol", lang="en", detail="short")
    assert store.users_with("notify_models") == [7]


def test_history_keeps_only_last_messages(tmp_path):
    store = Store(tmp_path / "s.db")
    for i in range(HISTORY_LIMIT + 5):
        store.remember(1, Turn("user", f"q{i}", 1))
    history = store.history(1)
    assert len(history) == HISTORY_LIMIT
    assert history[-1].text == f"q{HISTORY_LIMIT + 4}"
    store.forget(1)
    assert store.history(1) == []


def test_spend_counts_tokens_for_today(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.spend(1, 100) == (0, 100)
    assert store.spend(1, 50) == (100, 150)
    assert store.used_today(1) == 150
    assert store.used_today(2) == 0


def test_tokens_and_plural():
    assert answers.count_tokens("") == 0
    assert answers.count_tokens("да") == 1
    assert answers.count_tokens("12345678") == 2
    assert answers.plural(1, "токен", "токена", "токенов") == "1 токен"
    assert answers.plural(3, "токен", "токена", "токенов") == "3 токена"
    assert answers.plural(12, "токен", "токена", "токенов") == "12 токенов"
    assert answers.plural(20000, "токен", "токена", "токенов") == "20 000 токенов"


# Экраны и настройки


async def test_every_screen_opens(tg):
    _, session, _ = tg
    screens = ("main", "models", "other", "settings", "picker", "role", "notify")
    for to in screens:
        await press(tg, ui.Nav(to=to))
    for model_id in MODELS:
        await press(tg, ui.Card(model_id=model_id))
    assert len(session.named("EditMessageText")) == len(screens) + len(MODELS)
    assert len(session.named("AnswerCallbackQuery")) == len(screens) + len(MODELS)


@pytest.mark.parametrize(
    ("text", "first_button"),
    [
        ("/start", "⁂ Модели"),
        ("/models", "✻ Claude Opus 5.5"),
        ("/other", "⌬ GPT-6.1 Sol"),
        ("/settings", "Модель · Claude Opus 5.5"),
        ("/unknown", "⁂ Модели"),
    ],
)
async def test_commands_get_a_screen(tg, text, first_button):
    await say(tg, text)
    sent = tg[1].named("SendMessage")[-1]
    assert buttons(sent.reply_markup)[0].text == first_button


async def test_make_model_default_from_card(tg):
    _, session, store = tg
    await press(tg, ui.UseModel(model_id="gpt-6-sol"))
    assert store.get(42).model == "gpt-6-sol"
    assert "✓ Основная модель" in session.named("EditMessageText")[-1].text
    assert session.named("AnswerCallbackQuery")[-1].text == "✓ Основная модель · GPT-6 Sol"


async def test_picker_returns_to_settings_with_new_model(tg):
    _, session, _ = tg
    await press(tg, ui.UseModel(model_id="gemini-3.8-flash", src="picker"))
    edit = session.named("EditMessageText")[-1]
    assert "Модель — ✧ Gemini 3.8 Flash" in edit.text
    assert buttons(edit.reply_markup)[0].text == "Модель · Gemini 3.8 Flash"


async def test_toggles_flip_settings(tg):
    _, session, store = tg
    for key in ui.TOGGLES:
        await press(tg, ui.Toggle(key=key))
    assert store.get(42) == Prefs(
        lang="en",
        detail="short",
        memory=False,
        show_usage=False,
        notify_models=False,
        notify_limit=False,
    )
    # переключатели уведомлений остаются на своём экране
    assert (
        buttons(session.named("EditMessageText")[-1].reply_markup)[0].text == "Новые модели · выкл"
    )
    assert session.named("AnswerCallbackQuery")[-1].text == "✓ Окончание лимита · выкл"
    await press(tg, ui.Toggle(key="lang"))
    assert store.get(42).lang == "ru"


async def test_preset_role(tg):
    _, session, store = tg
    await press(tg, ui.SetRole(key="lawyer"))
    assert store.get(42).role == "lawyer"
    assert "Юрист  ✓" in [
        b.text for b in buttons(session.named("EditMessageText")[-1].reply_markup)
    ]
    await say(tg, "Можно ли расторгнуть договор?")
    assert "С юридической точки зрения" in session.sent()[-1]


async def test_custom_role_is_saved_from_next_message(tg):
    _, session, store = tg
    await press(tg, ui.SetRole(key=CUSTOM_ROLE))
    assert "Пришлите инструкцию" in session.named("EditMessageText")[-1].text
    await say(tg, "Отвечай как пират")
    prefs = store.get(42)
    assert (prefs.role, prefs.role_text) == (CUSTOM_ROLE, "Отвечай как пират")
    assert "✓ Своя роль сохранена" in session.sent()[-1]
    await say(tg, "Привет")  # дальше — обычный диалог в этой роли
    assert "Отвечаю в вашей роли" in session.sent()[-1]


async def test_custom_role_input_ends_on_command_or_button(tg):
    _, session, store = tg
    await press(tg, ui.SetRole(key=CUSTOM_ROLE))
    await say(tg, "/start", "Привет")
    assert store.get(42).role == "none"
    assert "тестовый ответ" in session.sent()[-1]

    await press(tg, ui.SetRole(key=CUSTOM_ROLE))
    await press(tg, ui.Nav(to="role"))  # «← Отмена»
    await say(tg, "Привет")
    assert store.get(42).role == "none"


async def test_custom_role_too_long_stays_in_input(tg):
    _, session, store = tg
    await press(tg, ui.SetRole(key=CUSTOM_ROLE))
    await say(tg, "а" * 501, "Короткая роль")
    assert "Слишком длинно: 501" in session.sent()[0]
    assert store.get(42).role_text == "Короткая роль"


# Диалог с тестовой моделью


async def test_answer_shows_model_usage_and_new_dialog_button(tg):
    _, session, store = tg
    await say(tg, "Привет")
    answer = session.named("SendMessage")[-1]
    assert answer.text.startswith("<b>✻ Claude Opus 5.5</b> · тестовый ответ")
    assert "<i>↳ " in answer.text and "сегодня" in answer.text
    assert [b.text for b in buttons(answer.reply_markup)] == ["↺ Новый диалог"]
    assert session.named("SendChatAction")
    assert store.used_today(42) > 0


async def test_memory_recalls_previous_question_until_new_dialog(tg):
    _, session, _ = tg
    await say(tg, "Как выбрать ноутбук?", "А для игр?")
    assert "вы спрашивали «Как выбрать ноутбук?»" in session.sent()[-1]
    assert "в контексте 2 сообщения" in session.sent()[-1]
    await press(tg, ui.NewDialog())
    assert session.sent()[-1] == ui.NEW_DIALOG_DONE
    await say(tg, "Привет")
    assert "вы спрашивали" not in session.sent()[-1]


async def test_memory_off_answers_each_question_alone(tg):
    _, session, store = tg
    store.update(42, memory=False)
    await say(tg, "Первый", "Второй")
    assert "вы спрашивали" not in session.sent()[-1]
    assert "каждый вопрос отдельно" in session.sent()[-1]
    assert session.named("SendMessage")[-1].reply_markup is None
    assert store.history(42) == []


async def test_memory_grows_input_tokens(tg):
    _, session, _ = tg
    await say(tg, "Привет", "Привет")
    first, second = (t for t in session.sent())
    assert answers.count_tokens("Привет") < int(second.split("вход ")[1].split(" ")[0])
    assert first.split("вход ")[1].split(" ")[0] == str(answers.count_tokens("Привет"))


async def test_usage_and_detail_can_be_hidden(tg):
    _, session, store = tg
    store.update(42, show_usage=False, detail="short", lang="en")
    await say(tg, "Hello")
    text = session.sent()[-1]
    assert "↳" not in text and "<blockquote>" not in text
    assert "test reply" in text


async def test_limit_warns_then_blocks(tg, monkeypatch):
    _, session, store = tg
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    store.update(42, memory=False)
    for _ in range(10):
        await say(tg, "Привет")
    texts = session.sent()
    assert sum("Израсходовано 80%" in t for t in texts) == 1
    assert sum("исчерпан" in t for t in texts) >= 2  # уведомление и отказ отвечать
    assert texts[-1] == answers.limit_reached()


async def test_limit_without_notifications_only_blocks(tg, monkeypatch):
    _, session, store = tg
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    store.update(42, memory=False, notify_limit=False)
    for _ in range(10):
        await say(tg, "Привет")
    texts = session.sent()
    assert not any("Израсходовано" in t for t in texts)
    assert texts[-1] == answers.limit_reached()


async def test_photo_gets_hint(tg):
    bot, session, store = tg
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    await DP.feed_update(bot, message(photo=photo), store=store)
    assert session.sent()[-1] == ui.ONLY_TEXT


# Уведомления о новых моделях


async def test_new_models_are_announced_to_subscribers(tg):
    bot, session, store = tg
    assert await notify.announce_new_models(bot, store) == 0  # первый запуск
    store.touch(1)
    store.update(2, notify_models=False)
    store.touch(3)
    known = [m for m in MODELS if m != "gemini-4-argon"]
    store.set_meta(notify.KNOWN_MODELS, json.dumps(known))
    assert await notify.announce_new_models(bot, store) == 2
    sent = session.named("SendMessage")
    assert {m.chat_id for m in sent} == {1, 3}
    assert "✧ Gemini 4 Argon" in sent[0].text
    assert await notify.announce_new_models(bot, store) == 0  # повторно не пишет


async def test_any_visitor_gets_new_model_notices(tg):
    _, _, store = tg
    await say(tg, "/start")
    assert store.users_with("notify_models") == [42]
