from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from models_bot import ui
from models_bot.catalog import MODELS
from models_bot.store import DETAILS, LANGS, Store, next_value

router = Router()


async def show(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Перерисовать экран в том же сообщении."""
    if not isinstance(call.message, Message):
        # Сообщение слишком старое, править его нельзя — присылаем экран заново
        await call.bot.send_message(call.from_user.id, text, reply_markup=markup)
        return
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        # Повторное нажатие на ту же кнопку: экран уже такой
        if "message is not modified" not in e.message:
            raise


@router.message(CommandStart())
async def start(message: Message, store: Store) -> None:
    prefs = store.get(message.from_user.id)
    await message.answer(
        ui.welcome(message.from_user.first_name, prefs), reply_markup=ui.main_keyboard()
    )


@router.message(Command("models"))
async def models(message: Message) -> None:
    await message.answer(ui.models_text(), reply_markup=ui.models_keyboard())


@router.message(Command("other"))
async def other(message: Message) -> None:
    await message.answer(ui.other_text(), reply_markup=ui.other_keyboard())


@router.message(Command("settings"))
async def settings(message: Message, store: Store) -> None:
    prefs = store.get(message.from_user.id)
    await message.answer(ui.settings_text(prefs), reply_markup=ui.settings_keyboard(prefs))


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(ui.HELP)


@router.callback_query(ui.Nav.filter(F.to == "main"))
async def nav_main(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.welcome(call.from_user.first_name, prefs), ui.main_keyboard())
    await call.answer("← Главное меню")


@router.callback_query(ui.Nav.filter(F.to == "models"))
async def nav_models(call: CallbackQuery) -> None:
    await show(call, ui.models_text(), ui.models_keyboard())
    await call.answer(ui.MODELS_TITLE)


@router.callback_query(ui.Nav.filter(F.to == "other"))
async def nav_other(call: CallbackQuery) -> None:
    await show(call, ui.other_text(), ui.other_keyboard())
    await call.answer("→ Остальные модели")


@router.callback_query(ui.Nav.filter(F.to == "settings"))
async def nav_settings(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.settings_text(prefs), ui.settings_keyboard(prefs))
    await call.answer(ui.SETTINGS)


@router.callback_query(ui.Nav.filter(F.to == "picker"))
async def nav_picker(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.picker_text(), ui.picker_keyboard(prefs))
    await call.answer("⛭ Основная модель")


@router.callback_query(ui.Card.filter())
async def card(call: CallbackQuery, callback_data: ui.Card, store: Store) -> None:
    model = MODELS.get(callback_data.model_id)
    if model is None:
        await call.answer("Модель больше недоступна", show_alert=True)
        return
    prefs = store.get(call.from_user.id)
    await show(call, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, callback_data.src))
    await call.answer(model.label)


@router.callback_query(ui.UseModel.filter())
async def use_model(call: CallbackQuery, callback_data: ui.UseModel, store: Store) -> None:
    model = MODELS.get(callback_data.model_id)
    if model is None:
        await call.answer("Модель больше недоступна", show_alert=True)
        return
    user_id = call.from_user.id
    if store.get(user_id).model == model.id and callback_data.src != "picker":
        await call.answer(f"✓ {model.name} уже основная")
        return
    prefs = store.update(user_id, model=model.id)
    if callback_data.src == "picker":
        text, markup = ui.settings_text(prefs), ui.settings_keyboard(prefs)
    else:
        text = ui.model_card(model, prefs)
        markup = ui.card_keyboard(model, prefs, callback_data.src)
    await show(call, text, markup)
    await call.answer(f"✓ Основная модель · {model.name}")


@router.callback_query(ui.Toggle.filter(F.key.in_({"lang", "detail"})))
async def toggle(call: CallbackQuery, callback_data: ui.Toggle, store: Store) -> None:
    user_id = call.from_user.id
    prefs = store.get(user_id)
    if callback_data.key == "lang":
        prefs = store.update(user_id, lang=next_value(LANGS, prefs.lang))
        note = f"Язык ответов · {LANGS[prefs.lang]}"
    else:
        prefs = store.update(user_id, detail=next_value(DETAILS, prefs.detail))
        note = f"Ответы · {DETAILS[prefs.detail]}"
    await show(call, ui.settings_text(prefs), ui.settings_keyboard(prefs))
    await call.answer(f"✓ {note}")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Выберите раздел ↓", reply_markup=ui.main_keyboard())
