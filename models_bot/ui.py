"""Тексты и кнопки.

Экраны: главное меню (Модели, Настройки) → Модели (Claude, GPT) → Остальные модели;
у каждой модели — карточка. В настройках — модель, роль, память, расход, уведомления,
язык и подробность ответов. Кнопка «Назад» ведёт на экран выше.

Оформление монохромное: только символы Unicode без эмодзи-представления, тонкие
и одного веса. Знаки разработчиков — в catalog.VENDOR_MARKS, остальное здесь:
⁂ модели, ⛭ настройки, ✎ своя роль, ↺ новый диалог, ⓘ уведомление,
→ ← ↓ ↳ переходы, ✓ выбранное, · разделитель.
"""

from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import BotCommand, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models_bot.answers import TEST_DAILY_LIMIT, WARN_SHARE, num
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
NEW_DIALOG = "↺ Новый диалог"
TO_MENU = "← В меню"
BACK = "← Назад"

COMMANDS = [
    BotCommand(command="start", description="⌂ Главное меню"),
    BotCommand(command="models", description="⁂ Модели"),
    BotCommand(command="settings", description="⛭ Настройки"),
    BotCommand(command="new", description="↺ Новый диалог"),
    BotCommand(command="help", description="ⓘ Помощь"),
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


class Nav(CallbackData, prefix="nav"):
    to: str  # main | models | other | settings | picker | role | notify


class Card(CallbackData, prefix="card"):
    model_id: str
    src: str = "models"  # models | other — куда ведёт «Назад»


class UseModel(CallbackData, prefix="use"):
    model_id: str
    src: str = "models"  # models | other — из карточки, picker — из настроек


class Toggle(CallbackData, prefix="tgl"):
    key: str  # ключ из TOGGLES


class SetRole(CallbackData, prefix="role"):
    key: str  # ключ из catalog.ROLES или CUSTOM_ROLE


class NewDialog(CallbackData, prefix="dlg"):
    pass


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


# Тексты


def welcome(first_name: str | None, prefs: Prefs) -> str:
    name = escape(first_name or "друг")
    return (
        f"<b>{BRAND}</b> · нейросети в одном окне\n"
        f"Привет, {name}.\n\n"
        "<blockquote>✻ Claude — код, тексты, анализ\n"
        "⌬ GPT — универсальные задачи\n"
        "✧ Gemini — линейка Google</blockquote>\n\n"
        f"Основная модель · <b>{escape(MODELS[prefs.model].name)}</b>\n"
        "Напишите вопрос, и она ответит, или выберите раздел ↓\n\n"
        "<i>Тестовый режим: модели отвечают заготовками.</i>"
    )


def models_text() -> str:
    return f"<b>{MODELS_TITLE}</b>\nТоп нейросетей: Claude и GPT\n\nВыберите модель ↓"


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
    if prefs.model == model.id:
        lines.append("✓ Основная модель")
    return "\n".join(lines)


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
        "Нажмите на пункт, чтобы изменить."
    )


def picker_text() -> str:
    return "<b>⛭ Основная модель</b>\nОна отвечает на ваши сообщения."


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
        f"Слишком длинно: {length} символов из {ROLE_MAX_LENGTH}. "
        "Сократите инструкцию и пришлите ещё раз."
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
    "Напишите вопрос, и основная модель ответит.\n\n"
    "<blockquote>/start — главное меню\n"
    "/models — модели\n"
    "/settings — настройки\n"
    "/new — новый диалог</blockquote>"
)

NEW_DIALOG_DONE = "↺ Новый диалог: прошлые сообщения забыты."
ONLY_TEXT = "Пока понимаю только текст. Напишите вопрос или выберите раздел ↓"
ROLE_ONLY_TEXT = "Пришлите инструкцию текстом или нажмите «← Отмена»."


# Кнопки


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=MODELS_TITLE, callback_data=Nav(to="models"))
    builder.button(text=SETTINGS, callback_data=Nav(to="settings"))
    builder.adjust(2)
    return builder.as_markup()


def models_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in TOP_MODELS:
        builder.button(text=m.label, callback_data=Card(model_id=m.id))
    builder.button(text="Остальные модели →", callback_data=Nav(to="other"))
    builder.button(text=TO_MENU, callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()


def other_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in OTHER_MODELS:
        builder.button(text=m.label, callback_data=Card(model_id=m.id, src="other"))
    builder.button(text=BACK, callback_data=Nav(to="models"))
    builder.adjust(1)
    return builder.as_markup()


def card_keyboard(model: Model, prefs: Prefs, src: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✓ Основная модель" if prefs.model == model.id else "Сделать основной",
        callback_data=UseModel(model_id=model.id, src=src),
    )
    builder.button(text=BACK, callback_data=Nav(to="other" if src == "other" else "models"))
    builder.adjust(1)
    return builder.as_markup()


def _toggle(builder: InlineKeyboardBuilder, key: str, prefs: Prefs) -> None:
    builder.button(
        text=f"{TOGGLES[key]} · {toggle_value(key, prefs)}", callback_data=Toggle(key=key)
    )


def settings_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Модель · {MODELS[prefs.model].name}", callback_data=Nav(to="picker"))
    builder.button(text=f"Роль · {prefs.role_title}", callback_data=Nav(to="role"))
    _toggle(builder, "memory", prefs)
    _toggle(builder, "show_usage", prefs)
    builder.button(text="Уведомления →", callback_data=Nav(to="notify"))
    _toggle(builder, "lang", prefs)
    _toggle(builder, "detail", prefs)
    builder.button(text=TO_MENU, callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()


def picker_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in TOP_MODELS + OTHER_MODELS:
        text = f"{m.label}  ✓" if m.id == prefs.model else m.label
        builder.button(text=text, callback_data=UseModel(model_id=m.id, src="picker"))
    builder.button(text=BACK, callback_data=Nav(to="settings"))
    builder.adjust(1)
    return builder.as_markup()


def role_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for role in ROLES.values():
        text = f"{role.title}  ✓" if role.key == prefs.role else role.title
        builder.button(text=text, callback_data=SetRole(key=role.key))
    custom = "✎ Своя роль"
    builder.button(
        text=f"{custom}  ✓" if prefs.role == CUSTOM_ROLE else custom,
        callback_data=SetRole(key=CUSTOM_ROLE),
    )
    builder.button(text=BACK, callback_data=Nav(to="settings"))
    builder.adjust(*([2] * (len(ROLES) // 2)), 1)
    return builder.as_markup()


def role_input_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Отмена", callback_data=Nav(to="role"))
    return builder.as_markup()


def notify_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _toggle(builder, "notify_models", prefs)
    _toggle(builder, "notify_limit", prefs)
    builder.button(text=BACK, callback_data=Nav(to="settings"))
    builder.adjust(1)
    return builder.as_markup()


def answer_keyboard(prefs: Prefs) -> InlineKeyboardMarkup | None:
    """Под ответом модели: начать новый диалог (только когда память включена)."""
    if not prefs.memory:
        return None
    builder = InlineKeyboardBuilder()
    builder.button(text=NEW_DIALOG, callback_data=NewDialog())
    return builder.as_markup()


def new_models_keyboard(models: list[Model]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in models[:8]:
        src = "models" if m in TOP_MODELS else "other"
        builder.button(text=m.label, callback_data=Card(model_id=m.id, src=src))
    builder.adjust(1)
    return builder.as_markup()
