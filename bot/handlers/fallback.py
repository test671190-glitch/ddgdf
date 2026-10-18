from aiogram import F, Router
from aiogram.enums import ContentType
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.keyboards.inline import main_menu
from bot.utils import safe_answer

router = Router(name="fallback")

# Сообщения, которые написал человек. Служебные (таймер автоудаления, разрешение писать
# и т. п.) молча пропускаем: ответ «Не понял запрос» на них сбивает с толку
USER_CONTENT = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.DOCUMENT,
    ContentType.STICKER,
    ContentType.VOICE,
    ContentType.VIDEO,
    ContentType.VIDEO_NOTE,
    ContentType.AUDIO,
    ContentType.ANIMATION,
    ContentType.CONTACT,
    ContentType.LOCATION,
}


@router.message(F.content_type.in_(USER_CONTENT))
async def unknown_message(message: Message, db: Database) -> None:
    sub = await db.get_subscription(message.from_user.id)
    has_sub = sub is not None and sub.is_active
    await message.answer(
        "Не понял запрос. Выберите раздел в меню:", reply_markup=main_menu(has_sub)
    )


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await safe_answer(callback, "Кнопка устарела, откройте /start", show_alert=True)
