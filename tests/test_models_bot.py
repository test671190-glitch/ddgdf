"""Бот моделей (models_bot): меню, чат, настройки, тестовые ответы, оформление без
эмодзи и мини-клиент Bot API. Апдейты — dict как от Telegram, API — фейковый."""

import asyncio
import itertools
import json
import sqlite3

import pytest

from models_bot import answers, notify, tg, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, OTHER_MODELS, ROLES, TOP_MODELS
from models_bot.handlers import SCREENS, Bot
from models_bot.store import HISTORY_LIMIT, Prefs, Store, Turn

_ids = itertools.count(1)

# Все символы оформления. Ни у одного нет свойства Emoji в Unicode (emoji-data.txt),
# поэтому Telegram не заменит их на цветные эмодзи
DECOR = set("✻⌬✧⁂⛭⌂ⓘ✎↺▸■→←↓↳✓·—«»…")


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, tg.TelegramError] = {}

    async def __call__(self, method: str, _timeout: float = 0, **params):
        self.calls.append((method, params))
        if method in self.fail:
            raise self.fail[method]
        if method in ("sendMessage", "editMessageText"):
            return {"message_id": next(_ids)}
        return True

    def named(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def sent(self) -> list[str]:
        return [p["text"] for p in self.named("sendMessage")]

    def last(self) -> dict:
        """Последний показанный экран: отправленный или отредактированный."""
        return next(p for m, p in reversed(self.calls) if m in ("sendMessage", "editMessageText"))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "TYPING_SECONDS", 0)
    monkeypatch.setattr(notify, "SEND_PAUSE", 0)
    return Bot(FakeApi(), Store(tmp_path / "settings.db"))


USER = {"id": 42, "is_bot": False, "first_name": "Neo"}


async def say(bot: Bot, *texts: str | None, chat_type: str = "private", **extra) -> None:
    for text in texts:
        message = {
            "message_id": next(_ids),
            "date": 1,
            "chat": {"id": 42 if chat_type == "private" else -100, "type": chat_type},
            "from": USER,
            **({"text": text} if text is not None else {}),
            **extra,
        }
        await bot.handle({"update_id": next(_ids), "message": message})


async def press(bot: Bot, data: str, date: int = 1) -> None:
    message = {"message_id": 7, "date": date, "chat": {"id": 42, "type": "private"}}
    query = {"id": str(next(_ids)), "from": USER, "data": data, "message": message}
    await bot.handle({"update_id": next(_ids), "callback_query": query})


def buttons(markup: dict | None) -> list[dict]:
    if not markup:
        return []
    rows = markup.get("inline_keyboard") or markup.get("keyboard") or []
    return [b for row in rows for b in row]


def texts(markup: dict | None) -> list[str]:
    return [b["text"] for b in buttons(markup)]


