"""Тексты и кнопки. Кнопки — обычные dict в формате Bot API.

Экраны: главное меню (Модели, Настройки, Мои чаты) → Модели (Claude, GPT) →
Остальные модели; у каждой модели — карточка с кнопкой «▸ Начать чат». В настройках —
модель, роль, память, расход, уведомления, язык и подробность ответов.
«Назад» ведёт на экран выше.

Чаты: у пользователя их может быть много, у каждого своя модель и переписка.
«☰ Мои чаты» — список: нажатие на чат переключает в него, «✎ Изменить» — переименовать
или удалить. Чат начинается кнопкой «▸ Начать чат» (или /chat): бот присылает сообщение
о начале и клавиатуру внизу — «☰ Чаты», «↺ Новый диалог», «■ Завершить чат», а в поле
ввода видно, какой модели вы пишете. Без открытого чата сообщения моделям не уходят.

Данные кнопок: nav:<экран>, card:<модель>:<откуда>, use:<модель>:<откуда>,
chat:<модель>[:<откуда>], tgl:<настройка>, role:<роль>, chats:<страница>:<s|e>,
open:<чат>:<l|i|h> (из списка, карточки чата, подсказки), info:<чат>, ren:<чат>,
del:<чат>, delok:<чат>, newchat.

Оформление монохромное: только символы Unicode без эмодзи-представления, тонкие
и одного веса. Знаки разработчиков — в catalog.VENDOR_MARKS, остальное здесь:
⁂ модели, ⛭ настройки, ☰ чаты, ▸ начать или открыть чат, ■ завершить, ↺ новый диалог,
✎ изменить, ✕ удалить, ⓘ уведомление, → ← ↓ ↳ переходы, ✓ выбранное, · разделитель.
"""

import time

from models_bot.answers import TEST_DAILY_LIMIT, WARN_SHARE, escape, num, plural, shorten
from models_bot.catalog import (
    CUSTOM_ROLE,
    MODELS,
    OTHER_MODELS,
    ROLE_MAX_LENGTH,
    ROLES,
    TOP_MODELS,
    Model,
)
from models_bot.store import DETAILS, LANGS, MAX_CHATS, TITLE_LENGTH, Chat, Prefs

BRAND = "unknown shop"
MODELS_TITLE = "⁂ Модели"
SETTINGS = "⛭ Настройки"
MY_CHATS = "☰ Мои чаты"
START_CHAT = "▸ Начать чат"
CHATS = "☰ Чаты"
NEW_DIALOG = "↺ Новый диалог"
END_CHAT = "■ Завершить чат"
CHAT_BUTTONS = (CHATS, NEW_DIALOG, END_CHAT)  # клавиатура внизу во время чата
TO_MENU = "← В меню"
BACK = "← Назад"
CHATS_PAGE = 8  # чатов на странице списка
MSK = 3 * 3600  # время в списке чатов — московское, как и сброс лимита

COMMANDS = [
    {"command": "start", "description": "⌂ Главное меню"},
    {"command": "chats", "description": "☰ Мои чаты: переключиться"},
    {"command": "models", "description": "⁂ Модели"},
    {"command": "chat", "description": "▸ Новый чат с основной моделью"},
    {"command": "new", "description": "↺ Новый диалог"},
    {"command": "stop", "description": "■ Завершить чат"},
    {"command": "settings", "description": "⛭ Настройки"},
    {"command": "help", "description": "ⓘ Помощь"},
]

# Переключатели в настройках: подпись на кнопке
TOGGLES = {
    "memory": "Память",
    "show_usage": "Расход",
    "lang": "Язык",
    "detail": "Ответы",
    "notify_models": "Новые модели",
    "notify_limit": "Окончание лимита",
}

REMOVE_KEYBOARD = {"remove_keyboard": True}


def button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def inline(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [row for row in rows if row]}


def column(buttons: list[dict]) -> list[list[dict]]:
    return [[b] for b in buttons]


