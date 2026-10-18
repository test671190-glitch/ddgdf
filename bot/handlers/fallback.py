from aiogram import Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import main_menu
from bot.utils import safe_answer

router = Router(name="fallback")


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer("Не понял запрос. Выберите раздел в меню:", reply_markup=main_menu())


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await safe_answer(callback, "Кнопка устарела, откройте /start", show_alert=True)
