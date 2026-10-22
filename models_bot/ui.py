"""Тексты и кнопки. Кнопки — обычные dict в формате Bot API.

Экраны: главное меню (Модели, Настройки) → Модели (Claude, GPT) → Остальные модели;
у каждой модели — карточка с кнопкой «▸ Начать чат». В настройках — модель, роль,
память, расход, уведомления, язык и подробность ответов. «Назад» ведёт на экран выше.

Чат начинается только кнопкой «▸ Начать чат» (или /chat): бот присылает сообщение
о начале чата и клавиатуру внизу — «↺ Новый диалог» и «■ Завершить чат», а в поле
ввода видно, какой модели вы пишете. Без открытого чата сообщения моделям не уходят.

Данные кнопок: nav:<экран>, card:<модель>:<откуда>, use:<модель>:<откуда>,
chat:<модель>[:<откуда>], tgl:<настройка>, role:<роль>.

Оформление монохромное: только символы Unicode без эмодзи-представления, тонкие
и одного веса. Знаки разработчиков — в catalog.VENDOR_MARKS, остальное здесь:
⁂ модели, ⛭ настройки, ▸ начать чат, ■ завершить, ↺ новый диалог, ✎ своя роль,
ⓘ уведомление, → ← ↓ ↳ переходы, ✓ выбранное, · разделитель.
"""

from models_bot.answers import TEST_DAILY_LIMIT, WARN_SHARE, escape, num, plural
from models_bot.catalog import (
    CUSTOM_ROLE,
    MODELS,
    OTHER_MODELS,
    ROLE_MAX_LENGTH,
    ROLES,
    TOP_MODELS,
    Model,
)
from models_bot.store import DETAILS, LANGS, Prefs

BRAND = "unknown shop"
MODELS_TITLE = "⁂ Модели"
SETTINGS = "⛭ Настройки"
START_CHAT = "▸ Начать чат"
NEW_DIALOG = "↺ Новый диалог"
END_CHAT = "■ Завершить чат"
TO_MENU = "← В меню"
BACK = "← Назад"

COMMANDS = [
    {"command": "start", "description": "⌂ Главное меню"},
    {"command": "models", "description": "⁂ Модели"},
    {"command": "chat", "description": "▸ Начать чат с основной моделью"},
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


# Тексты


def welcome(first_name: str | None, prefs: Prefs) -> str:
    name = escape(first_name or "друг")
    if prefs.chat:
        status = (
            f"Открыт чат с <b>{escape(MODELS[prefs.chat].label)}</b>: просто пишите сообщения.\n"
            f"Закончить — «{END_CHAT}» внизу или /stop."
        )
    else:
        status = (
            f"Чтобы начать чат: {MODELS_TITLE} → выберите модель → «{START_CHAT}».\n"
            f"Или сразу /chat — чат с основной моделью {escape(MODELS[prefs.model].name)}."
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


def model_card(model: Model, prefs: Prefs) -> str:
    lines = [
        f"<b>{escape(model.label)}</b>",
        f"<i>{escape(model.vendor)} · тестовая модель</i>",
        "",
        f"<blockquote>{escape(model.about)}</blockquote>",
        "",
        f"ID · <code>{escape(model.id)}</code>",
    ]
    if prefs.chat == model.id:
        lines.append("▸ Сейчас открыт чат с этой моделью")
    if prefs.model == model.id:
        lines.append("✓ Основная модель")
    return "\n".join(lines)


def chat_started(model: Model, prefs: Prefs, previous: Model | None = None) -> str:
    head = f"<b>▸ Чат начат · {escape(model.label)}</b>"
    if previous and previous.id != model.id:
        head += f"\n<i>Чат с {escape(previous.name)} завершён.</i>"
    return (
        f"{head}\n\n"
        f"<blockquote>Роль — {escape(prefs.role_title.lower())}\n"
        f"Память — {memory_summary(prefs)}\n"
        f"Ответы — {DETAILS[prefs.detail]}, {LANGS[prefs.lang]}</blockquote>\n\n"
        f"Напишите сообщение, и {escape(model.name)} ответит.\n"
        f"Внизу: «{NEW_DIALOG}» очищает переписку, «{END_CHAT}» закрывает чат."
    )


def chat_ended(model: Model | None) -> str:
    name = f" с {escape(model.name)}" if model else ""
    return f"■ Чат{name} завершён. Переписка удалена."


def new_dialog_done(model: Model) -> str:
    return f"↺ Новый диалог с {escape(model.name)}: прошлые сообщения забыты."


def chat_already_open(model: Model) -> str:
    return (
        f"▸ Чат с {escape(model.name)} уже открыт: просто пишите сообщение.\n"
        f"Начать с чистого листа — «{NEW_DIALOG}» внизу."
    )


def no_chat_text() -> str:
    return (
        "Чат ещё не начат, поэтому сообщение никуда не ушло.\n\n"
        f"Выберите модель и нажмите «{START_CHAT}» или начните с основной моделью ↓"
    )


NO_CHAT_TO_CLOSE = "Чат и так не открыт. Начать: /chat или ⁂ Модели → модель → «▸ Начать чат»."


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
    return "<b>⛭ Основная модель</b>\nС ней начинается чат по /chat."


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
    "Пока чат открыт, все ваши сообщения уходят этой модели.\n\n"
    "<blockquote>/start — главное меню\n"
    "/models — модели\n"
    "/chat — начать чат с основной моделью\n"
    "/new — новый диалог в открытом чате\n"
    "/stop — завершить чат\n"
    "/settings — настройки</blockquote>"
)

ONLY_TEXT = "Пока понимаю только текст. Напишите вопрос словами."
ROLE_ONLY_TEXT = "Пришлите инструкцию текстом или нажмите «← Отмена»."
STALE_BUTTON = "Кнопка устарела, открываю меню"
ROLE_SAVED = "✓ Своя роль сохранена"


# Кнопки


def main_keyboard() -> dict:
    return inline([button(MODELS_TITLE, "nav:models"), button(SETTINGS, "nav:settings")])


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


def card_keyboard(model: Model, prefs: Prefs, src: str) -> dict:
    start = "↺ Начать чат заново" if prefs.chat == model.id else START_CHAT
    main = "✓ Основная модель" if prefs.model == model.id else "Сделать основной"
    back = "nav:other" if src == "other" else "nav:models"
    return inline(
        [button(start, f"chat:{model.id}:{src or 'models'}")],
        [button(main, f"use:{model.id}:{src}")],
        [button(BACK, back)],
    )


def no_chat_keyboard(prefs: Prefs) -> dict:
    return inline(
        [button(f"▸ Чат с {MODELS[prefs.model].name}", f"chat:{prefs.model}")],
        [button(MODELS_TITLE, "nav:models")],
    )


def chat_keyboard(model: Model) -> dict:
    """Клавиатура внизу на время чата: в поле ввода видно, кому вы пишете."""
    return {
        "keyboard": [[{"text": NEW_DIALOG}, {"text": END_CHAT}]],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": f"Сообщение для {model.name}"[:64],
    }


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