def pairs(buttons: list[dict]) -> list[list[dict]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def on(flag: bool) -> str:
    return "вкл" if flag else "выкл"


def toggle_value(key: str, prefs: Prefs) -> str:
    if key == "lang":
        return LANGS[prefs.lang]
    if key == "detail":
        return DETAILS[prefs.detail]
    return on(getattr(prefs, key))


def toggle_note(key: str, prefs: Prefs) -> str:
    return f"✓ {TOGGLES[key]} · {toggle_value(key, prefs)}"


def notify_summary(prefs: Prefs) -> str:
    parts = [
        name
        for flag, name in ((prefs.notify_models, "новые модели"), (prefs.notify_limit, "лимит"))
        if flag
    ]
    return " и ".join(parts) if parts else "выключены"


def memory_summary(prefs: Prefs) -> str:
    return "помню переписку" if prefs.memory else "каждый вопрос отдельно"


def messages(n: int) -> str:
    return plural(n, "сообщение", "сообщения", "сообщений")


def chat_name(chat: Chat) -> str:
    """Название чата: первый вопрос или своё, а у пустого — «Чат с моделью»."""
    if chat.title:
        return chat.title
    model = MODELS.get(chat.model)
    return f"Чат с {model.name}" if model else "Чат"


def chat_label(chat: Chat) -> str:
    model = MODELS.get(chat.model)
    return f"{model.mark if model else '·'} {chat_name(chat)}"


def when(ts: float, now: float | None = None) -> str:
    """«сегодня 19:40», «вчера 08:15» или «21.10» — по Москве."""
    now = time.time() if now is None else now
    day = time.gmtime(ts + MSK)
    days = int((now + MSK) // 86400) - int((ts + MSK) // 86400)
    clock = time.strftime("%H:%M", day)
    if days == 0:
        return f"сегодня {clock}"
    if days == 1:
        return f"вчера {clock}"
    return time.strftime("%d.%m", day)


# Тексты


def welcome(first_name: str | None, prefs: Prefs, chat: Chat | None = None) -> str:
    name = escape(first_name or "друг")
    if chat:
        status = (
            f"Открыт чат «{escape(chat_name(chat))}» · {escape(MODELS[chat.model].label)}: "
            "просто пишите сообщения.\n"
            f"Переключиться — «{CHATS}» внизу, закрыть — «{END_CHAT}»."
        )
    else:
        status = (
            f"Начать чат: {MODELS_TITLE} → модель → «{START_CHAT}» или сразу /chat "
            f"с основной моделью {escape(MODELS[prefs.model].name)}.\n"
            f"Все ваши чаты — в «{MY_CHATS}»: между ними можно переключаться."
        )
    return (
        f"<b>{BRAND}</b> · нейросети в одном окне\n"
        f"Привет, {name}.\n\n"
        "<blockquote>✻ Claude — код, тексты, анализ\n"
        "⌬ GPT — универсальные задачи\n"
        "✧ Gemini — линейка Google</blockquote>\n\n"
        f"{status}\n\n"
        "<i>Тестовый режим: модели отвечают заготовками.</i>"
    )


def models_text() -> str:
    return (
        f"<b>{MODELS_TITLE}</b>\nТоп нейросетей: Claude и GPT\n\n"
        f"Выберите модель ↓ В её карточке — «{START_CHAT}»."
    )


def other_text() -> str:
    return "<b>Остальные модели</b>\nGPT-6.1 и линейка Gemini\n\nВыберите модель ↓"


def model_card(model: Model, prefs: Prefs, open_model: str = "") -> str:
    """open_model — модель открытого чата."""
    lines = [
        f"<b>{escape(model.label)}</b>",
        f"<i>{escape(model.vendor)} · тестовая модель</i>",
        "",
        f"<blockquote>{escape(model.about)}</blockquote>",
        "",
        f"ID · <code>{escape(model.id)}</code>",
    ]
    if open_model == model.id:
        lines.append("▸ Сейчас открыт чат с этой моделью")
    if prefs.model == model.id:
        lines.append("✓ Основная модель")
    return "\n".join(lines)


def _chat_bottom_hint() -> str:
    return (
        f"Внизу: «{CHATS}» — переключиться, «{NEW_DIALOG}» — ещё один чат с этой моделью, "
        f"«{END_CHAT}» — закрыть."
    )


def chat_started(
    model: Model, prefs: Prefs, previous: Chat | None = None, dropped: list[Chat] = ()
) -> str:
    """previous — открытый до этого чат (он сохранён), dropped — удалённые сверх лимита."""
    lines = [f"<b>▸ Новый чат · {escape(model.label)}</b>"]
    if previous:
        lines.append(f"<i>Чат «{escape(chat_name(previous))}» сохранён в «{MY_CHATS}».</i>")
    for chat in dropped:
        lines.append(
            f"<i>Самый давний чат «{escape(chat_name(chat))}» удалён: храню до {MAX_CHATS}.</i>"
        )
    lines += [
        "",
        f"<blockquote>Роль — {escape(prefs.role_title.lower())}\n"
        f"Память — {memory_summary(prefs)}\n"
        f"Ответы — {DETAILS[prefs.detail]}, {LANGS[prefs.lang]}</blockquote>",
        "",
        f"Напишите сообщение, и {escape(model.name)} ответит.",
        _chat_bottom_hint(),
    ]
    return "\n".join(lines)


def chat_opened(chat: Chat, model: Model, last_question: str | None, prefs: Prefs) -> str:
    lines = [
        f"<b>▸ Чат «{escape(chat_name(chat))}»</b>",
        f"<i>{escape(model.label)} · {messages(chat.messages)} · {when(chat.updated)}</i>",
    ]
    if last_question:
        lines += [
            "",
            f"<blockquote>Последний вопрос: «{escape(shorten(last_question))}»</blockquote>",
        ]
    lines.append("")
    if last_question and prefs.memory:
        lines.append(f"Продолжайте: {escape(model.name)} помнит эту переписку.")
    else:
        lines.append(f"Напишите сообщение, и {escape(model.name)} ответит.")
    lines.append(_chat_bottom_hint())
    return "\n".join(lines)


def chat_ended(chat: Chat, kept: bool) -> str:
    if kept:
        return f"■ Чат «{escape(chat_name(chat))}» закрыт и сохранён в «{MY_CHATS}»."
    return "■ Чат закрыт. Он был пустым, поэтому в списке его нет."


def chat_already_open(chat: Chat, model: Model) -> str:
    return (
        f"▸ Чат «{escape(chat_name(chat))}» с {escape(model.name)} уже открыт: "
        "просто пишите сообщение.\n"
        f"Ещё один чат с этой моделью — «{NEW_DIALOG}» внизу."
    )


def no_chat_text() -> str:
    return (
        "Чат не открыт, поэтому сообщение никуда не ушло.\n\n"
        "Продолжите прошлый чат, начните новый или выберите из списка ↓"
    )


NO_CHAT_TO_CLOSE = (
    f"Чат и так не открыт. Начать: /chat или {MODELS_TITLE} → модель → «{START_CHAT}», "
    f"продолжить старый — «{MY_CHATS}»."
)


def chats_text(chats: list[Chat], active: Chat | None, edit: bool) -> str:
    if not chats:
        return (
            f"<b>{MY_CHATS}</b>\n\n"
            "Чатов пока нет. Выберите модель и нажмите «▸ Начать чат» — "
            "чат появится здесь, и к нему можно будет вернуться."
        )
    head = f"<b>{'✎' if edit else '☰'} Мои чаты</b> · {len(chats)}"
    now = f"Открыт: {escape(chat_label(active))}" if active else "Сейчас ни один чат не открыт."
    hint = (
        "Выберите чат, чтобы переименовать или удалить ↓"
        if edit
        else "Нажмите на чат, чтобы перейти в него ↓"
    )
    return f"{head}\n{now}\n\n{hint}"


def chat_info_text(chat: Chat, active_id: int, last_question: str | None) -> str:
    model = MODELS.get(chat.model)
    lines = [
        f"<b>{escape(chat_label(chat))}</b>",
        f"<i>{escape(model.name) if model else 'Модель больше недоступна'} · "
        f"{messages(chat.messages)} · {when(chat.updated)}</i>",
    ]
    if chat.id == active_id:
        lines.append("▸ Сейчас открыт")
    if last_question:
        lines += [
            "",
            f"<blockquote>Последний вопрос: «{escape(shorten(last_question))}»</blockquote>",
        ]
    return "\n".join(lines)


def rename_text(chat: Chat) -> str:
    return (
        "<b>✎ Название чата</b>\n"
        f"Сейчас: «{escape(chat_name(chat))}»\n\n"
        f"Пришлите новое название одним сообщением, до {TITLE_LENGTH} символов."
    )


def title_too_long(length: int) -> str:
    return (
        f"Слишком длинно: {plural(length, 'символ', 'символа', 'символов')} "
        f"из {TITLE_LENGTH}. Пришлите название покороче."
    )


TITLE_SAVED = "✓ Название сохранено"


def delete_text(chat: Chat) -> str:
    return (
        f"<b>✕ Удалить чат «{escape(chat_name(chat))}»?</b>\n"
        f"{messages(chat.messages).capitalize()} удалятся, вернуть их не получится."
    )


def chat_deleted(chat: Chat) -> str:
    return f"✕ Открытый чат «{escape(chat_name(chat))}» удалён."


CHAT_GONE = "Этого чата больше нет"


def settings_text(prefs: Prefs, used_today: int) -> str:
    return (
        f"<b>{SETTINGS}</b>\n\n"
        f"<blockquote>Модель — {escape(MODELS[prefs.model].label)}\n"
        f"Роль — {escape(prefs.role_title.lower())}\n"
        f"Память диалога — {'включена' if prefs.memory else 'выключена'}\n"
        f"Расход токенов — {'показывать' if prefs.show_usage else 'не показывать'}\n"
        f"Уведомления — {notify_summary(prefs)}\n"
        f"Язык ответов — {LANGS[prefs.lang]}\n"
        f"Ответы — {DETAILS[prefs.detail]}</blockquote>\n\n"
        f"Сегодня израсходовано {num(used_today)} из {num(TEST_DAILY_LIMIT)} тестовых токенов.\n"
        "Нажмите на пункт, чтобы изменить. Роль, память и ответы сразу действуют и в открытом чате."
    )


def picker_text() -> str:
    return "<b>⛭ Основная модель</b>\nС ней начинается новый чат по /chat."


def role_text(prefs: Prefs, note: str = "") -> str:
    prompt = escape(prefs.role_prompt) if prefs.role_prompt else "Модель отвечает без инструкции."
    head = f"{note}\n\n" if note else ""
    return (
        f"{head}<b>⛭ Роль</b>\n"
        "Инструкция, которую модель получает перед каждым вопросом.\n\n"
        f"<blockquote><b>{escape(prefs.role_title)}</b>\n{prompt}</blockquote>"
    )


def role_input_text() -> str:
    return (
        "<b>✎ Своя роль</b>\n"
        f"Пришлите инструкцию одним сообщением, до {ROLE_MAX_LENGTH} символов.\n\n"
        "<blockquote>Например: отвечай как юрист по трудовому праву и ссылайся "
        "на статьи ТК РФ.</blockquote>"
    )


def role_too_long(length: int) -> str:
    return (
        f"Слишком длинно: {plural(length, 'символ', 'символа', 'символов')} "
        f"из {ROLE_MAX_LENGTH}. Сократите инструкцию и пришлите ещё раз."
    )


def notify_text() -> str:
    return (
        "<b>⛭ Уведомления</b>\n\n"
        "<blockquote>Новые модели — сообщу, когда в боте появится модель\n"
        f"Окончание лимита — предупрежу на {round(WARN_SHARE * 100)}% и 100% "
        "дневного лимита</blockquote>"
    )


def new_models_text(models: list[Model]) -> str:
    names = "\n".join(escape(m.label) for m in models)
    return (
        "<b>ⓘ Новые модели в боте</b>\n\n"
        f"<blockquote>{names}</blockquote>\n\n"
        "Откройте карточку ↓\n"
        "<i>Отключить: ⛭ Настройки → Уведомления.</i>"
    )


HELP = (
    "<b>ⓘ Помощь</b>\n\n"
    f"Чат с моделью начинается кнопкой «{START_CHAT}» в карточке модели или командой /chat. "
    "Пока чат открыт, все ваши сообщения уходят этой модели. Чатов может быть сколько угодно: "
    f"у каждого своя модель и переписка, переключаться — в «{MY_CHATS}».\n\n"
    "<blockquote>/start — главное меню\n"
    "/chats — мои чаты и переключение\n"
    "/models — модели\n"
    "/chat — новый чат с основной моделью\n"
    "/new — ещё один чат с моделью открытого\n"
    "/stop — закрыть чат (он сохранится)\n"
    "/settings — настройки</blockquote>"
)

ONLY_TEXT = "Пока понимаю только текст. Напишите вопрос словами."
ROLE_ONLY_TEXT = "Пришлите инструкцию текстом или нажмите «← Отмена»."
TITLE_ONLY_TEXT = "Пришлите название текстом или нажмите «← Отмена»."
STALE_BUTTON = "Кнопка устарела, открываю меню"
ROLE_SAVED = "✓ Своя роль сохранена"


# Кнопки


def main_keyboard() -> dict:
    return inline(
        [button(MODELS_TITLE, "nav:models"), button(SETTINGS, "nav:settings")],
        [button(MY_CHATS, "nav:chats")],
    )


def models_keyboard() -> dict:
    models = [button(m.label, f"card:{m.id}:models") for m in TOP_MODELS]
    return inline(
        *column(models),
        [button("Остальные модели →", "nav:other")],
        [button(TO_MENU, "nav:main")],
    )


def other_keyboard() -> dict:
    models = [button(m.label, f"card:{m.id}:other") for m in OTHER_MODELS]
    return inline(*column(models), [button(BACK, "nav:models")])


def card_keyboard(model: Model, prefs: Prefs, src: str, open_model: str = "") -> dict:
    start = "▸ Ещё один чат с ней" if open_model == model.id else START_CHAT
    main = "✓ Основная модель" if prefs.model == model.id else "Сделать основной"
    back = "nav:other" if src == "other" else "nav:models"
    return inline(
        [button(start, f"chat:{model.id}:{src or 'models'}")],
        [button(main, f"use:{model.id}:{src}")],
        [button(BACK, back)],
    )


def no_chat_keyboard(prefs: Prefs, last: Chat | None = None) -> dict:
    rows = []
    if last and last.model in MODELS:
        rows.append([button(f"▸ Продолжить «{shorten(chat_name(last), 28)}»", f"open:{last.id}:h")])
    rows += [
        [button(f"▸ Новый чат с {MODELS[prefs.model].name}", f"chat:{prefs.model}")],
        [button(MY_CHATS, "nav:chats"), button(MODELS_TITLE, "nav:models")],
    ]
    return inline(*rows)


def chat_keyboard(model: Model) -> dict:
    """Клавиатура внизу на время чата: в поле ввода видно, кому вы пишете."""
    return {
        "keyboard": [[{"text": CHATS}, {"text": NEW_DIALOG}], [{"text": END_CHAT}]],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": f"Сообщение для {model.name}"[:64],
    }


def chats_keyboard(chats: list[Chat], active_id: int, page: int, edit: bool, prefs: Prefs) -> dict:
    mode = "e" if edit else "s"
    pages = max(1, -(-len(chats) // CHATS_PAGE))
    page = min(max(page, 0), pages - 1)
    rows = []
    for chat in chats[page * CHATS_PAGE : (page + 1) * CHATS_PAGE]:
        label = chat_label(chat) + ("  ✓" if chat.id == active_id else "")
        rows.append([button(label, f"info:{chat.id}" if edit else f"open:{chat.id}:l")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(button("←", f"chats:{page - 1}:{mode}"))
        nav.append(button(f"{page + 1} из {pages}", f"chats:{page}:{mode}"))
        if page < pages - 1:
            nav.append(button("→", f"chats:{page + 1}:{mode}"))
        rows.append(nav)
    if chats:
        rows.append(
            [
                button("+ Новый чат", "newchat"),
                button(
                    "✓ Готово" if edit else "✎ Изменить", f"chats:{page}:{'s' if edit else 'e'}"
                ),
            ]
        )
    else:
        rows.append([button(f"+ Новый чат с {MODELS[prefs.model].name}", "newchat")])
        rows.append([button(MODELS_TITLE, "nav:models")])
    rows.append([button(TO_MENU, "nav:main")])
    return inline(*rows)


def chat_info_keyboard(chat: Chat, active_id: int) -> dict:
    go = "▸ Продолжить чат" if chat.id == active_id else "▸ Перейти в чат"
    rows = []
    if chat.model in MODELS:
        rows.append([button(go, f"open:{chat.id}:i")])
    rows += [
        [button("✎ Переименовать", f"ren:{chat.id}"), button("✕ Удалить", f"del:{chat.id}")],
        [button("← Все чаты", "chats:0:e")],
    ]
    return inline(*rows)


def rename_keyboard(chat: Chat) -> dict:
    return inline([button("← Отмена", f"info:{chat.id}")])


def delete_keyboard(chat: Chat) -> dict:
    return inline([button("✕ Да, удалить", f"delok:{chat.id}"), button("← Нет", f"info:{chat.id}")])


def _toggle(key: str, prefs: Prefs) -> dict:
    return button(f"{TOGGLES[key]} · {toggle_value(key, prefs)}", f"tgl:{key}")


def settings_keyboard(prefs: Prefs) -> dict:
    return inline(
        *column(
            [
                button(f"Модель · {MODELS[prefs.model].name}", "nav:picker"),
                button(f"Роль · {prefs.role_title}", "nav:role"),
                _toggle("memory", prefs),
                _toggle("show_usage", prefs),
                button("Уведомления →", "nav:notify"),
                _toggle("lang", prefs),
                _toggle("detail", prefs),
                button(TO_MENU, "nav:main"),
            ]
        )
    )


def picker_keyboard(prefs: Prefs) -> dict:
    models = [
        button(f"{m.label}  ✓" if m.id == prefs.model else m.label, f"use:{m.id}:picker")
        for m in TOP_MODELS + OTHER_MODELS
    ]
    return inline(*column(models), [button(BACK, "nav:settings")])


def role_keyboard(prefs: Prefs) -> dict:
    roles = [
        button(f"{r.title}  ✓" if r.key == prefs.role else r.title, f"role:{r.key}")
        for r in ROLES.values()
    ]
    custom = "✎ Своя роль" + ("  ✓" if prefs.role == CUSTOM_ROLE else "")
    return inline(
        *pairs(roles), [button(custom, f"role:{CUSTOM_ROLE}")], [button(BACK, "nav:settings")]
    )


def role_input_keyboard() -> dict:
    return inline([button("← Отмена", "nav:role")])


def notify_keyboard(prefs: Prefs) -> dict:
    return inline(
        [_toggle("notify_models", prefs)],
        [_toggle("notify_limit", prefs)],
        [button(BACK, "nav:settings")],
    )


def new_models_keyboard(models: list[Model]) -> dict:
    return inline(
        *column(
            [
                button(m.label, f"card:{m.id}:{'models' if m in TOP_MODELS else 'other'}")
                for m in models[:8]
            ]
        )
    )
