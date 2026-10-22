"""Бот нейросетей (тестовый режим: модели отвечают заготовками).

Запуск: python -m models_bot. Токен — BOT_TOKEN в models_bot/.env.
Зависимостей нет: только стандартная библиотека Python 3.11+.
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

from models_bot import ui
from models_bot.handlers import Bot
from models_bot.notify import announce_new_models
from models_bot.store import Store
from models_bot.tg import Api, Serial, poll


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


async def main() -> None:
    load_env(Path(__file__).with_name(".env"))
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан: укажите его в models_bot/.env")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    # На сервере systemd выдаёт каталог /var/lib/models-bot (StateDirectory в юните)
    data_dir = Path(os.environ.get("STATE_DIRECTORY") or Path(__file__).with_name("data"))
    store = Store(data_dir / "settings.db")
    api = Api(token)
    me = await api("getMe")
    await api("setMyCommands", commands=ui.COMMANDS)
    # Вебхук снимаем, но апдейты не выбрасываем: сообщения, пришедшие во время
    # перезапуска, бот обработает, а не потеряет
    await api("deleteWebhook", drop_pending_updates=False)
    logging.info("Бот @%s запущен", me["username"])

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: остановка по Ctrl+C
    # Рассылка о новых моделях идёт параллельно, бот отвечает сразу
    announce = asyncio.create_task(announce_new_models(api, store))
    try:
        await poll(api, Serial(Bot(api, store).handle), stop)
    finally:
        announce.cancel()
        api.close()
        logging.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
