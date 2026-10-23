"""Бот нейросетей (тестовый режим: модели отвечают заготовками).

Запуск: python -m models_bot. Токен — BOT_TOKEN в models_bot/.env.
Зависимостей нет: только стандартная библиотека Python 3.11+.
"""

import os
import threading

from models_bot import log, ui
from models_bot.handlers import Bot
from models_bot.notify import announce_new_models
from models_bot.store import Store
from models_bot.tg import Api, Serial, poll

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env(os.path.join(HERE, ".env"))
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан: укажите его в models_bot/.env")
    # На сервере systemd выдаёт каталог /var/lib/models-bot (StateDirectory в юните)
    data_dir = os.environ.get("STATE_DIRECTORY") or os.path.join(HERE, "data")
    store = Store(os.path.join(data_dir, "settings.db"))
    api = Api(token)
    me = api("getMe")
    api("setMyCommands", commands=ui.COMMANDS)
    # Вебхук снимаем, но апдейты не выбрасываем: сообщения, пришедшие во время
    # перезапуска, бот обработает, а не потеряет
    api("deleteWebhook", drop_pending_updates=False)
    log.info("Бот @%s запущен", me["username"])
    # Рассылка о новых моделях идёт параллельно, бот отвечает сразу
    threading.Thread(
        target=announce_new_models, args=(api, store), name="announce", daemon=True
    ).start()
    try:
        poll(api, Serial(Bot(api, store, me["username"]).handle), threading.Event())
    finally:
        api.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    main()
