from aiogram import F, Router
from aiogram.enums import ContentType
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.keyboards.inline import main_menu_for
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
async def unknown_message(message: Message, db: Database, settings: Settings) -> None:
    sub = await db.get_subscription(message.from_user.id)
    if message.from_user.id in settings.admin_ids:
        # чаще всего это ответ клиенту, набранный без кнопки «Ответить» и без реплая:
        # админ должен понять, что он никуда не ушёл
        await message.answer(texts.support_admin_hint(), reply_markup=main_menu_for(sub))
        return
    await message.answer(
        "Не понял запрос. Выберите раздел в меню:", reply_markup=main_menu_for(sub)
    )


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    await safe_answer(callback, "Кнопка устарела, откройте /start", show_alert=True)
