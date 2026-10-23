"""Бот моделей (models_bot): чат без лишних шагов, переключение чатов и моделей, настройки,
тестовые ответы, баланс, оформление без эмодзи и мини-клиент Bot API.
Апдейты — dict как от Telegram, API — фейковый."""

import itertools
import json
import signal
import socket
import sqlite3
import threading
import time

import pytest

from models_bot import answers, notify, tg, ui
from models_bot.catalog import (
    CUSTOM_ROLE,
    LEGACY_ROLES,
    MODELS,
    NO_ROLE,
    OTHER_MODELS,
    STYLES,
    TOOLS,
    TOP_MODELS,
)
from models_bot.handlers import KEYBOARD_VERSION, SCREENS, Bot
from models_bot.store import HISTORY_LIMIT, MAX_CHATS, Chat, Prefs, Store, Turn

_ids = itertools.count(1)

# Все символы оформления. Ни у одного нет свойства Emoji в Unicode (emoji-data.txt),
# поэтому Telegram не заменит их на цветные эмодзи
DECOR = set("✻⌬✧⁂⛭⌂ⓘ✎✕☰↺▸■◈↻⇄−▍+◐▣◩✶◎✐≡⌘→←↓↳✓·•—«»…×–")
OPUS = MODELS["claude-opus-5-5"]


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.messages: dict[int, dict] = {}  # id → параметры последней отправки или правки

    def __call__(self, method: str, _timeout: float = 0, **params):
        self.calls.append((method, params))
        if method in self.fail:
            raise self.fail[method]
        if method == "sendMessage":
            message_id = next(_ids)
            self.messages[message_id] = params
            return {"message_id": message_id}
        if method == "editMessageText":
            if params["message_id"] in self.messages:
                self.messages[params["message_id"]] = params
            return {"message_id": params["message_id"]}
        return True

    def named(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def sent(self) -> list[str]:
        """Тексты отправленных сообщений в их нынешнем виде (с учётом правок)."""
        return [p["text"] for p in self.messages.values()]

    def final(self) -> dict:
        """Последнее отправленное сообщение в нынешнем виде."""
        return list(self.messages.values())[-1]

    def last(self) -> dict:
        """Последний показанный экран: отправленный или отредактированный."""
        return next(p for m, p in reversed(self.calls) if m in ("sendMessage", "editMessageText"))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "STREAM_DELAY", 0)
    monkeypatch.setattr(notify, "SEND_PAUSE", 0)
    store = Store(tmp_path / "settings.db")
    store.update(42, kb=KEYBOARD_VERSION)  # пользователь уже с новой клавиатурой
    return Bot(FakeApi(), store)


USER = {"id": 42, "is_bot": False, "first_name": "Neo"}


def say(bot: Bot, *texts: str | None, chat_type: str = "private", **extra) -> None:
    for text in texts:
        message = {
            "message_id": next(_ids),
            "date": 1,
            "chat": {"id": 42 if chat_type == "private" else -100, "type": chat_type},
            "from": USER,
            **({"text": text} if text is not None else {}),
            **extra,
        }
        bot.handle({"update_id": next(_ids), "message": message})


def press(bot: Bot, data_: str, date: int = 1, uid: int = 42, message_id: int = 7) -> None:
    message = {"message_id": message_id, "date": date, "chat": {"id": uid, "type": "private"}}
    user = {**USER, "id": uid}
    query = {"id": str(next(_ids)), "from": user, "data": data_, "message": message}
    bot.handle({"update_id": next(_ids), "callback_query": query})


def buttons(markup: dict | None) -> list[dict]:
    if not markup:
        return []
    rows = markup.get("inline_keyboard") or markup.get("keyboard") or []
    return [b for row in rows for b in row]


def texts(markup: dict | None) -> list[str]:
    return [b["text"] for b in buttons(markup)]


def data(markup: dict | None) -> list[str]:
    return [b.get("callback_data", "") for b in buttons(markup)]


def active(bot: Bot) -> Chat | None:
    prefs = bot.store.get(42)
    return bot.store.chat(prefs.active) if prefs.active else None


def history(bot: Bot) -> list[Turn]:
    chat = active(bot)
    return bot.store.history(chat.id) if chat else []


def answer_text(bot: Bot) -> str:
    return bot.api.final()["text"]


def all_screens() -> list[tuple[str, dict | None]]:
    prefs = Prefs()
    custom = Prefs(role=CUSTOM_ROLE, role_text="Отвечай как юрист", active=1)
    chat = Chat(1, 42, "gpt-6-sol", "Как выбрать ноутбук", 3, time.time())
    empty = Chat(2, 42, "claude-opus-5-5", "", 0, time.time() - 86400 * 3)
    gone = Chat(3, 42, "grok-4.7", "Старый чат", 1, 0)
    model = MODELS[prefs.model]
    screens = [
        (ui.welcome("Neo", model), ui.keyboard(model)),
        (ui.welcome("Neo", model, chat), ui.menu_keyboard()),
        (ui.menu_text(model, chat, custom) + ui.menu_text(model, None, prefs), None),
        (ui.models_text(), ui.models_keyboard()),
        (ui.other_text(), ui.other_keyboard()),
        (ui.model_card(model, model.id), ui.card_keyboard(model, "other", model.id)),
        (ui.model_card(model), ui.card_keyboard(model, "models")),
        (ui.picker_text(model), ui.picker_keyboard(model.id)),
        (ui.model_switched(model, chat) + ui.model_switched(model, None), None),
        (ui.new_chat_text(model, chat, [empty]) + ui.already_new(model), None),
        (ui.chat_opened(chat, model, "Вопрос <b>", custom), None),
        (
            ui.chats_text([chat, empty, gone], chat, False),
            ui.chats_keyboard([chat, empty, gone], 1, 0, False),
        ),
        (ui.chats_text([chat], None, True), ui.chats_keyboard([chat], 0, 0, True)),
        (ui.chats_text([], None, False), ui.chats_keyboard([], 0, 0, False)),
        (ui.chat_info_text(chat, 1, "Вопрос"), ui.chat_info_keyboard(chat, 1)),
        (ui.chat_info_text(gone, 1, None), ui.chat_info_keyboard(gone, 1)),
        (ui.rename_text(chat) + ui.title_too_long(41) + ui.TITLE_SAVED, ui.rename_keyboard(chat)),
        (ui.delete_text(chat) + ui.chat_deleted(chat) + ui.CHAT_GONE, ui.delete_keyboard(chat)),
        (ui.NO_CLOSING + ui.KEYBOARD_UPDATED, None),
        (ui.settings_text(prefs, 1234), ui.settings_keyboard(prefs)),
        (ui.role_text(custom, note=ui.ROLE_SAVED), ui.role_keyboard(custom)),
        (ui.role_input_text(), ui.role_input_keyboard()),
        (ui.role_too_long(501), None),
        (ui.notify_text(), ui.notify_keyboard(prefs)),
        (ui.new_models_text(OTHER_MODELS), ui.new_models_keyboard(OTHER_MODELS)),
        (ui.balance_text(prefs, 100), ui.balance_keyboard()),
        (ui.paid(100_000, 100_000) + ui.PAY_SUPPORT, ui.buy_keyboard()),
        (ui.cant_read(model, "voice"), ui.answer_keyboard(1)),
        (ui.HELP, ui.other_model_keyboard(1, model.id)),
        (ui.STALE_BUTTON + ui.ONLY_TEXT + ui.ROLE_ONLY_TEXT + ui.TITLE_ONLY_TEXT, None),
        (answers.limit_reached() + answers.BALANCE_EMPTY, None),
        (answers.limit_notices(0, 16_000)[0], None),
        (answers.limit_notices(0, 30_000, 5)[0], None),
        *((text, None) for text in SCREENS.values()),
    ]
    turns = [Turn("user", "Как дела?", 3), Turn("bot", "Хорошо", 2)]
    photo = answers.Attachment("image", "изображение 10×10", 800)
    for role in (NO_ROLE, CUSTOM_ROLE):
        for lang in ("ru", "en"):
            p = Prefs(role=role, role_text="Будь краток", lang=lang)
            reply = answers.compose(model, p, "Привет", turns, attachment=photo)
            screens.append((reply.html + answers.usage_line(reply, 100, 5), None))
            for tool in [*TOOLS, *(f"styles:{s}" for s in STYLES)]:
                reply = answers.compose(
                    model, p, "Тема", turns, attempt=1, attachment=photo, tool=tool
                )
                screens.append((reply.html, None))
    screens += [
        (ui.tools_text(model), ui.tools_keyboard()),
        (ui.styles_text(), ui.styles_keyboard()),
        *((ui.tool_ask_text(k, "anime") + ui.tool_needs(k), ui.tool_ask_keyboard()) for k in TOOLS),
        (ui.role_text(Prefs()) + ui.ROLE_RESET, ui.role_keyboard(Prefs())),
    ]
    return screens


