"""Каркас бота: меню топ-нейросетей.

Запуск: python -m models_bot (токен — BOT_TOKEN в models_bot/.env).
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from models_bot import ui
from models_bot.handlers import router
from models_bot.notify import announce_new_models
from models_bot.store import Store


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
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(store=store)
    dp.include_router(router)
    await bot.set_my_commands(ui.COMMANDS)
    await bot.delete_webhook(drop_pending_updates=True)
    # Рассылка о новых моделях идёт параллельно, бот отвечает сразу
    announce = asyncio.create_task(announce_new_models(bot, store))
    try:
        await dp.start_polling(bot)
    finally:
        announce.cancel()


if __name__ == "__main__":
    asyncio.run(main())
