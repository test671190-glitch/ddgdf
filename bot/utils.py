import asyncio
import logging
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from bot.config import Settings

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER = 60  # дольше не ждём: обработчик и рассылка не должны висеть минутами
NETWORK_RETRY_DELAY = 3
# Уведомления, которые должны остаться в истории чата: квитанция об оплате с ключом, выдача
# ключа, ответ поддержки. Кнопка под ними открывает раздел новым сообщением, а не вместо них
KEEP_IN_CHAT = (
    "🎉 Оплата прошла!",
    "Вам выдан доступ",
    "Ваш API-ключ для тарифа",
    "Ответ поддержки",
)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") + " UTC"


async def show(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Показывает раздел: редактирует текущее сообщение или, если нельзя, шлёт новое."""
    message = callback.message
    sent = False
    try:
        if (
            isinstance(message, Message)
            and message.text is not None
            and not message.text.startswith(KEEP_IN_CHAT)
        ):
            try:
                await message.edit_text(text, reply_markup=markup)
                sent = True
            except TelegramBadRequest as e:
                sent = "message is not modified" in str(e)
        if not sent:
            await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
    finally:
        # даже при сбое Telegram отвечаем на нажатие, иначе кнопка крутится у клиента
        await safe_answer(callback)


async def safe_answer(callback: CallbackQuery, text: str | None = None, **kwargs) -> None:
    """Отвечает на нажатие кнопки. Устаревший запрос (например, после простоя бота) не ошибка."""
    try:
        await callback.answer(text, **kwargs)
    except TelegramBadRequest as e:
        logger.debug("Не удалось ответить на callback: %s", e)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as e:
            # Лимит Telegram на частоту сообщений (рассылка напоминаний, выдача ключей
            # ожидающим): ждём, сколько он просит, и пробуем ещё раз, а не теряем сообщение
            if attempt or e.retry_after > MAX_RETRY_AFTER:
                logger.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
                return False
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, TelegramServerError) as e:
            # Временный сбой связи с Telegram: сообщение с ключом после оплаты иначе
            # терялось бы. Лучше редкий дубль, чем клиент без ключа
            if attempt:
                logger.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
                return False
            await asyncio.sleep(NETWORK_RETRY_DELAY)
        except TelegramAPIError as e:
            logger.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
            return False
    return False


async def notify_admins(bot: Bot, settings: Settings, text: str, **kwargs) -> None:
    if not settings.admin_ids:
        # без текста: в уведомлениях бывают ключи провайдера, а журнал читают не только админы
        logger.warning("ADMIN_IDS не заданы, уведомление администраторам не отправлено")
    for admin_id in settings.admin_ids:
        await safe_send(bot, admin_id, text, **kwargs)


def user_label(user: User) -> str:
    return f"@{escape(user.username)}" if user.username else escape(user.full_name)


def mask_key(key: str) -> str:
    """Хвост ключа: по нему ключ узнают и ищут, но не могут им воспользоваться."""
    return "…" + key[-6:] if len(key) >= 12 else "…"
