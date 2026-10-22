"""Тексты и кнопки.

Оформление монохромное: только символы Unicode без эмодзи-представления, тонкие
и одного веса. Знаки разработчиков — в catalog.VENDOR_MARKS, остальное здесь:
⛭ настройки, → ← ↓ переходы, ✓ выбранное, · разделитель.
"""

from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import BotCommand, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models_bot.catalog import MODELS, OTHER_MODELS, TOP_MODELS, Model
from models_bot.store import DETAILS, LANGS, Prefs

BRAND = "unknown shop"
SETTINGS = "⛭ Настройки"
TO_MENU = "← В меню"

COMMANDS = [
    BotCommand(command="start", description="⌂ Главное меню"),
    BotCommand(command="other", description="→ Остальные модели"),
    BotCommand(command="settings", description="⛭ Настройки"),
    BotCommand(command="help", description="ⓘ Помощь"),
]


class Nav(CallbackData, prefix="nav"):
    to: str  # main | other | settings | picker


class Card(CallbackData, prefix="card"):
    model_id: str
    src: str = "main"  # main | other — куда ведёт «Назад»


class UseModel(CallbackData, prefix="use"):
    model_id: str
    src: str = "main"  # main | other — из карточки, picker — из настроек


class Toggle(CallbackData, prefix="tgl"):
    key: str  # lang | detail


def welcome(first_name: str | None, prefs: Prefs) -> str:
    name = escape(first_name or "друг")
    return (
        f"<b>{BRAND}</b> · нейросети в одном окне\n"
        f"Привет, {name}.\n\n"
        "<blockquote>✻ Claude — код, тексты, анализ\n"
        "⌬ GPT — универсальные задачи\n"
        "✧ Gemini — в остальных моделях</blockquote>\n\n"
        f"Основная модель · <b>{escape(MODELS[prefs.model].name)}</b>\n"
        "Выберите модель ↓"
    )


def other_text() -> str:
    return "<b>Остальные модели</b>\nGPT-6.1 и линейка Gemini\n\nВыберите модель ↓"


def model_card(model: Model, prefs: Prefs) -> str:
    lines = [
        f"<b>{escape(model.label)}</b>",
        f"<i>{escape(model.vendor)}</i>",
        "",
        f"<blockquote>{escape(model.about)}</blockquote>",
        "",
        f"ID · <code>{escape(model.id)}</code>",
    ]
    if prefs.model == model.id:
        lines.append("✓ Основная модель")
    return "\n".join(lines)


def settings_text(prefs: Prefs) -> str:
    return (
        f"<b>{SETTINGS}</b>\n\n"
        f"<blockquote>Модель — {escape(MODELS[prefs.model].label)}\n"
        f"Язык ответов — {LANGS[prefs.lang]}\n"
        f"Ответы — {DETAILS[prefs.detail]}</blockquote>\n\n"
        "Нажмите на пункт, чтобы изменить."
    )


def picker_text() -> str:
    return "<b>⛭ Основная модель</b>\nЕё бот предложит первой в новом диалоге."


HELP = (
    "<b>ⓘ Помощь</b>\n\n"
    "<blockquote>/start — главное меню\n"
    "/other — остальные модели\n"
    "/settings — настройки</blockquote>"
)


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in TOP_MODELS:
        builder.button(text=m.label, callback_data=Card(model_id=m.id))
    builder.button(text="Остальные модели →", callback_data=Nav(to="other"))
    builder.button(text=SETTINGS, callback_data=Nav(to="settings"))
    builder.adjust(1)
    return builder.as_markup()


def other_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in OTHER_MODELS:
        builder.button(text=m.label, callback_data=Card(model_id=m.id, src="other"))
    builder.button(text=TO_MENU, callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()


def card_keyboard(model: Model, prefs: Prefs, src: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✓ Основная модель" if prefs.model == model.id else "Сделать основной",
        callback_data=UseModel(model_id=model.id, src=src),
    )
    if src == "other":
        builder.button(text="← К списку", callback_data=Nav(to="other"))
    builder.button(text=TO_MENU, callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()


def settings_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Модель · {MODELS[prefs.model].name}", callback_data=Nav(to="picker"))
    builder.button(text=f"Язык · {LANGS[prefs.lang]}", callback_data=Toggle(key="lang"))
    builder.button(text=f"Ответы · {DETAILS[prefs.detail]}", callback_data=Toggle(key="detail"))
    builder.button(text=TO_MENU, callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()


def picker_keyboard(prefs: Prefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in TOP_MODELS + OTHER_MODELS:
        text = f"{m.label}  ✓" if m.id == prefs.model else m.label
        builder.button(text=text, callback_data=UseModel(model_id=m.id, src="picker"))
    builder.button(text="← Назад", callback_data=Nav(to="settings"))
    builder.adjust(1)
    return builder.as_markup()
