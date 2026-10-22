"""Тексты и кнопки. Оформление — только символы Unicode без эмодзи-представления,
чтобы всё было в одной чёрно-серой гамме: ✦ ✧ ◆ ◇ ➜ → ←."""

from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import BotCommand, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models_bot.catalog import OTHER_MODELS, TOP_MODELS, Model

BRAND = "unknown shop"
LINE = "─────────────────"
PAGE_SIZE = 8

COMMANDS = [
    BotCommand(command="start", description="✦ Главное меню"),
    BotCommand(command="models", description="◆ Топ нейросетей"),
    BotCommand(command="other", description="◇ Остальные модели"),
    BotCommand(command="help", description="➜ Помощь"),
]


class Nav(CallbackData, prefix="nav"):
    to: str  # main | other
    page: int = 0


class Pick(CallbackData, prefix="pick"):
    model_id: str
    page: int = -1  # -1 — пришли из главного меню, иначе — со страницы «Остальные»


def mark(model: Model) -> str:
    return "✦" if model.vendor == "Anthropic" else "◆" if model.vendor == "OpenAI" else "◇"


def welcome(first_name: str | None) -> str:
    name = escape(first_name or "друг")
    return (
        f"<b>✦ {BRAND}</b>\n"
        f"{LINE}\n"
        f"Привет, {name}.\n"
        "Лучшие нейросети в одном месте.\n\n"
        "<b>◆ Топ моделей</b>\n"
        "Сначала Claude, затем GPT ➜ выберите модель ниже."
    )


HELP = (
    f"<b>➜ Помощь</b>\n{LINE}\n"
    "/start  ✦ главное меню\n"
    "/models  ◆ топ нейросетей\n"
    "/other  ◇ остальные модели"
)


def model_card(model: Model) -> str:
    lines = [
        f"<b>{mark(model)} {escape(model.name)}</b>",
        LINE,
        f"Разработчик → {escape(model.vendor)}",
    ]
    lines.append(f"ID → <code>{escape(model.id)}</code>")
    if model.about:
        lines += ["", escape(model.about)]
    return "\n".join(lines)


def other_title(page: int, pages: int) -> str:
    return f"<b>◇ Остальные модели</b>\n{LINE}\nСтраница {page + 1} из {pages}"


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in TOP_MODELS:
        builder.button(text=f"{mark(m)} {m.name}", callback_data=Pick(model_id=m.id))
    builder.button(text="◇ Остальные модели ➜", callback_data=Nav(to="other"))
    builder.adjust(1)
    return builder.as_markup()


def pages_count() -> int:
    return max(1, -(-len(OTHER_MODELS) // PAGE_SIZE))


def other_keyboard(page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    chunk = OTHER_MODELS[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    for m in chunk:
        builder.button(text=f"{mark(m)} {m.name}", callback_data=Pick(model_id=m.id, page=page))
    nav = []
    if page > 0:
        builder.button(text="←", callback_data=Nav(to="other", page=page - 1))
        nav.append(1)
    if page < pages_count() - 1:
        builder.button(text="→", callback_data=Nav(to="other", page=page + 1))
        nav.append(1)
    builder.button(text="← В меню", callback_data=Nav(to="main"))
    builder.adjust(*([2] * (-(-len(chunk) // 2))), len(nav) or 1, 1)
    return builder.as_markup()


def model_keyboard(page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if page >= 0:
        builder.button(text="← К списку", callback_data=Nav(to="other", page=page))
    builder.button(text="← В меню", callback_data=Nav(to="main"))
    builder.adjust(1)
    return builder.as_markup()