def all_screens() -> list[tuple[str, dict | None]]:
    prefs = Prefs()
    in_chat = Prefs(chat="gpt-6-sol", role=CUSTOM_ROLE, role_text="Отвечай как юрист")
    model = MODELS[prefs.model]
    screens = [
        (ui.welcome("Neo", prefs), ui.main_keyboard()),
        (ui.welcome("Neo", in_chat), None),
        (ui.models_text(), ui.models_keyboard()),
        (ui.other_text(), ui.other_keyboard()),
        (ui.model_card(model, in_chat), ui.card_keyboard(model, in_chat, "other")),
        (ui.chat_started(model, in_chat, MODELS["gpt-6-sol"]), ui.chat_keyboard(model)),
        (ui.chat_ended(model), None),
        (ui.new_dialog_done(model), None),
        (ui.no_chat_text(), ui.no_chat_keyboard(prefs)),
        (ui.NO_CHAT_TO_CLOSE, None),
        (ui.settings_text(prefs, 1234), ui.settings_keyboard(prefs)),
        (ui.picker_text(), ui.picker_keyboard(prefs)),
        (ui.role_text(in_chat, note=ui.ROLE_SAVED), ui.role_keyboard(in_chat)),
        (ui.role_input_text(), ui.role_input_keyboard()),
        (ui.notify_text(), ui.notify_keyboard(prefs)),
        (ui.new_models_text(OTHER_MODELS), ui.new_models_keyboard(OTHER_MODELS)),
        (ui.HELP, None),
        (ui.STALE_BUTTON + ui.ONLY_TEXT + ui.ROLE_ONLY_TEXT, None),
        (answers.limit_reached(), None),
        (answers.limit_notices(0, 16_000)[0], None),
        *((text, None) for text in SCREENS.values()),
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
    assert ui.main_keyboard()["inline_keyboard"] == [
        [
            {"text": "⁂ Модели", "callback_data": "nav:models"},
            {"text": "⛭ Настройки", "callback_data": "nav:settings"},
        ]
    ]


def test_models_menu_lists_claude_then_gpt_then_other():
    names = texts(ui.models_keyboard())
    assert names[:3] == ["✻ Claude Opus 5.5", "✻ Claude Sonnet 5.5", "✻ Claude Haiku 5.5"]
    assert names[3:6] == ["⌬ GPT-6 Sol", "⌬ GPT-6 Astra", "⌬ GPT-6 Luna"]
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
        return buttons(markup)[-1]["callback_data"]

    model, prefs = MODELS["gemini-4-argon"], Prefs()
    assert back(ui.models_keyboard()) == "nav:main"
    assert back(ui.other_keyboard()) == "nav:models"
    assert back(ui.card_keyboard(model, prefs, "other")) == "nav:other"
    assert back(ui.card_keyboard(model, prefs, "models")) == "nav:models"
    assert back(ui.settings_keyboard(prefs)) == "nav:main"
    assert back(ui.picker_keyboard(prefs)) == "nav:settings"
    assert back(ui.role_keyboard(prefs)) == "nav:settings"
    assert back(ui.notify_keyboard(prefs)) == "nav:settings"
    assert back(ui.role_input_keyboard()) == "nav:role"


def test_other_models_are_gpt_6_1_and_gemini_only():
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
    markups = [m for _, m in all_screens() if m] + [ui.picker_keyboard(Prefs())]
    for model in MODELS.values():
        markups += [ui.card_keyboard(model, Prefs(), "other"), ui.chat_keyboard(model)]
        assert len(ui.chat_keyboard(model)["input_field_placeholder"]) <= 64
    for markup in markups:
        for b in buttons(markup):
            assert len(b.get("callback_data", "").encode()) <= 64


# Хранилище


def test_store_keeps_prefs_and_forgets_removed_models(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.get(1) == Prefs()
    store.update(1, model="gemini-4-argon", lang="en", memory=False)
    assert Store(tmp_path / "s.db").get(1) == Prefs("gemini-4-argon", "en", memory=False)
    store.update(1, model="grok-4.7", role="poet", chat="grok-4.7", awaiting="x")
    assert store.get(1) == Prefs(lang="en", memory=False)


def test_store_upgrades_old_settings_table(tmp_path):
    """На сервере уже есть база прошлых версий: новые столбцы добавляются,
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


async def test_every_screen_and_card_opens(bot):
    for name in SCREENS:
        await press(bot, f"nav:{name}")
    for model_id in MODELS:
        await press(bot, f"card:{model_id}:models")
    assert len(bot.api.named("editMessageText")) == len(SCREENS) + len(MODELS)
    assert len(bot.api.named("answerCallbackQuery")) == len(SCREENS) + len(MODELS)


@pytest.mark.parametrize(
    ("text", "first_button"),
    [
        ("/start", "⁂ Модели"),
        ("/models", "✻ Claude Opus 5.5"),
        ("/other", "⌬ GPT-6.1 Sol"),
        ("/settings", "Модель · Claude Opus 5.5"),
        ("/start@modelsbot", "⁂ Модели"),
        ("/unknown", "⁂ Модели"),
    ],
)
async def test_commands_get_a_screen(bot, text, first_button):
    await say(bot, text)
    assert texts(bot.api.last()["reply_markup"])[0] == first_button


async def test_stale_button_answers_and_opens_menu(bot):
    """Кнопки прошлых версий бота не должны крутить часики бесконечно."""
    await press(bot, "pick:gpt-6-sol:-1")
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == ui.STALE_BUTTON
    assert texts(bot.api.last()["reply_markup"]) == ["⁂ Модели", "⛭ Настройки"]


async def test_old_message_screen_is_sent_anew(bot):
    await press(bot, "nav:models", date=0)  # недоступное сообщение
    assert not bot.api.named("editMessageText")
    assert bot.api.sent()[-1] == ui.models_text()


async def test_not_modified_edit_is_quiet(bot):
    bot.api.fail["editMessageText"] = tg.TelegramError(400, "Bad Request: message is not modified")
    await press(bot, "nav:models")
    assert not bot.api.named("sendMessage")


async def test_make_model_default_from_card(bot):
    await press(bot, "use:gpt-6-sol:models")
    assert bot.store.get(42).model == "gpt-6-sol"
    assert "✓ Основная модель" in bot.api.last()["text"]
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == "✓ Основная модель · GPT-6 Sol"


async def test_picker_returns_to_settings_with_new_model(bot):
    await press(bot, "use:gemini-3.8-flash:picker")
    shown = bot.api.last()
    assert "Модель — ✧ Gemini 3.8 Flash" in shown["text"]
    assert texts(shown["reply_markup"])[0] == "Модель · Gemini 3.8 Flash"


async def test_toggles_flip_settings(bot):
    for key in ui.TOGGLES:
        await press(bot, f"tgl:{key}")
    assert bot.store.get(42) == Prefs(
        lang="en",
        detail="short",
        memory=False,
        show_usage=False,
        notify_models=False,
        notify_limit=False,
    )
    # переключатели уведомлений остаются на своём экране
    assert texts(bot.api.last()["reply_markup"])[0] == "Новые модели · выкл"
    assert bot.api.named("answerCallbackQuery")[-1]["text"] == "✓ Окончание лимита · выкл"


async def test_preset_role_changes_answers(bot):
    await press(bot, "role:lawyer")
    assert bot.store.get(42).role == "lawyer"
    assert "Юрист  ✓" in texts(bot.api.last()["reply_markup"])
    await say(bot, "/chat", "Можно ли расторгнуть договор?")
    assert "С юридической точки зрения" in bot.api.sent()[-1]


async def test_custom_role_is_saved_from_next_message(bot):
    await press(bot, f"role:{CUSTOM_ROLE}")
    assert "Пришлите инструкцию" in bot.api.last()["text"]
    await say(bot, "Отвечай как пират")
    prefs = bot.store.get(42)
    assert (prefs.role, prefs.role_text, prefs.awaiting) == (CUSTOM_ROLE, "Отвечай как пират", "")
    assert ui.ROLE_SAVED in bot.api.sent()[-1]
    await say(bot, "/chat", "Привет")
    assert "Отвечаю в вашей роли" in bot.api.sent()[-1]


async def test_custom_role_input_survives_restart(tmp_path, bot):
    await press(bot, f"role:{CUSTOM_ROLE}")
    restarted = Bot(FakeApi(), Store(tmp_path / "settings.db"))
    await say(restarted, "Отвечай кратко")
    assert restarted.store.get(42).role_text == "Отвечай кратко"


async def test_custom_role_input_ends_on_command_or_button(bot):
    await press(bot, f"role:{CUSTOM_ROLE}")
    await say(bot, "/start", "Привет")
    assert bot.store.get(42).role == "none"
    assert bot.api.sent()[-1] == ui.no_chat_text()

    await press(bot, f"role:{CUSTOM_ROLE}")
    await press(bot, "nav:role")  # «← Отмена»
    await say(bot, "Привет")
    assert bot.store.get(42).role == "none"


async def test_custom_role_too_long_or_not_text_stays_in_input(bot):
    await press(bot, f"role:{CUSTOM_ROLE}")
    await say(bot, "а" * 501)
    await say(bot, None, photo=[{"file_id": "f"}])
    await say(bot, "Короткая роль")
    sent = bot.api.sent()
    assert "Слишком длинно: 501" in sent[0]
    assert sent[1] == ui.ROLE_ONLY_TEXT
    assert bot.store.get(42).role_text == "Короткая роль"


# Чат с тестовой моделью


async def test_text_without_chat_does_not_reach_model(bot):
    await say(bot, "Привет")
    shown = bot.api.last()
    assert shown["text"] == ui.no_chat_text()
    assert texts(shown["reply_markup"]) == ["▸ Чат с Claude Opus 5.5", "⁂ Модели"]
    assert not bot.api.named("sendChatAction")
    assert bot.store.used_today(42) == 0


async def test_chat_starts_from_card_with_visible_keyboard(bot):
    await press(bot, "card:gpt-6-astra:models")
    assert texts(bot.api.last()["reply_markup"])[0] == "▸ Начать чат"
    await press(bot, "chat:gpt-6-astra")
    started = bot.api.last()
    assert started["text"].startswith("<b>▸ Чат начат · ⌬ GPT-6 Astra</b>")
    assert texts(started["reply_markup"]) == ["↺ Новый диалог", "■ Завершить чат"]
    assert started["reply_markup"]["input_field_placeholder"] == "Сообщение для GPT-6 Astra"
    assert bot.store.get(42).chat == "gpt-6-astra"
    await say(bot, "Привет")
    answer = bot.api.last()
    assert answer["text"].startswith("<b>⌬ GPT-6 Astra</b> · тестовый ответ")
    assert "<i>↳ " in answer["text"] and "сегодня" in answer["text"]
    assert bot.api.named("sendChatAction")


async def test_welcome_and_card_show_open_chat(bot):
    await say(bot, "/chat", "/start")
    assert "Открыт чат с <b>✻ Claude Opus 5.5</b>" in bot.api.last()["text"]
    await press(bot, "card:claude-opus-5-5:models")
    assert "Сейчас открыт чат с этой моделью" in bot.api.last()["text"]
    assert texts(bot.api.last()["reply_markup"])[0] == "↺ Начать чат заново"


async def test_switching_chat_model_says_so(bot):
    await say(bot, "/chat")
    await press(bot, "chat:gemini-4-argon")
    assert "Чат с Claude Opus 5.5 завершён" in bot.api.last()["text"]
    assert bot.store.get(42).chat == "gemini-4-argon"


async def test_end_chat_removes_keyboard_and_stops_answers(bot):
    await say(bot, "/chat", "Вопрос", ui.END_CHAT)
    ended, menu = bot.api.named("sendMessage")[-2:]
    assert ended["text"] == ui.chat_ended(MODELS["claude-opus-5-5"])
    assert ended["reply_markup"] == ui.REMOVE_KEYBOARD
    assert texts(menu["reply_markup"]) == ["⁂ Модели", "⛭ Настройки"]
    assert bot.store.get(42).chat == ""
    assert bot.store.history(42) == []
    await say(bot, "Ещё вопрос")
    assert bot.api.last()["text"] == ui.no_chat_text()


async def test_chat_buttons_without_chat_remove_stale_keyboard(bot):
    await say(bot, ui.END_CHAT, ui.NEW_DIALOG, "/stop")
    for shown in bot.api.named("sendMessage"):
        assert shown["text"] == ui.NO_CHAT_TO_CLOSE
        assert shown["reply_markup"] == ui.REMOVE_KEYBOARD


async def test_memory_recalls_previous_question_until_new_dialog(bot):
    await say(bot, "/chat", "Как выбрать ноутбук?", "А для игр?")
    assert "вы спрашивали «Как выбрать ноутбук?»" in bot.api.sent()[-1]
    assert "в контексте 2 сообщения" in bot.api.sent()[-1]
    await say(bot, ui.NEW_DIALOG)
    assert bot.api.sent()[-1] == ui.new_dialog_done(MODELS["claude-opus-5-5"])
    await say(bot, "Привет")
    assert "вы спрашивали" not in bot.api.sent()[-1]


async def test_memory_off_answers_each_question_alone(bot):
    bot.store.update(42, memory=False)
    await say(bot, "/chat", "Первый", "Второй")
    assert "вы спрашивали" not in bot.api.sent()[-1]
    assert "каждый вопрос отдельно" in bot.api.sent()[-1]
    assert bot.store.history(42) == []


async def test_memory_grows_input_tokens(bot):
    await say(bot, "/chat", "Привет", "Привет")
    first, second = bot.api.sent()[-2:]

    def tokens_in(text: str) -> int:
        return int(text.split("вход ")[1].split(" ")[0])

    assert tokens_in(first) == answers.count_tokens("Привет")
    assert tokens_in(second) > tokens_in(first)


async def test_usage_and_detail_can_be_hidden(bot):
    bot.store.update(42, show_usage=False, detail="short", lang="en")
    await say(bot, "/chat", "Hello")
    text = bot.api.sent()[-1]
    assert "↳" not in text and "<blockquote>" not in text
    assert "test reply" in text


async def test_limit_warns_then_blocks(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    bot.store.update(42, memory=False)
    await say(bot, "/chat", *["Привет"] * 10)
    sent = bot.api.sent()
    assert sum("Израсходовано 80%" in t for t in sent) == 1
    assert sum("исчерпан" in t for t in sent) >= 2  # уведомление и отказ отвечать
    assert sent[-1] == answers.limit_reached()


async def test_limit_without_notifications_only_blocks(bot, monkeypatch):
    monkeypatch.setattr(answers, "TEST_DAILY_LIMIT", 600)
    bot.store.update(42, memory=False, notify_limit=False)
    await say(bot, "/chat", *["Привет"] * 10)
    sent = bot.api.sent()
    assert not any("Израсходовано" in t for t in sent)
    assert sent[-1] == answers.limit_reached()


async def test_photo_gets_hint(bot):
    await say(bot, None, photo=[{"file_id": "f"}])
    assert bot.api.sent()[-1] == ui.ONLY_TEXT


async def test_group_messages_are_ignored(bot):
    await say(bot, "/start", "Привет", chat_type="supergroup")
    assert bot.api.calls == []


# Уведомления о новых моделях


async def test_new_models_are_announced_to_subscribers(bot):
    api, store = bot.api, bot.store
    assert await notify.announce_new_models(api, store) == 0  # первый запуск
    store.touch(1)
    store.update(2, notify_models=False)
    store.touch(3)
    known = [m for m in MODELS if m != "gemini-4-argon"]
    store.set_meta(notify.KNOWN_MODELS, json.dumps(known))
    api.fail["sendMessage"] = tg.TelegramError(403, "Forbidden: bot was blocked by the user")
    assert await notify.announce_new_models(api, store) == 0  # заблокировавшие не ломают рассылку
    del api.fail["sendMessage"]
    store.set_meta(notify.KNOWN_MODELS, json.dumps(known))
    assert await notify.announce_new_models(api, store) == 2
    sent = api.named("sendMessage")
    assert {m["chat_id"] for m in sent} == {1, 3}
    assert "✧ Gemini 4 Argon" in sent[-1]["text"]
    assert await notify.announce_new_models(api, store) == 0  # повторно не пишет


async def test_any_visitor_gets_new_model_notices(bot):
    await say(bot, "/start")
    assert bot.store.users_with("notify_models") == [42]


# Мини-клиент Bot API: настоящий HTTP через локальный сервер без TLS


async def fake_telegram(responses: list[bytes]):
    requests: list[bytes] = []
    replies = iter(responses)

    async def serve(reader, writer):
        while True:
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                break
            length = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
            requests.append(head + await reader.readexactly(length))
            reply = next(replies)
            writer.write(reply)
            await writer.drain()
            if b"Connection: close" in reply:
                break
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, tg.Api("1:T", host="127.0.0.1", port=port, tls=False), requests


def http(body: dict, chunked: bool = False, close: bool = False) -> bytes:
    data = json.dumps(body).encode()
    head = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
    if close:
        head += "Connection: close\r\n"
    if chunked:
        half = len(data) // 2
        parts = [data[:half], data[half:]]
        payload = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in parts) + b"0\r\n\r\n"
        return (head + "Transfer-Encoding: chunked\r\n\r\n").encode() + payload
    return (head + f"Content-Length: {len(data)}\r\n\r\n").encode() + data


async def test_api_keeps_connection_and_reads_chunked_bodies():
    server, api, requests = await fake_telegram(
        [
            http({"ok": True, "result": {"id": 1}}),
            http({"ok": True, "result": [1, 2]}, chunked=True),
        ]
    )
    async with server:
        assert await api("getMe") == {"id": 1}
        assert await api("sendMessage", chat_id=1, text="Привет", reply_markup=None) == [1, 2]
        api.close()
    assert requests[0].startswith(b"POST /bot1:T/getMe HTTP/1.1")
    body = json.loads(requests[1].split(b"\r\n\r\n", 1)[1])
    assert body == {"chat_id": 1, "text": "Привет"}  # None не отправляется


async def test_api_raises_telegram_errors_and_waits_on_429():
    flood = {"retry_after": 0.01}
    server, api, requests = await fake_telegram(
        [
            http({"ok": False, "error_code": 429, "description": "Flood", "parameters": flood}),
            http({"ok": True, "result": True}),
            http({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}),
        ]
    )
    async with server:
        assert await api("sendMessage", chat_id=1, text="a") is True
        with pytest.raises(tg.TelegramError) as e:
            await api("sendMessage", chat_id=1, text="b")
        api.close()
    assert e.value.code == 403
    assert len(requests) == 3


async def test_api_reconnects_after_server_closes_connection():
    server, api, requests = await fake_telegram(
        [http({"ok": True, "result": 1}, close=True), http({"ok": True, "result": 2})]
    )
    async with server:
        assert await api("getMe") == 1
        assert await api("getMe") == 2  # первое соединение сервер закрыл — открыто новое
        api.close()


async def test_serial_runs_one_users_updates_in_order():
    order: list[str] = []

    async def handle(update):
        order.append(f"start {update['update_id']}")
        await asyncio.sleep(0.01)
        order.append(f"end {update['update_id']}")

    serial = tg.Serial(handle)
    for i in (1, 2):
        serial.submit({"update_id": i, "message": {"from": {"id": 5}}})
    serial.submit({"update_id": 3, "message": {"from": {"id": 6}}})
    await serial.drain(timeout=1)
    assert order.index("end 1") < order.index("start 2")  # один пользователь — по очереди
    assert order.index("start 3") < order.index("end 1")  # другой — параллельно
    assert serial.queues == {}


async def test_poll_dispatches_updates_and_confirms_them_on_stop():
    calls: list[dict] = []
    stop = asyncio.Event()
    got: list[int] = []

    async def api(method, _timeout=0, **params):
        calls.append(params)
        if len(calls) == 1:
            return [{"update_id": 10, "message": {"from": {"id": 1}}}]
        if params.get("timeout") == 0:
            return []
        stop.set()
        await asyncio.sleep(3600)

    async def handle(update):
        got.append(update["update_id"])

    await asyncio.wait_for(tg.poll(api, tg.Serial(handle), stop), 2)
    assert got == [10]
    assert calls[1]["offset"] == 11
    assert calls[-1] == {"offset": 11, "timeout": 0, "limit": 1}
