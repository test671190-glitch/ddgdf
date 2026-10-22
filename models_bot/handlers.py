import asyncio

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from models_bot import answers, ui
from models_bot.catalog import CUSTOM_ROLE, MODELS, ROLE_MAX_LENGTH, ROLES
from models_bot.store import DETAILS, LANGS, Store, Turn, next_value

router = Router()


class RoleInput(StatesGroup):
    text = State()


async def track(handler, event, data):
    """Запоминаем пользователя (ему придут уведомления) и выходим из ввода своей роли,
    если вместо неё нажали кнопку или отправили команду."""
    if event.from_user:
        data["store"].touch(event.from_user.id)
    state: FSMContext | None = data.get("state")
    leaving = isinstance(event, CallbackQuery) or (event.text or "").startswith("/")
    if state and leaving and data.get("raw_state"):
        await state.clear()
        data["raw_state"] = None  # фильтры состояний читают его, а не FSMContext
    return await handler(event, data)


router.message.outer_middleware(track)
router.callback_query.outer_middleware(track)


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


# Команды


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
    user_id = message.from_user.id
    prefs = store.get(user_id)
    await message.answer(
        ui.settings_text(prefs, store.used_today(user_id)),
        reply_markup=ui.settings_keyboard(prefs),
    )


@router.message(Command("new"))
async def new_dialog(message: Message, store: Store) -> None:
    store.forget(message.from_user.id)
    await message.answer(ui.NEW_DIALOG_DONE)


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(ui.HELP)


# Экраны


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
    user_id = call.from_user.id
    prefs = store.get(user_id)
    await show(
        call, ui.settings_text(prefs, store.used_today(user_id)), ui.settings_keyboard(prefs)
    )
    await call.answer(ui.SETTINGS)


@router.callback_query(ui.Nav.filter(F.to == "picker"))
async def nav_picker(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.picker_text(), ui.picker_keyboard(prefs))
    await call.answer("⛭ Основная модель")


@router.callback_query(ui.Nav.filter(F.to == "role"))
async def nav_role(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.role_text(prefs), ui.role_keyboard(prefs))
    await call.answer("⛭ Роль")


@router.callback_query(ui.Nav.filter(F.to == "notify"))
async def nav_notify(call: CallbackQuery, store: Store) -> None:
    prefs = store.get(call.from_user.id)
    await show(call, ui.notify_text(), ui.notify_keyboard(prefs))
    await call.answer("⛭ Уведомления")


@router.callback_query(ui.Card.filter())
async def card(call: CallbackQuery, callback_data: ui.Card, store: Store) -> None:
    model = MODELS.get(callback_data.model_id)
    if model is None:
        await call.answer("Модель больше недоступна", show_alert=True)
        return
    prefs = store.get(call.from_user.id)
    await show(call, ui.model_card(model, prefs), ui.card_keyboard(model, prefs, callback_data.src))
    await call.answer(model.label)


# Настройки


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
        text = ui.settings_text(prefs, store.used_today(user_id))
        markup = ui.settings_keyboard(prefs)
    else:
        text = ui.model_card(model, prefs)
        markup = ui.card_keyboard(model, prefs, callback_data.src)
    await show(call, text, markup)
    await call.answer(f"✓ Основная модель · {model.name}")


@router.callback_query(ui.Toggle.filter(F.key.in_(ui.TOGGLES)))
async def toggle(call: CallbackQuery, callback_data: ui.Toggle, store: Store) -> None:
    user_id, key = call.from_user.id, callback_data.key
    prefs = store.get(user_id)
    if key == "lang":
        value = next_value(LANGS, prefs.lang)
    elif key == "detail":
        value = next_value(DETAILS, prefs.detail)
    else:
        value = not getattr(prefs, key)
    prefs = store.update(user_id, **{key: value})
    if key.startswith("notify_"):
        await show(call, ui.notify_text(), ui.notify_keyboard(prefs))
    else:
        text = ui.settings_text(prefs, store.used_today(user_id))
        await show(call, text, ui.settings_keyboard(prefs))
    await call.answer(ui.toggle_note(key, prefs))


@router.callback_query(ui.SetRole.filter(F.key == CUSTOM_ROLE))
async def custom_role(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RoleInput.text)
    await show(call, ui.role_input_text(), ui.role_input_keyboard())
    await call.answer("✎ Своя роль")


@router.callback_query(ui.SetRole.filter(F.key.in_(ROLES)))
async def set_role(call: CallbackQuery, callback_data: ui.SetRole, store: Store) -> None:
    prefs = store.update(call.from_user.id, role=callback_data.key)
    await show(call, ui.role_text(prefs), ui.role_keyboard(prefs))
    await call.answer(f"✓ Роль · {prefs.role_title}")


@router.message(RoleInput.text, F.text)
async def role_input(message: Message, state: FSMContext, store: Store) -> None:
    text = message.text.strip()
    if len(text) > ROLE_MAX_LENGTH:
        await message.answer(ui.role_too_long(len(text)), reply_markup=ui.role_input_keyboard())
        return
    await state.clear()
    prefs = store.update(message.from_user.id, role=CUSTOM_ROLE, role_text=text)
    await message.answer(
        ui.role_text(prefs, note="✓ Своя роль сохранена"), reply_markup=ui.role_keyboard(prefs)
    )


@router.message(RoleInput.text)
async def role_input_not_text(message: Message) -> None:
    await message.answer(ui.ROLE_ONLY_TEXT, reply_markup=ui.role_input_keyboard())


# Диалог с моделью


@router.callback_query(ui.NewDialog.filter())
async def new_dialog_button(call: CallbackQuery, store: Store) -> None:
    store.forget(call.from_user.id)
    if isinstance(call.message, Message):
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass  # кнопку уже убрали — не важно
        await call.message.answer(ui.NEW_DIALOG_DONE)
    await call.answer("↺ Новый диалог")


@router.message(F.text, ~F.text.startswith("/"))
async def chat(message: Message, store: Store) -> None:
    user_id = message.from_user.id
    if store.used_today(user_id) >= answers.TEST_DAILY_LIMIT:
        await message.answer(answers.limit_reached())
        return
    prefs = store.get(user_id)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await asyncio.sleep(answers.TYPING_SECONDS)
    history = store.history(user_id) if prefs.memory else []
    reply = answers.compose(MODELS[prefs.model], prefs, message.text, history)
    before, after = store.spend(user_id, reply.total)
    if prefs.memory:
        store.remember(
            user_id,
            Turn("user", message.text, reply.question_tokens),
            Turn("bot", answers.plain_text(reply.html), reply.output_tokens),
        )
    text = reply.html
    if prefs.show_usage:
        text += "\n\n" + answers.usage_line(reply, after)
    await message.answer(text, reply_markup=ui.answer_keyboard(prefs))
    if prefs.notify_limit:
        for notice in answers.limit_notices(before, after):
            await message.answer(notice)


@router.message()
async def fallback(message: Message) -> None:
    text = ui.ONLY_TEXT if not message.text else "Выберите раздел ↓"
    await message.answer(text, reply_markup=ui.main_keyboard())
