"""Поддержка прямо в боте.

Клиент нажимает «Написать в поддержку» и отправляет сообщение — оно приходит всем админам
вместе с карточкой клиента. Админ отвечает на него реплаем, и бот пересылает ответ клиенту.
Какое сообщение у админа к какому клиенту относится, хранится в базе (support_messages).
"""

import logging
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import IsAdmin
from bot.handlers.fallback import USER_CONTENT
from bot.keyboards.inline import (
    Menu,
    SupportWrite,
    main_menu,
    support_answer_keyboard,
    support_cancel_keyboard,
    support_keyboard,
)
from bot.utils import safe_answer, show, user_label

logger = logging.getLogger(__name__)
router = Router(name="support")

# Сколько ждём сообщение после нажатия «Написать в поддержку». Клиент мог уйти в другие
# разделы и забыть: его случайное сообщение через час не должно улететь админам
WAIT_SECONDS = 30 * 60


class SupportForm(StatesGroup):
    waiting = State()


@router.message(Command("support"))
async def cmd_support(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.support(settings), reply_markup=support_keyboard(settings.support_username)
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.support(settings, payment=True),
        reply_markup=support_keyboard(settings.support_username),
    )


@router.callback_query(Menu.filter(F.section == "support"))
async def cb_support(callback: CallbackQuery, settings: Settings) -> None:
    await show(callback, texts.support(settings), support_keyboard(settings.support_username))


@router.callback_query(SupportWrite.filter(F.cancel))
async def cb_support_cancel(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await show(callback, texts.support(settings), support_keyboard(settings.support_username))


@router.callback_query(SupportWrite.filter())
async def cb_support_write(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not settings.admin_ids:
        await safe_answer(callback, texts.support_unavailable(), show_alert=True)
        return
    await state.set_state(SupportForm.waiting)
    await state.update_data(since=time.time())
    # Новым сообщением, а не правкой: кнопка «Ответить» стоит под ответом поддержки,
    # и правка стёрла бы сам ответ
    await callback.bot.send_message(
        callback.from_user.id, texts.support_prompt(), reply_markup=support_cancel_keyboard()
    )
    await safe_answer(callback)


async def support_client(message: Message, db: Database) -> dict | bool:
    """Фильтр: админ ответил реплаем на обращение — возвращает клиента."""
    reply = message.reply_to_message
    if reply is None:
        return False
    client_id = await db.support_user(message.chat.id, reply.message_id)
    return {"client_id": client_id} if client_id is not None else False


@router.message(IsAdmin(), support_client)
async def admin_answer(message: Message, bot: Bot, client_id: int) -> None:
    try:
        if message.text:
            await bot.send_message(
                client_id,
                texts.support_answer(message.text),
                reply_markup=support_answer_keyboard(),
            )
        else:
            # фото, файл, голосовое — копируем как есть, подпись сохраняется
            await bot.send_message(client_id, texts.support_answer(None))
            await message.copy_to(client_id, reply_markup=support_answer_keyboard())
    except TelegramAPIError as e:
        await message.reply(f"❌ Ответ не доставлен: {escape(str(e))}")
        return
    await message.reply("✅ Ответ отправлен клиенту")


@router.message(
    SupportForm.waiting,
    F.content_type.in_(USER_CONTENT),
    ~F.text.startswith("/"),  # неизвестную команду не шлём админам как вопрос
)
async def client_message(
    message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings
) -> None:
    data = await state.get_data()
    await state.clear()
    user = message.from_user
    sub = await db.get_subscription(user.id)
    has_sub = sub is not None and sub.is_active
    if time.time() - data.get("since", 0) > WAIT_SECONDS:
        await message.answer(
            "Не понял запрос. Если это вопрос в поддержку, нажмите кнопку ниже.",
            reply_markup=support_keyboard(settings.support_username),
        )
        return

    ticket = texts.support_ticket(user.id, user_label(user), sub)
    delivered = 0
    for admin_id in settings.admin_ids:
        try:
            card = await bot.send_message(admin_id, ticket)
            copy = await message.copy_to(admin_id)
        except TelegramAPIError as e:
            logger.warning("Обращение %s не доставлено админу %s: %s", user.id, admin_id, e)
            continue
        # отвечать можно реплаем и на карточку, и на само сообщение клиента
        await db.add_support_message(admin_id, card.message_id, user.id)
        await db.add_support_message(admin_id, copy.message_id, user.id)
        delivered += 1

    if delivered:
        await message.answer(texts.support_sent(), reply_markup=main_menu(has_sub))
    else:
        await message.answer(
            texts.support_unavailable(), reply_markup=support_keyboard(settings.support_username)
        )
