from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from models_bot import ui
from models_bot.catalog import MODELS

router = Router()


@router.message(CommandStart())
@router.message(Command("models"))
async def start(message: Message) -> None:
    await message.answer(ui.welcome(message.from_user.first_name), reply_markup=ui.main_keyboard())


@router.message(Command("other"))
async def other(message: Message) -> None:
    await message.answer(ui.other_title(0, ui.pages_count()), reply_markup=ui.other_keyboard(0))


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(ui.HELP)


@router.callback_query(ui.Nav.filter(F.to == "main"))
async def nav_main(call: CallbackQuery) -> None:
    await call.message.edit_text(
        ui.welcome(call.from_user.first_name), reply_markup=ui.main_keyboard()
    )
    await call.answer("✦ Главное меню")


@router.callback_query(ui.Nav.filter(F.to == "other"))
async def nav_other(call: CallbackQuery, callback_data: ui.Nav) -> None:
    page = min(max(callback_data.page, 0), ui.pages_count() - 1)
    await call.message.edit_text(
        ui.other_title(page, ui.pages_count()), reply_markup=ui.other_keyboard(page)
    )
    await call.answer(f"◇ Страница {page + 1}")


@router.callback_query(ui.Pick.filter())
async def pick(call: CallbackQuery, callback_data: ui.Pick) -> None:
    model = MODELS.get(callback_data.model_id)
    if model is None:
        await call.answer("✧ Модель не найдена", show_alert=True)
        return
    await call.message.edit_text(
        ui.model_card(model), reply_markup=ui.model_keyboard(callback_data.page)
    )
    await call.answer(f"{ui.mark(model)} {model.name}")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("➜ Выберите модель в меню", reply_markup=ui.main_keyboard())
