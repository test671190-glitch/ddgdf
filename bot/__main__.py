import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import BotCommand, User
from aiohttp import web

from bot.config import get_settings
from bot.db import Database
from bot.gateway import Gateway
from bot.handlers import get_routers
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.user import UserMiddleware
from bot.services.crypto import CryptoPayments, crypto_loop
from bot.services.cryptopay import CryptoPayClient
from bot.services.expiry import expiry_loop
from bot.utils import notify_admins
from bot.web.server import WebApp

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="tariffs", description="Тарифы и оплата"),
    BotCommand(command="profile", description="Моя подписка и API-ключ"),
    BotCommand(command="models", description="Список моделей"),
    BotCommand(command="api", description="Как подключить API"),
    BotCommand(command="faq", description="Частые вопросы"),
    BotCommand(command="paysupport", description="Помощь с оплатой"),
    BotCommand(command="terms", description="Условия"),
]


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db = Database(settings.db_path, hide_keys=settings.uses_gateway)
    await db.connect()

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        session=AiohttpSession(proxy=settings.proxy) if settings.proxy else None,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    crypto_client = (
        CryptoPayClient(settings.crypto_pay_token.get_secret_value(), settings.crypto_pay_url)
        if settings.crypto_pay_token
        else None
    )
    crypto = CryptoPayments(crypto_client, db, bot, settings) if crypto_client else None

    dp = Dispatcher(settings=settings, db=db, crypto=crypto)
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(UserMiddleware())
    # Бот работает только в личке: в группах он не должен отвечать на каждое сообщение
    dp.message.filter(F.chat.type == ChatType.PRIVATE)
    dp.include_routers(*get_routers())

    tasks: list[asyncio.Task] = []
    runners: list[web.AppRunner] = []

    async def serve(app: web.Application, host: str, port: int) -> None:
        runner = web.AppRunner(
            app,
            access_log=None,
            # клиент отключился — отменяем и его запрос к провайдеру, а не держим соединение
            handler_cancellation=True,
            # при перезапуске новый процесс не стартует, пока старый ждёт открытые потоки
            shutdown_timeout=10,
        )
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        runners.append(runner)

    try:
        # Эндпоинт поднимаем раньше связи с Telegram: если api.telegram.org недоступен,
        # оплатившие клиенты всё равно работают с моделями
        if settings.uses_gateway:

            async def alert(text: str) -> None:
                await notify_admins(bot, settings, text)

            gateway = Gateway(db, settings, alert=alert)
            await serve(gateway.build(), settings.gateway_host, settings.gateway_port)
            logging.info(
                "Эндпоинт %s (локально %s:%s)",
                settings.client_api_url,
                settings.gateway_host,
                settings.gateway_port,
            )

        me = await connect_telegram(bot)
        logging.info("Бот @%s запущен", me.username)

        tasks.append(asyncio.create_task(expiry_loop(bot, db, settings)))
        if crypto:
            tasks.append(asyncio.create_task(crypto_loop(crypto)))
            logging.info("Оплата криптой через Crypto Pay включена")
        if settings.web_enabled:
            await serve(
                WebApp(db, settings, crypto, me.username).build(),
                settings.web_host,
                settings.web_port,
            )
            logging.info("Сайт: http://%s:%s", settings.web_host, settings.web_port)

        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)
        for runner in runners:
            await runner.cleanup()
        if crypto_client:
            await crypto_client.close()
        await bot.session.close()
        await db.close()


async def connect_telegram(bot: Bot) -> User:
    """Проверяет токен и готовит бота. Пока Telegram недоступен, повторяет попытки,
    а не завершает процесс: вместе с ним остановился бы и эндпоинт."""
    delay = 5
    while True:
        try:
            me = await bot.get_me()
            await bot.set_my_commands(COMMANDS)
            # Не сбрасываем накопившиеся апдейты: среди них могут быть оплаты
            await bot.delete_webhook(drop_pending_updates=False)
            return me
        except TelegramRetryAfter as e:
            wait = e.retry_after
            logging.warning("Telegram просит подождать %s с", wait)
        except TelegramUnauthorizedError:
            wait = 300
            logging.critical("Telegram не принял BOT_TOKEN — проверьте токен в .env")
        except (TelegramNetworkError, TelegramServerError) as e:
            wait = delay
            delay = min(delay * 2, 300)
            logging.critical(
                "Нет связи с api.telegram.org (%s). Включите VPN или укажите PROXY в .env. "
                "Повтор через %s с",
                e,
                wait,
            )
        await asyncio.sleep(wait)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
