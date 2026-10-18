import logging
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from bot.config import Settings

logger = logging.getLogger(__name__)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") + " UTC"


async def show(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Показывает раздел: редактирует текущее сообщение или, если нельзя, шлёт новое."""
    message = callback.message
    sent = False
    if isinstance(message, Message) and message.text is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            sent = True
        except TelegramBadRequest as e:
            sent = "message is not modified" in str(e)
    if not sent:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
    await safe_answer(callback)


async def safe_answer(callback: CallbackQuery, text: str | None = None, **kwargs) -> None:
    """Отвечает на нажатие кнопки. Устаревший запрос (например, после простоя бота) не ошибка."""
    try:
        await callback.answer(text, **kwargs)
    except TelegramBadRequest as e:
        logger.debug("Не удалось ответить на callback: %s", e)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as e:
        logger.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
        return False


async def notify_admins(bot: Bot, settings: Settings, text: str, **kwargs) -> None:
    if not settings.admin_ids:
        logger.warning("ADMIN_IDS не заданы, уведомление потеряно: %s", text)
    for admin_id in settings.admin_ids:
        await safe_send(bot, admin_id, text, **kwargs)


def user_label(user: User) -> str:
    return f"@{escape(user.username)}" if user.username else escape(user.full_name)
