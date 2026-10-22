"""Уведомления о новых моделях: при запуске бот сравнивает каталог с прошлым запуском
и пишет тем, у кого включено «Новые модели»."""

import asyncio
import json
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from models_bot import ui
from models_bot.catalog import MODELS
from models_bot.store import Store

KNOWN_MODELS = "known_models"
SEND_PAUSE = 0.05  # не упираться в лимит Telegram на рассылку


async def announce_new_models(bot: Bot, store: Store) -> int:
    """Возвращает, скольким пользователям ушло уведомление."""
    known = store.meta(KNOWN_MODELS)
    # Сначала запоминаем каталог: при перезапусках по кругу уведомление не повторится
    store.set_meta(KNOWN_MODELS, json.dumps(sorted(MODELS)))
    if known is None:
        return 0  # первый запуск: всё, что есть в каталоге, — не новинки
    new = [m for model_id, m in MODELS.items() if model_id not in set(json.loads(known))]
    if not new:
        return 0
    sent = 0
    for user_id in store.users_with("notify_models"):
        try:
            await bot.send_message(
                user_id, ui.new_models_text(new), reply_markup=ui.new_models_keyboard(new)
            )
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logging.info("Уведомление о новых моделях не дошло до %s: %s", user_id, e)
        await asyncio.sleep(SEND_PAUSE)
    logging.info("Новые модели %s: уведомлено %s", [m.id for m in new], sent)
    return sent