# Клавиатура, меню и оформление


def test_keyboard_below_has_the_four_actions():
    board = ui.keyboard(OPUS)
    assert texts(board) == ["+ Новый чат", "☰ Мои чаты", "⁂ Модель", "⛭ Меню"]
    assert (
        board["is_persistent"]
        and board["input_field_placeholder"] == "Сообщение для Claude Opus 5.5"
    )


def test_menu_has_tools_models_chats_balance_and_settings():
    assert texts(ui.menu_keyboard()) == [
        "⌘ Инструменты",
        "⁂ Модели",
        "☰ Мои чаты",
        "◈ Мой баланс",
        "⛭ Настройки",
    ]


def test_models_menu_lists_claude_then_gpt_then_other():
    names = texts(ui.models_keyboard())
    assert names[:3] == ["✻ Claude Opus 5.5", "✻ Claude Sonnet 5.5", "✻ Claude Haiku 5.5"]
    assert names[3:6] == ["⌬ GPT-6.1 Sol", "⌬ GPT-6 Astra", "⌬ GPT-6 Luna"]
    assert names[6:] == ["Остальные модели →", "← В меню"]


def test_settings_lists_every_setting():
    assert texts(ui.settings_keyboard(Prefs())) == [
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
    def back(markup: dict) -> str:
        return data(markup)[-1]

    model, prefs = MODELS["gemini-4-argon"], Prefs()
    chat = Chat(5, 42, model.id, "", 1, 0)
    assert back(ui.models_keyboard()) == "nav:main"
    assert back(ui.other_keyboard()) == "nav:models"
    assert back(ui.card_keyboard(model, "other")) == "nav:other"
    assert back(ui.card_keyboard(model, "models")) == "nav:models"
    assert back(ui.settings_keyboard(prefs)) == "nav:main"
    assert back(ui.role_keyboard(prefs)) == "nav:settings"
    assert back(ui.notify_keyboard(prefs)) == "nav:settings"
    assert back(ui.role_input_keyboard()) == "nav:role"
    assert back(ui.chats_keyboard([chat], 0, 0, False)) == "nav:main"
    assert back(ui.chat_info_keyboard(chat, 0)) == "chats:0:e"
    assert back(ui.rename_keyboard(chat)) == "info:5"
    assert back(ui.delete_keyboard(chat)) == "info:5"
    assert back(ui.balance_keyboard()) == "nav:main"


def test_other_models_are_gpt_6_sol_and_gemini_only():
    assert [m.vendor for m in OTHER_MODELS] == ["OpenAI"] + ["Google"] * 5
    assert {m.vendor for m in TOP_MODELS} == {"Anthropic", "OpenAI"}


def test_decor_is_symbols_not_emoji():
    commands = [c["description"] for c in ui.COMMANDS]
    for text, markup in all_screens():
        for s in [text, *commands, *texts(markup)]:
            odd = {c for c in s if ord(c) > 127 and not c.isalpha() and c not in DECOR}
            assert not odd, (s, odd)
            assert "️" not in s


def test_callback_data_and_placeholder_fit_telegram_limits():
    markups = [m for _, m in all_screens() if m and "inline_keyboard" in m]
    for model in MODELS.values():
        markups += [ui.card_keyboard(model, "other"), ui.picker_keyboard(model.id)]
        markups.append(ui.other_model_keyboard(10**12, model.id))
        assert len(ui.keyboard(model)["input_field_placeholder"]) <= 64
    big = Chat(10**12, 42, "gemini-3.1-pro-preview", "x" * 40, 1, 0)
    markups += [ui.chat_info_keyboard(big, 0), ui.chats_keyboard([big] * 30, 0, 3, True)]
    for markup in markups:
        for b in buttons(markup):
            assert len(b.get("callback_data", "").encode()) <= 64


def test_escape_and_plain_text():
    assert answers.escape("a<b>&c") == "a&lt;b&gt;&amp;c"
    assert answers.plain_text("<b>x</b> &lt;y&gt; &amp;lt; &quot;") == 'x <y> &lt; "'
    assert ui.role_too_long(501).startswith("Слишком длинно: 501 символ из 500")


def test_when_is_moscow_time():
    now = time.mktime((2026, 10, 22, 16, 0, 0, 0, 0, 0)) - time.timezone  # 16:00 UTC
    assert ui.when(int(now - 3600), now) == "сегодня 18:00"
    assert ui.when(int(now - 86400), now) == "вчера 19:00"
    assert ui.when(int(now - 86400 * 3), now) == "19.10"


# Хранилище


def test_store_keeps_prefs_and_forgets_removed_models(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get(1) == Prefs()
    store.update(1, model="gemini-4-argon", lang="en", memory=False)
    assert Store(tmp_path / "s.db").get(1) == Prefs("gemini-4-argon", "en", memory=False)
    store.update(1, model="grok-4.7", role="poet", awaiting="x")
    assert store.get(1) == Prefs(lang="en", memory=False)
    store.update(1, awaiting="rename:12")
    assert store.get(1).awaiting == "rename:12"


def test_store_upgrades_old_settings_table(tmp_path):
    """На сервере уже есть база первой версии: новые столбцы добавляются,
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


def test_store_moves_single_chat_into_chat_list(tmp_path):
    """В одной из прошлых версий чат был один: модель в prefs.chat, история без chat_id.
    Он становится первым чатом в списке и остаётся текущим, с перепиской."""
    db = sqlite3.connect(tmp_path / "v2.db")
    db.executescript(
        "CREATE TABLE prefs (user_id INTEGER PRIMARY KEY, model TEXT, chat TEXT);"
        "INSERT INTO prefs VALUES (7, 'gpt-6-sol', 'gemini-4-argon');"
        "INSERT INTO prefs VALUES (8, 'gpt-6-sol', '');"
        "CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,"
        " author TEXT, text TEXT, tokens INTEGER);"
        "INSERT INTO history (user_id, author, text, tokens) VALUES (7, 'user', 'Как дела?', 3);"
        "INSERT INTO history (user_id, author, text, tokens) VALUES (7, 'bot', 'Хорошо', 2);"
        "INSERT INTO history (user_id, author, text, tokens) VALUES (8, 'user', 'Закрытый', 2);"
    )
    db.commit()
    db.close()
    store = Store(tmp_path / "v2.db")
    prefs = store.get(7)
    chat = store.chat(prefs.active)
    assert (chat.user_id, chat.model, chat.title, chat.messages) == (
        7,
        "gemini-4-argon",
        "Как дела?",
        1,
    )
    assert [t.text for t in store.history(chat.id)] == ["Как дела?", "Хорошо"]
    assert prefs.chat == ""
    assert store.chats(8) == [] and store.get(8).active == 0
    Store(tmp_path / "v2.db")  # повторный запуск ничего не дублирует
    assert len(store.chats(7)) == 1


def test_chats_keep_own_history_and_order(tmp_path):
    store = Store(tmp_path / "s.db")
    a, _ = store.new_chat(1, "gpt-6-sol")
    b, _ = store.new_chat(1, "gemini-4-argon")
    store.remember(a.id, 1, Turn("user", "про A", 1))
    store.note_question(a.id, "про A")
    assert [c.id for c in store.chats(1)] == [a.id, b.id]  # последний вопрос — наверху
    assert store.history(b.id) == []
    assert store.chat(a.id).title == "про A"
    store.note_question(a.id, "второй вопрос")
    assert store.chat(a.id).title == "про A"  # название — из первого вопроса
    store.set_chat_model(a.id, "claude-opus-5-5")
    assert store.chat(a.id).model == "claude-opus-5-5"
    for i in range(HISTORY_LIMIT + 5):
        store.remember(a.id, 1, Turn("user", f"q{i}", 1))
    assert len(store.history(a.id)) == HISTORY_LIMIT
    store.delete_chat(a.id)
    assert store.chat(a.id) is None and store.history(a.id) == []


def test_too_many_chats_drop_the_oldest(tmp_path):
    store = Store(tmp_path / "s.db")
    first, _ = store.new_chat(1, "gpt-6-sol")
    for _ in range(MAX_CHATS - 1):
        _, dropped = store.new_chat(1, "gpt-6-sol")
        assert dropped == []
    _, dropped = store.new_chat(1, "gpt-6-sol")
    assert [c.id for c in dropped] == [first.id]
    assert len(store.chats(1)) == MAX_CHATS


def test_spend_counts_tokens_for_today(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.spend(1, 100) == (0, 100)
    assert store.spend(1, 50) == (100, 150)
    assert store.used_today(1) == 150
    assert store.used_today(2) == 0


def test_store_is_safe_from_many_threads(tmp_path):
    store = Store(tmp_path / "s.db")

    def work(uid: int) -> None:
        chat, _ = store.new_chat(uid, "gpt-6-sol")
        for _ in range(50):
            store.touch(uid)
            store.spend(uid, 1)
            store.remember(chat.id, uid, Turn("user", "q", 1))
            store.note_question(chat.id, "q")
            store.update(uid, memory=not store.get(uid).memory)

    threads = [threading.Thread(target=work, args=(uid,)) for uid in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(store.used_today(uid) == 50 for uid in range(8))
    assert all(store.chats(uid)[0].messages == 50 for uid in range(8))
    assert sorted(store.users_with("notify_models")) == list(range(8))


def test_tokens_and_plural():
    assert answers.count_tokens("") == 0
    assert answers.count_tokens("да") == 1
    assert answers.count_tokens("12345678") == 2
    assert answers.plural(1, "токен", "токена", "токенов") == "1 токен"
    assert answers.plural(3, "токен", "токена", "токенов") == "3 токена"
    assert answers.plural(12, "токен", "токена", "токенов") == "12 токенов"
    assert answers.plural(20000, "токен", "токена", "токенов") == "20 000 токенов"


# Экраны и команды


def test_every_screen_and_card_opens(bot):
    for name in SCREENS:
        press(bot, f"nav:{name}")
    for model_id in MODELS:
        press(bot, f"card:{model_id}:models")
    assert len(bot.api.named("editMessageText")) == len(SCREENS) + len(MODELS)
    assert len(bot.api.named("answerCallbackQuery")) == len(SCREENS) + len(MODELS)


@pytest.mark.parametrize(
    ("text", "first_button"),
    [
        ("/start", "+ Новый чат"),  # приветствие — с клавиатурой внизу
        ("/menu", "⌘ Инструменты"),
        ("/tools", "◐ Фотостили"),
        ("/model", "✻ Claude Opus 5.5  ✓"),
        ("/models", "✻ Claude Opus 5.5"),
        ("/other", "⌬ GPT-6 Sol"),
        ("/chats", "+ Новый чат"),
        ("/settings", "Модель · Claude Opus 5.5"),
        ("/balance", "+ 100 000 токенов · 50 Stars"),
        ("/start@modelsbot", "+ Новый чат"),
        ("/unknown", "+ Новый чат"),
        (ui.MENU, "⌘ Инструменты"),
        (ui.MODEL, "✻ Claude Opus 5.5  ✓"),
        (ui.MY_CHATS, "+ Новый чат"),
    ],
)
def test_commands_and_buttons_get_a_screen(bot, text, first_button):
    say(bot, text)
    assert texts(bot.api.last()["reply_markup"])[0] == first_button


def test_stale_button_answers_and_opens_menu(bot):
    """Кнопки прошлых версий бота не должны крутить часики бесконечно."""
    press(bot, "zzz:gpt-6-sol:-1")
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == ui.STALE_BUTTON
    assert texts(bot.api.last()["reply_markup"]) == texts(ui.menu_keyboard())


def test_old_message_screen_is_sent_anew(bot):
    press(bot, "nav:models", date=0)  # недоступное сообщение
    assert not bot.api.named("editMessageText")
    assert bot.api.sent()[-1] == ui.models_text()


def test_not_modified_edit_is_quiet(bot):
    bot.api.fail["editMessageText"] = tg.TelegramError(400, "Bad Request: message is not modified")
    press(bot, "nav:models")
    assert not bot.api.named("sendMessage")


def test_toggles_flip_settings(bot):
    for key in ui.TOGGLES:
        press(bot, f"tgl:{key}")
    prefs = bot.store.get(42)
    assert (prefs.lang, prefs.detail, prefs.memory, prefs.show_usage) == (
        "en",
        "short",
        False,
        False,
    )
    assert (prefs.notify_models, prefs.notify_limit) == (False, False)
    # переключатели уведомлений остаются на своём экране
    assert texts(bot.api.last()["reply_markup"])[0] == "Новые модели · выкл"
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == "✓ Окончание лимита · выкл"


# Чат: просто пишите


def test_just_typing_starts_a_chat_and_answers_at_once(bot):
    say(bot, "Как выбрать ноутбук?")
    chat = active(bot)
    assert chat is not None and chat.model == "claude-opus-5-5"
    assert chat.title == "Как выбрать ноутбук?" and chat.messages == 1
    first = bot.api.named("sendMessage")[0]
    assert first["text"].endswith(ui.CURSOR)  # первая часть ответа — сразу, без «печатает…»
    assert not bot.api.named("sendChatAction")
    final = answer_text(bot)
    assert final.startswith("<i>+ Новый чат</i>\n<b>✻ Claude Opus 5.5</b>")
    assert texts(bot.api.final()["reply_markup"]) == [ui.REGENERATE, ui.OTHER_MODEL, ui.SHORTER]
    assert len(bot.api.messages) == 1  # одно сообщение: никаких «чат начат»


def test_next_messages_go_to_the_same_chat(bot):
    say(bot, "Как выбрать ноутбук?", "А для игр?")
    assert len(bot.store.chats(42)) == 1
    assert "вы спрашивали «Как выбрать ноутбук?»" in answer_text(bot)
    assert "в контексте 2 сообщения" in answer_text(bot)
    assert not answer_text(bot).startswith("<i>")  # «новый чат» — только в первом ответе


def test_new_chat_button_keeps_the_old_one(bot):
    say(bot, "Как выбрать ноутбук?", ui.NEW_CHAT)
    note = bot.api.final()
    assert "Прошлый чат «Как выбрать ноутбук?» сохранён" in note["text"]
    assert texts(note["reply_markup"]) == texts(ui.keyboard(OPUS))
    say(bot, "Привет")
    assert "вы спрашивали" not in answer_text(bot)
    assert len(bot.store.chats(42)) == 2


def test_new_chat_in_empty_chat_does_not_pile_up(bot):
    say(bot, ui.NEW_CHAT, ui.NEW_CHAT, "/new")
    assert len(bot.store.chats(42)) <= 1
    assert bot.api.sent()[-1] == ui.already_new(OPUS)


def test_switch_model_inside_the_chat_keeps_the_dialog(bot):
    say(bot, "Как выбрать ноутбук?", ui.MODEL)
    picker = bot.api.final()
    assert "✻ Claude Opus 5.5  ✓" in texts(picker["reply_markup"])
    press(bot, "pick:gpt-6.1-sol:p")
    assert active(bot).model == "gpt-6.1-sol"
    assert bot.store.get(42).model == "gpt-6.1-sol"  # и новые чаты начнутся с неё
    switched = bot.api.final()
    assert switched["text"].startswith("⁂ Теперь в чате «Как выбрать ноутбук?» отвечает")
    assert switched["reply_markup"]["input_field_placeholder"] == "Сообщение для GPT-6.1 Sol"
    say(bot, "А для игр?")
    assert answer_text(bot).startswith("<b>⌬ GPT-6.1 Sol</b>")
    assert "вы спрашивали «Как выбрать ноутбук?»" in answer_text(bot)  # переписка та же
    assert len(bot.store.chats(42)) == 1


def test_model_card_switches_or_starts_a_new_chat(bot):
    say(bot, "Вопрос")
    press(bot, "card:gemini-4-argon:other")
    assert texts(bot.api.last()["reply_markup"])[:2] == [
        "▸ Писать ей в текущем чате",
        "+ Новый чат с ней",
    ]
    press(bot, "pick:gemini-4-argon:other")
    assert active(bot).model == "gemini-4-argon"
    card = [p for m, p in bot.api.calls if m == "editMessageText"][-1]
    assert texts(card["reply_markup"])[0] == "✓ Сейчас отвечает она"
    press(bot, "chat:gpt-6-luna:models")
    assert active(bot).model == "gpt-6-luna" and len(bot.store.chats(42)) == 2


def test_picking_the_same_model_does_nothing(bot):
    press(bot, "pick:claude-opus-5-5:p")
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == "✓ Уже отвечает Claude Opus 5.5"
    assert not bot.api.named("sendMessage")


def test_switching_between_chats_keeps_each_dialog(bot):
    say(bot, "Вопрос про ноутбук")
    laptop = active(bot)
    press(bot, "chat:gemini-4-argon:other")  # новый чат с другой моделью
    say(bot, "Вопрос про отпуск")
    trip = active(bot)
    assert trip.id != laptop.id and trip.model == "gemini-4-argon"

    say(bot, ui.MY_CHATS)
    shown = bot.api.final()
    assert texts(shown["reply_markup"])[:2] == ["✧ Вопрос про отпуск  ✓", "✻ Вопрос про ноутбук"]
    press(bot, f"open:{laptop.id}:l")  # переключение одним нажатием
    listed, opened = bot.api.calls[-2][1], bot.api.calls[-1][1]
    assert texts(listed["reply_markup"])[1] == "✻ Вопрос про ноутбук  ✓"  # галочка переехала
    assert opened["text"].startswith("<b>▸ Чат «Вопрос про ноутбук»</b>")
    assert "Последний вопрос: «Вопрос про ноутбук»" in opened["text"]
    assert opened["reply_markup"]["input_field_placeholder"] == "Сообщение для Claude Opus 5.5"
    assert active(bot).id == laptop.id and bot.store.get(42).model == "claude-opus-5-5"

    say(bot, "А что с батареей?")
    answer = answer_text(bot)
    assert answer.startswith("<b>✻ Claude Opus 5.5</b>")
    assert "вы спрашивали «Вопрос про ноутбук»" in answer  # помнит свою переписку
    assert "отпуск" not in answer  # и не путает с чужой


def test_empty_chats_are_not_listed_and_not_kept(bot):
    say(bot, "Вопрос", ui.NEW_CHAT)  # новый пустой чат — текущий
    say(bot, ui.MY_CHATS)
    names = texts(bot.api.final()["reply_markup"])
    assert names[:2] == ["✻ Новый чат  ✓", "✻ Вопрос"]
    first = bot.store.chats(42)[-1]
    press(bot, f"open:{first.id}:l")  # ушли из пустого — он удаляется
    assert len(bot.store.chats(42)) == 1


def test_old_keyboard_buttons_still_work(bot):
    say(bot, "Вопрос", ui.OLD_END_CHAT)
    assert bot.api.final()["text"] == ui.NO_CLOSING
    assert texts(bot.api.final()["reply_markup"]) == texts(ui.keyboard(OPUS))
    say(bot, ui.OLD_NEW_DIALOG)
    assert len(bot.store.chats(42)) == 2
    say(bot, ui.OLD_CHATS, "/stop")
    assert bot.api.final()["text"] == ui.NO_CLOSING


def test_existing_users_get_the_new_keyboard_once(bot):
    say(bot, "Вопрос")
    bot.store.update(42, kb=1)  # как будто пользователь из прошлой версии
    say(bot, "Ещё вопрос", "И ещё")
    updates = [t for t in bot.api.sent() if t == ui.KEYBOARD_UPDATED]
    assert len(updates) == 1
    assert bot.store.get(42).kb == KEYBOARD_VERSION


def test_new_users_get_the_keyboard_with_welcome_only(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "STREAM_DELAY", 0)
    fresh = Bot(FakeApi(), Store(tmp_path / "fresh.db"))
    say(fresh, "/start")
    assert ui.KEYBOARD_UPDATED not in fresh.api.sent()
    assert fresh.api.final()["reply_markup"] == ui.keyboard(OPUS)


def test_rename_chat(bot):
    say(bot, "Вопрос")
    chat = active(bot)
    press(bot, "chats:0:e")
    assert data(bot.api.last()["reply_markup"])[0] == f"info:{chat.id}"
    press(bot, f"info:{chat.id}")
    assert texts(bot.api.last()["reply_markup"]) == [
        "▸ Продолжить чат",
        "✎ Переименовать",
        "✕ Удалить",
        "← Все чаты",
    ]
    press(bot, f"ren:{chat.id}")
    say(bot, "x" * 41)  # слишком длинно — остаёмся в вводе
    assert "Слишком длинно: 41 символ" in bot.api.sent()[-1]
    say(bot, "  Ноутбук   для работы ")
    assert bot.store.chat(chat.id).title == "Ноутбук для работы"
    assert bot.api.sent()[-1].startswith(ui.TITLE_SAVED)
    assert bot.store.get(42).awaiting == ""
    say(bot, "Ещё вопрос")  # дальше снова обычный чат
    assert answer_text(bot).startswith("<b>✻ Claude Opus 5.5</b>")


def test_rename_is_cancelled_by_any_button(bot):
    say(bot, "Вопрос")
    chat = active(bot)
    press(bot, f"ren:{chat.id}")
    press(bot, f"info:{chat.id}")  # «← Отмена»
    say(bot, "Новое название?")
    assert bot.store.chat(chat.id).title == "Вопрос"


def test_delete_chat_with_confirmation(bot):
    say(bot, "Первый", ui.NEW_CHAT, "Второй")
    first, second = sorted(bot.store.chats(42), key=lambda c: c.id)
    press(bot, f"del:{first.id}")
    assert texts(bot.api.last()["reply_markup"]) == ["✕ Да, удалить", "← Нет"]
    assert bot.store.chat(first.id) is not None  # без подтверждения не удаляем
    press(bot, f"delok:{first.id}")
    assert bot.store.chat(first.id) is None
    assert active(bot).id == second.id  # текущий чат не тронут


def test_deleting_the_current_chat_starts_fresh(bot):
    say(bot, "Вопрос")
    chat = active(bot)
    press(bot, f"delok:{chat.id}")
    assert bot.store.get(42).active == 0
    assert bot.api.final()["text"] == ui.chat_deleted(chat)
    say(bot, "Привет")
    assert answer_text(bot).startswith("<i>+ Новый чат</i>")
    assert active(bot).id != chat.id


def test_other_users_chats_are_off_limits(bot):
    say(bot, "Секрет")
    chat = active(bot)
    for action in ("open", "info", "ren", "del", "delok"):
        press(bot, f"{action}:{chat.id}", uid=777)
        assert bot.api.named("answerCallbackQuery")[-1]["text"] == ui.CHAT_GONE
    assert bot.store.chat(chat.id).title == "Секрет"
    assert bot.store.get(777).active == 0


def test_chats_list_pages(bot):
    for i in range(ui.CHATS_PAGE + 2):
        chat, _ = bot.store.new_chat(42, "gpt-6-sol")
        bot.store.note_question(chat.id, f"вопрос {i}")
    say(bot, "/chats")
    first = bot.api.last()["reply_markup"]
    assert "1 из 2" in texts(first) and "chats:1:s" in data(first)
    press(bot, "chats:1:s")
    second = bot.api.last()["reply_markup"]
    assert len([d for d in data(second) if d.startswith("open:")]) == 2


def test_role_is_only_your_own_prompt(bot):
    press(bot, "nav:role")
    shown = bot.api.last()
    assert "Сейчас не задана" in shown["text"]
    assert texts(shown["reply_markup"]) == ["✎ Написать свою роль", "← Назад"]
    press(bot, f"role:{CUSTOM_ROLE}")
    say(bot, "Отвечай как маркетолог")
    assert texts(bot.api.final()["reply_markup"]) == ["✎ Изменить", "✕ Сбросить", "← Назад"]
    press(bot, "role:reset")
    prefs = bot.store.get(42)
    assert (prefs.role, prefs.role_text) == (NO_ROLE, "")
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == ui.ROLE_RESET


def test_old_preset_role_buttons_change_nothing(bot):
    press(bot, "role:lawyer")
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == ui.STALE_BUTTON
    assert bot.store.get(42).role == NO_ROLE


def test_chosen_preset_role_becomes_your_own(tmp_path):
    """Кто выбрал готовую роль в прошлой версии, сохраняет её как свою: ответы не меняются."""
    store = Store(tmp_path / "s.db")
    store.update(7, memory=True)
    store.db.execute("UPDATE prefs SET role = 'marketer' WHERE user_id = 7")
    store.db.commit()
    prefs = Store(tmp_path / "s.db").get(7)
    assert (prefs.role, prefs.role_text) == (CUSTOM_ROLE, LEGACY_ROLES["marketer"])


# Инструменты


def test_tools_are_a_compact_grid(bot):
    say(bot, "/tools")
    rows = bot.api.final()["reply_markup"]["inline_keyboard"]
    assert [len(row) for row in rows] == [2, 2, 2, 2]  # 7 инструментов и «В меню»
    assert [b["text"] for b in rows[0]] == ["◐ Фотостили", "▣ Нарисовать"]
    assert rows[-1][-1]["callback_data"] == "nav:main"


def test_text_tool_answers_once_in_the_current_chat(bot):
    say(bot, "Обычный вопрос")
    press(bot, "tool:brainstorm")
    assert bot.store.get(42).awaiting == "tool:brainstorm"
    assert "Напишите тему или задачу" in bot.api.last()["text"]
    say(bot, "Как назвать кофейню")
    text = answer_text(bot)
    assert text.startswith("<b>✻ Claude Opus 5.5</b> · ✶ Брейншторм")
    assert "Идеи по теме «Как назвать кофейню»" in text
    assert "Инструмент · брейншторм" in text
    assert bot.store.get(42).awaiting == ""
    assert len(bot.store.chats(42)) == 1  # тот же чат
    assert history(bot)[-2].text == "[Брейншторм] Как назвать кофейню"
    say(bot, "А ещё?")  # дальше — обычный ответ
    assert "тестовый ответ" in answer_text(bot)


def test_tool_waits_for_the_right_input(bot):
    press(bot, "tool:brainstorm")
    say(bot, None, photo=[{"file_id": "a", "width": 10, "height": 10}])
    assert bot.api.sent()[-1] == ui.tool_needs("brainstorm")
    assert bot.store.get(42).awaiting == "tool:brainstorm"  # ждём дальше


def test_photo_styles(bot):
    press(bot, "tool:styles")
    assert data(bot.api.last()["reply_markup"])[0] == "style:anime"
    press(bot, "style:anime")
    assert "в стиле «аниме»" in bot.api.last()["text"]
    say(bot, "без фото")
    assert bot.api.sent()[-1] == ui.tool_needs("styles")
    say(bot, None, photo=[{"file_id": "a", "width": 800, "height": 600}])
    assert "Здесь будет ваше изображение 800×600 в стиле «аниме»" in answer_text(bot)


def test_summary_takes_a_pdf(bot):
    press(bot, "tool:summary")
    pdf = {
        "file_id": "d",
        "file_name": "отчёт.pdf",
        "file_size": 2048,
        "mime_type": "application/pdf",
    }
    say(bot, None, document=pdf)
    assert "≡ Пересказ" in answer_text(bot)
    assert "Главное из «файл «отчёт.pdf», 2 КБ»" in answer_text(bot)


def test_regenerating_a_tool_answer_keeps_the_tool(bot):
    press(bot, "tool:explain")
    say(bot, "Что такое инфляция")
    answer_button(bot, "r")
    assert "◎ Объяснить тему · вариант 2" in answer_text(bot)
    assert "Объясняю «Что такое инфляция»" in answer_text(bot)


def test_tool_is_cancelled_by_keyboard_or_any_button(bot):
    press(bot, "tool:rewrite")
    say(bot, ui.MY_CHATS)
    assert bot.store.get(42).awaiting == ""
    press(bot, "tool:rewrite")
    press(bot, "nav:tools")  # «← Инструменты»
    say(bot, "Привет")
    assert "тестовый ответ" in answer_text(bot)


def test_custom_role_is_saved_from_next_message(bot):
    press(bot, f"role:{CUSTOM_ROLE}")
    assert "Пришлите инструкцию" in bot.api.last()["text"]
    say(bot, "Отвечай как пират")
    prefs = bot.store.get(42)
    assert (prefs.role, prefs.role_text, prefs.awaiting) == (CUSTOM_ROLE, "Отвечай как пират", "")
    assert ui.ROLE_SAVED in bot.api.sent()[-1]
    assert bot.store.chats(42) == []  # текст роли — не вопрос модели
    say(bot, "Привет")
    assert "Отвечаю в вашей роли" in answer_text(bot)


def test_custom_role_input_survives_restart(tmp_path, bot):
    press(bot, f"role:{CUSTOM_ROLE}")
    restarted = Bot(FakeApi(), Store(tmp_path / "settings.db"))
    say(restarted, "Отвечай кратко")
    assert restarted.store.get(42).role_text == "Отвечай кратко"


def test_custom_role_input_ends_on_command_or_keyboard(bot):
    press(bot, f"role:{CUSTOM_ROLE}")
    say(bot, ui.MY_CHATS, "Привет")
    assert bot.store.get(42).role == "none"
    assert answer_text(bot).startswith("<i>+ Новый чат</i>")

    press(bot, f"role:{CUSTOM_ROLE}")
    press(bot, "nav:role")  # «← Отмена»
    say(bot, "/start")
    assert bot.store.get(42).awaiting == ""


def test_custom_role_too_long_or_not_text_stays_in_input(bot):
    press(bot, f"role:{CUSTOM_ROLE}")
    say(bot, "а" * 501)
    say(bot, None, photo=[{"file_id": "f", "width": 1, "height": 1}])
    say(bot, "Короткая роль")
    sent = bot.api.sent()
    assert "Слишком длинно: 501 символ" in sent[0]
    assert sent[1] == ui.ROLE_ONLY_TEXT
    assert bot.store.get(42).role_text == "Короткая роль"


def test_memory_off_answers_each_question_alone(bot):
    bot.store.update(42, memory=False)
    say(bot, "Первый", "Второй")
    assert "вы спрашивали" not in answer_text(bot)
    assert "каждый вопрос отдельно" in answer_text(bot)
    assert history(bot) == []
    assert active(bot).messages == 2  # чат всё равно в списке, с названием


def test_memory_grows_input_tokens(bot):
    say(bot, "Привет", "Привет")
    first, second = bot.api.sent()[-2:]

    def tokens_in(text: str) -> int:
        return int(text.split("вход ")[1].split(" ")[0])

    assert tokens_in(first) == answers.count_tokens("Привет")
    assert tokens_in(second) > tokens_in(first)


def test_undelivered_answer_costs_nothing(bot):
    bot.api.fail["sendMessage"] = tg.TelegramError(403, "Forbidden: bot was blocked by the user")
    with pytest.raises(tg.TelegramError):
        say(bot, "Привет")
    assert bot.store.used_today(42) == 0
    assert history(bot) == []
    assert active(bot).messages == 0


def test_usage_and_detail_can_be_hidden(bot):
    bot.store.update(42, show_usage=False, detail="short", lang="en")
    say(bot, "Hello")
    text = answer_text(bot)
    assert "↳" not in text and "<blockquote>" not in text
    assert "test reply" in text


def test_limit_warns_then_blocks(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    bot.store.update(42, memory=False)
    say(bot, *["Привет"] * 10)
    sent = bot.api.sent()
    assert sum("Израсходовано 80%" in t for t in sent) == 1
    assert sum("исчерпан" in t for t in sent) >= 2  # уведомление и отказ отвечать
    assert sent[-1] == answers.limit_reached()


def test_limit_without_notifications_only_blocks(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    bot.store.update(42, memory=False, notify_limit=False)
    say(bot, *["Привет"] * 10)
    sent = bot.api.sent()
    assert not any("Израсходовано" in t for t in sent)
    assert sent[-1] == answers.limit_reached()


def test_sticker_gets_hint(bot):
    say(bot, None, sticker={"file_id": "f"})
    assert bot.api.sent()[-1] == ui.ONLY_TEXT


def test_group_messages_are_ignored(bot):
    say(bot, "/start", "Привет", chat_type="supergroup")
    assert bot.api.calls == []


# Потоковые ответы и кнопки под ответом


def test_answer_streams_into_one_message(bot):
    say(bot, "Расскажи что-нибудь длинное")
    answer_id = max(bot.api.messages)
    edits = [p for p in bot.api.named("editMessageText") if p["message_id"] == answer_id]
    assert len(edits) == answers.STREAM_STEPS - 1  # первая часть отправлена, дальше — правки
    first = bot.api.named("sendMessage")[-1]
    assert first["text"].endswith(ui.CURSOR) and not first.get("reply_markup")
    final = bot.api.messages[answer_id]
    assert not final["text"].endswith(ui.CURSOR)
    assert "<b>✻ Claude Opus 5.5</b>" in final["text"] and "<blockquote>" in final["text"]
    assert final["reply_markup"] == ui.answer_keyboard(1)


def press_on_answer(bot: Bot, data_: str) -> None:
    press(bot, data_, message_id=max(bot.api.messages))


def answer_button(bot: Bot, action: str) -> None:
    reply_id = data(bot.api.final()["reply_markup"])[0].split(":")[1]
    press_on_answer(bot, f"ans:{reply_id}:{action}")


def test_regenerate_rewrites_the_same_message_and_costs_tokens(bot):
    say(bot, "Привет")
    before_text, used = answer_text(bot), bot.store.used_today(42)
    count = len(bot.api.messages)
    answer_button(bot, "r")
    assert len(bot.api.messages) == count  # ответ переписан в том же сообщении
    assert answer_text(bot) != before_text  # другая заготовка
    assert bot.store.used_today(42) > used
    assert len(history(bot)) == 2  # в памяти — по-прежнему один вопрос и один ответ
    assert history(bot)[-1].text == answers.plain_text(answer_text(bot).split("\n\n<i>↳")[0])


def test_shorter_gives_one_sentence(bot):
    say(bot, "Привет", "Ещё")
    answer_button(bot, "s")
    text = answer_text(bot)
    assert "<blockquote>" not in text
    body = text.split("\n\n")[1]
    assert body.count(".") == 1


def test_other_model_answers_the_same_question(bot):
    say(bot, "Привет")
    answer_button(bot, "m")
    picker = bot.api.named("editMessageReplyMarkup")[-1]["reply_markup"]
    assert "✻ Claude Opus 5.5" not in texts(picker) and texts(picker)[-1] == "← Назад"
    press_on_answer(bot, data(picker)[2])  # третья в списке — GPT-6.1 Sol
    assert "<b>⌬ GPT-6.1 Sol</b>" in answer_text(bot)
    assert active(bot).model == "claude-opus-5-5"  # сам чат остался с прежней моделью
    answer_button(bot, "back")  # «← Назад» возвращает кнопки ответа
    assert texts(bot.api.named("editMessageReplyMarkup")[-1]["reply_markup"])[0] == ui.REGENERATE


def test_answer_buttons_of_someone_else_do_nothing(bot):
    say(bot, "Привет")
    press(bot, "ans:1:r", uid=777)
    assert bot.api.named("answerCallbackQuery")[-1]["show_alert"]
    assert bot.store.used_today(777) == 0


# Фото, файлы и голосовые


def test_photo_with_caption_goes_to_the_model(bot):
    photo = [
        {"file_id": "a", "width": 90, "height": 90},
        {"file_id": "b", "width": 1280, "height": 720},
    ]
    say(bot, None, photo=photo, caption="Что на фото?")
    text = answer_text(bot)
    assert "Вижу изображение 1280×720." in text
    assert "вход 803" in text  # 800 за картинку + 3 за подпись
    assert active(bot).title == "[изображение 1280×720] Что на фото?"


def test_pdf_and_voice_depend_on_the_model(bot):
    pdf = {
        "file_id": "d",
        "file_name": "отчёт.pdf",
        "file_size": 20480,
        "mime_type": "application/pdf",
    }
    say(bot, None, document=pdf)  # Claude: файлы понимает, голос — нет
    assert "Прочитал файл «отчёт.pdf», 20 КБ." in answer_text(bot)
    say(bot, None, voice={"file_id": "v", "duration": 75})
    assert bot.api.sent()[-1] == ui.cant_read(OPUS, "voice")
    press(bot, "pick:gpt-6.1-sol:p")  # сменили модель в том же чате
    say(bot, None, voice={"file_id": "v", "duration": 75})
    assert "Прослушал голосовое 1:15." in answer_text(bot)


def test_model_card_lists_what_it_understands():
    assert "Понимает: текст, фото, PDF и документы" in ui.model_card(OPUS)
    assert "голосовые" in ui.model_card(MODELS["gemini-4-argon"])


# Баланс и оплата Stars


def test_balance_screen_and_invoice(bot):
    press(bot, "nav:balance")
    shown = bot.api.last()
    assert "осталось 20 000 из 20 000" in shown["text"]
    assert texts(shown["reply_markup"])[0] == "+ 100 000 токенов · 50 Stars"
    press(bot, "buy:s")
    invoice = bot.api.named("sendInvoice")[-1]
    assert (invoice["currency"], invoice["payload"]) == ("XTR", "pack:s")
    assert invoice["prices"] == [{"label": "100 000 токенов", "amount": 50}]


def checkout(bot: Bot, payload: str, amount: int, currency: str = "XTR") -> dict:
    query = {
        "id": "pc1",
        "from": USER,
        "currency": currency,
        "total_amount": amount,
        "invoice_payload": payload,
    }
    bot.handle({"update_id": next(_ids), "pre_checkout_query": query})
    return bot.api.named("answerPreCheckoutQuery")[-1]


def test_pre_checkout_checks_the_price(bot):
    assert checkout(bot, "pack:m", 200)["ok"] is True
    assert checkout(bot, "pack:m", 1)["ok"] is False
    assert checkout(bot, "pack:zzz", 200)["ok"] is False
    assert checkout(bot, "pack:m", 200, "USD")["ok"] is False


def pay(bot: Bot, charge_id: str = "ch1", payload: str = "pack:s", amount: int = 50) -> None:
    payment = {
        "currency": "XTR",
        "total_amount": amount,
        "invoice_payload": payload,
        "telegram_payment_charge_id": charge_id,
        "provider_payment_charge_id": "",
    }
    say(bot, None, successful_payment=payment)


def test_successful_payment_credits_once(bot):
    pay(bot)
    assert bot.store.get(42).balance == 100_000
    assert bot.api.sent()[-1] == ui.paid(100_000, 100_000)
    pay(bot)  # Telegram прислал ту же оплату ещё раз
    assert bot.store.get(42).balance == 100_000
    pay(bot, "ch2", "pack:m", 200)
    assert bot.store.get(42).balance == 600_000
    assert bot.store.chats(42) == []  # оплата — не вопрос модели


def test_paid_tokens_are_spent_after_the_free_limit(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 300)
    bot.store.update(42, memory=False)
    pay(bot)
    say(bot, *["Привет"] * 6)
    used = bot.store.used_today(42)
    assert used > 300
    assert bot.store.get(42).balance == 100_000 - (used - 300)
    notes = [t for t in bot.api.sent() if "Дальше токены списываются с баланса" in t]
    assert len(notes) == 1
    assert "баланс " in answer_text(bot)  # строка расхода показывает остаток


def test_out_of_tokens_offers_to_top_up(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 300)
    bot.store.update(42, memory=False, notify_limit=False)
    say(bot, *["Привет"] * 6)
    last = bot.api.named("sendMessage")[-1]
    assert last["text"] == answers.limit_reached()
    assert data(last["reply_markup"]) == ["nav:balance"]


# Уведомления о новых моделях


def test_new_models_are_announced_to_subscribers(bot):
    api, store = bot.api, bot.store
    assert notify.announce_new_models(api, store) == 0  # первый запуск
    store.touch(1)
    store.update(2, notify_models=False)
    store.touch(3)
    known = [m for m in MODELS if m != "gemini-4-argon"]
    store.set_meta(notify.KNOWN_MODELS, json.dumps(known))
    assert notify.announce_new_models(api, store) == 3  # 1, 3 и сам 42
    sent = api.named("sendMessage")
    assert {m["chat_id"] for m in sent} == {1, 3, 42}
    assert "✧ Gemini 4 Argon" in sent[-1]["text"]
    assert notify.announce_new_models(api, store) == 0  # повторно не пишет


def test_blocked_users_are_skipped_until_they_write_again(bot):
    api, store = bot.api, bot.store
    store.set_meta(notify.KNOWN_MODELS, json.dumps([]))
    api.fail["sendMessage"] = tg.TelegramError(403, "Forbidden: bot was blocked by the user")
    assert notify.announce_new_models(api, store) == 0
    assert store.get(42).blocked
    assert store.users_with("notify_models") == []
    del api.fail["sendMessage"]
    say(bot, "/start")
    assert store.users_with("notify_models") == [42]


def test_any_visitor_gets_new_model_notices(tmp_path):
    fresh = Bot(FakeApi(), Store(tmp_path / "fresh.db"))
    say(fresh, "/start")
    assert fresh.store.users_with("notify_models") == [42]


# Мини-клиент Bot API: настоящий HTTP через локальный сервер без TLS


def fake_telegram(responses: list[bytes]):
    """Сервер на 127.0.0.1: отвечает заготовками по очереди и записывает запросы."""
    server = socket.create_server(("127.0.0.1", 0))
    requests: list[bytes] = []
    replies = iter(responses)
    lock = threading.Lock()

    def connection(conn) -> None:
        stream = conn.makefile("rb")
        while True:
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                chunk = stream.readline()
                if not chunk:
                    break
                head += chunk
            if not head.endswith(b"\r\n\r\n"):
                break
            length = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
            with lock:
                requests.append(head + stream.read(length))
                reply = next(replies)
            conn.sendall(reply)
            if b"Connection: close" in reply:
                break
        stream.close()
        conn.close()

    def serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=connection, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    api = tg.Api("1:T", host="127.0.0.1", port=server.getsockname()[1], tls=False)
    return server, api, requests


def http(body: dict, chunked: bool = False, close: bool = False) -> bytes:
    payload = json.dumps(body).encode()
    head = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
    if close:
        head += "Connection: close\r\n"
    if chunked:
        half = len(payload) // 2
        parts = [payload[:half], payload[half:]]
        body_bytes = (
            b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in parts) + b"0\r\n\r\n"
        )
        return (head + "Transfer-Encoding: chunked\r\n\r\n").encode() + body_bytes
    return (head + f"Content-Length: {len(payload)}\r\n\r\n").encode() + payload


def test_api_keeps_connection_and_reads_chunked_bodies():
    server, api, requests = fake_telegram(
        [
            http({"ok": True, "result": {"id": 1}}),
            http({"ok": True, "result": [1, 2]}, chunked=True),
        ]
    )
    assert api("getMe") == {"id": 1}
    assert api("sendMessage", chat_id=1, text="Привет", reply_markup=None) == [1, 2]
    assert len(api.idle) == 1  # одно соединение на оба запроса
    api.close()
    server.close()
    assert requests[0].startswith(b"POST /bot1:T/getMe HTTP/1.1")
    body = json.loads(requests[1].split(b"\r\n\r\n", 1)[1])
    assert body == {"chat_id": 1, "text": "Привет"}  # None не отправляется


def test_api_raises_telegram_errors_and_waits_on_429():
    flood = {"retry_after": 0.01}
    server, api, requests = fake_telegram(
        [
            http({"ok": False, "error_code": 429, "description": "Flood", "parameters": flood}),
            http({"ok": True, "result": True}),
            http({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}),
        ]
    )
    assert api("sendMessage", chat_id=1, text="a") is True
    with pytest.raises(tg.TelegramError) as e:
        api("sendMessage", chat_id=1, text="b")
    api.close()
    server.close()
    assert e.value.code == 403
    assert len(requests) == 3


def test_api_reconnects_after_server_closes_connection():
    server, api, _ = fake_telegram(
        [http({"ok": True, "result": 1}, close=True), http({"ok": True, "result": 2})]
    )
    assert api("getMe") == 1
    assert api.idle == []  # сервер закрыл соединение — в пул оно не вернулось
    assert api("getMe") == 2
    api.close()
    server.close()


def test_api_from_many_threads():
    server, api, _ = fake_telegram([http({"ok": True, "result": i}) for i in range(40)])
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.extend(api("getMe") for _ in range(10)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    api.close()
    server.close()
    assert sorted(results) == list(range(40))


def test_serial_runs_one_users_updates_in_order():
    order: list[str] = []
    lock = threading.Lock()

    def handle(update) -> None:
        with lock:
            order.append(f"start {update['update_id']}")
        time.sleep(0.05)
        with lock:
            order.append(f"end {update['update_id']}")

    serial = tg.Serial(handle)
    for i in (1, 2):
        serial.submit({"update_id": i, "message": {"from": {"id": 5}}})
    serial.submit({"update_id": 3, "message": {"from": {"id": 6}}})
    assert serial.drain(timeout=2)
    assert order.index("end 1") < order.index("start 2")  # один пользователь — по очереди
    assert order.index("start 3") < order.index("end 1")  # другой — параллельно
    assert serial.queues == {}
    assert serial.workers == 2


def test_serial_survives_handler_errors():
    done: list[int] = []

    def handle(update) -> None:
        if update["update_id"] == 1:
            raise RuntimeError("сбой")
        done.append(update["update_id"])

    serial = tg.Serial(handle)
    for i in (1, 2):
        serial.submit({"update_id": i, "message": {"from": {"id": 5}}})
    assert serial.drain(timeout=2)
    assert done == [2]


class PollApi:
    """getUpdates отдаёт по очереди заготовки: список апдейтов, исключение или функцию."""

    def __init__(self, steps: list) -> None:
        self.steps = steps
        self.calls: list[dict] = []

    def __call__(self, method, _timeout=0, **params):
        self.calls.append(params)
        if params.get("timeout") == 0:
            return []  # подтверждение при остановке
        step = self.steps.pop(0) if self.steps else []
        if isinstance(step, Exception):
            raise step
        return step() if callable(step) else step


def test_poll_dispatches_updates_survives_errors_and_confirms_on_stop(monkeypatch):
    stop = threading.Event()
    got: list[int] = []
    api = PollApi(
        [
            [{"update_id": 10, "message": {"from": {"id": 1}}}],
            RuntimeError("неожиданный ответ"),  # бот не падает, а пробует снова
            lambda: stop.set() or [],
        ]
    )
    monkeypatch.setattr(threading.Event, "wait", lambda self, timeout=None: self.is_set())
    tg.poll(api, tg.Serial(lambda u: got.append(u["update_id"])), stop)
    assert got == [10]
    assert api.calls[1]["offset"] == 11
    assert api.calls[-1] == {"offset": 11, "timeout": 0, "limit": 1}


def test_poll_stops_on_signal_during_long_wait():
    """SIGTERM прерывает ожидание getUpdates сразу, а не через 50 секунд."""

    def wait_long():
        signal.raise_signal(signal.SIGINT)
        time.sleep(5)  # сюда не доходим: сигнал прервал ожидание
        return []

    api = PollApi([[{"update_id": 3, "message": {"from": {"id": 1}}}], wait_long])
    before = signal.getsignal(signal.SIGINT)
    started = time.monotonic()
    tg.poll(api, tg.Serial(lambda u: None), threading.Event())
    assert time.monotonic() - started < 2
    assert api.calls[-1] == {"offset": 4, "timeout": 0, "limit": 1}
    assert signal.getsignal(signal.SIGINT) is before  # прежний обработчик вернули
