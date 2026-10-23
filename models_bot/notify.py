"""Уведомления о новых моделях: при запуске бот сравнивает каталог с прошлым запуском
и пишет тем, у кого включено «Новые модели». Работает в отдельном потоке."""

import json
import time

from models_bot import log, ui
from models_bot.catalog import MODELS
from models_bot.store import Store
from models_bot.tg import NETWORK_ERRORS, Api, TelegramError

KNOWN_MODELS = "known_models"
SEND_PAUSE = 0.05  # не упираться в лимит Telegram на рассылку


def announce_new_models(api: Api, store: Store) -> int:
    """Возвращает, скольким пользователям ушло уведомление."""
    known = store.meta(KNOWN_MODELS)
    # Сначала запоминаем каталог: при перезапусках по кругу уведомление не повторится
    store.set_meta(KNOWN_MODELS, json.dumps(sorted(MODELS)))
    if known is None:
        return 0  # первый запуск: всё, что есть в каталоге, — не новинки
    known_ids = set(json.loads(known))
    new = [m for model_id, m in MODELS.items() if model_id not in known_ids]
    if not new:
        return 0
    sent = 0
    for user_id in store.users_with("notify_models"):
        try:
            api(
                "sendMessage",
                chat_id=user_id,
                text=ui.new_models_text(new),
                parse_mode="HTML",
                reply_markup=ui.new_models_keyboard(new),
            )
            sent += 1
        except TelegramError as e:
            if e.code == 403:  # бота заблокировали: больше не пишем, пока сам не напишет
                store.update(user_id, blocked=True)
            log.info("Уведомление о новых моделях не дошло до %s: %s", user_id, e)
        except NETWORK_ERRORS as e:
            log.info("Уведомление о новых моделях не дошло до %s: %r", user_id, e)
        time.sleep(SEND_PAUSE)
    log.info("Новые модели %s: уведомлено %s", [m.id for m in new], sent)
    return sent
