"""Поддержка прямо в боте.

Клиент нажимает «Написать в поддержку» — открывается обращение. Всё, что он пишет дальше
(текст, скриншоты альбомом, файлы, дописки), уходит всем админам: первым сообщением карточка
клиента, затем копии. Обращение закрывается, когда клиент уходит в любой раздел или команду,
жмёт «Отмена» или молчит 30 минут. Админ отвечает реплаем на карточку или копию — бот
пересылает ответ клиенту. Какое сообщение у админа к какому клиенту относится, хранится
в базе (support_messages).
"""

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from html import escape

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, MessageEntity

from bot import texts
from bot.config import Settings
from bot.db import Database
from bot.filters.admin import IsAdmin
from bot.handlers.fallback import USER_CONTENT
from bot.keyboards.inline import (
    Menu,
    SupportWrite,
    main_menu_for,
    support_answer_keyboard,
    support_cancel_keyboard,
    support_keyboard,
)
from bot.utils import MAX_RETRY_AFTER, safe_answer, safe_send, show, user_label

logger = logging.getLogger(__name__)
router = Router(name="support")

# Обращение закрывается после 30 минут тишины: случайное сообщение через час не уйдёт админам
WAIT_SECONDS = 30 * 60
# Не больше сообщений от одного клиента за час: защита админов от флуда
MAX_MESSAGES_PER_HOUR = 20
# Настоящая команда («/start», «/tariffs@bot»), а не текст вроде «/v1/chat/completions отдаёт 404»
COMMAND = re.compile(r"^/[A-Za-z0-9_]{1,32}(@\w+)?(\s|$)")
ANSWER_HEAD = "Ответ поддержки"
# Сообщения с подписью: заголовок ответа ставим в подпись, одним сообщением
CAPTIONABLE = {
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.DOCUMENT,
    ContentType.AUDIO,
    ContentType.ANIMATION,
    ContentType.VOICE,
}

_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_recent: defaultdict[int, deque[float]] = defaultdict(deque)
_answered_albums: dict[str, float] = {}


class SupportForm(StatesGroup):
    writing = State()


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


async def _with_retry(call):
    """Отправка с одной повторной попыткой при лимите частоты или сбое связи. None — не вышло."""
    for attempt in range(2):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt or e.retry_after > MAX_RETRY_AFTER:
                logger.warning("Поддержка: не отправлено (%s)", e)
                return None
            await asyncio.sleep(e.retry_after)
        except TelegramNetworkError as e:
            if attempt:
                logger.warning("Поддержка: не отправлено (%s)", e)
                return None
            await asyncio.sleep(3)
        except TelegramAPIError as e:
            logger.warning("Поддержка: не отправлено (%s)", e)
            return None
    return None


# --- выход из обращения: любая команда или кнопка раздела ---


@router.message(SupportForm.writing, F.text.regexp(COMMAND))
async def leave_on_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    raise SkipHandler  # команду обработает её обычный обработчик


@router.callback_query(SupportForm.writing, ~SupportWrite.filter())
async def leave_on_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    raise SkipHandler


# --- ответ админа ---


async def support_client(message: Message, db: Database) -> dict | bool:
    """Фильтр: админ ответил реплаем на обращение — возвращает клиента."""
    reply = message.reply_to_message
    if reply is None:
        return False
    client_id = await db.support_user(message.chat.id, reply.message_id)
    return {"client_id": client_id} if client_id is not None else False


def _first_of_album(media_group_id: str | None) -> bool:
    """Альбом приходит отдельными сообщениями: заголовок и «✅» — только на первое."""
    if media_group_id is None:
        return True
    now = time.time()
    for group, seen in list(_answered_albums.items()):
        if now - seen > 600:
            del _answered_albums[group]
    if media_group_id in _answered_albums:
        return False
    _answered_albums[media_group_id] = now
    return True


def _with_head(text: str, entities: list[MessageEntity] | None) -> tuple[str, list[MessageEntity]]:
    """Ставит «Ответ поддержки» перед текстом, сохраняя форматирование и ссылки админа."""
    prefix = f"{ANSWER_HEAD}\n\n" if text else ANSWER_HEAD
    shift = _utf16(prefix)
    shifted = [e.model_copy(update={"offset": e.offset + shift}) for e in entities or []]
    return prefix + text, [
        MessageEntity(type="bold", offset=0, length=_utf16(ANSWER_HEAD))
    ] + shifted


async def _deliver_answer(bot: Bot, message: Message, client_id: int, head: bool) -> None:
    markup = support_answer_keyboard() if head else None
    if message.text is not None:
        text, entities = _with_head(message.text, message.entities)
        if _utf16(text) <= 4096:
            await bot.send_message(
                client_id, text, entities=entities, parse_mode=None, reply_markup=markup
            )
            return
        # длинный ответ: заголовок отдельно, текст копией (лимит 4096 с заголовком не влезал)
        await bot.send_message(client_id, texts.support_answer(None))
        await message.copy_to(client_id, reply_markup=markup)
        return
    if head and message.content_type in CAPTIONABLE:
        caption, entities = _with_head(message.caption or "", message.caption_entities)
        if _utf16(caption) <= 1024:
            await message.copy_to(
                client_id,
                caption=caption,
                caption_entities=entities,
                parse_mode=None,
                reply_markup=markup,
            )
            return
    if head:
        await bot.send_message(client_id, texts.support_answer(None))
    await message.copy_to(client_id, reply_markup=markup)


@router.message(IsAdmin(), support_client)
async def admin_answer(message: Message, bot: Bot, client_id: int) -> None:
    head = _first_of_album(message.media_group_id)
    try:
        await _deliver_answer(bot, message, client_id, head)
    except TelegramAPIError as e:
        await message.reply(f"❌ Ответ не доставлен: {escape(str(e))}")
        return
    if head:
        await message.reply("✅ Ответ отправлен клиенту")


# --- раздел поддержки ---


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


async def _open(state: FSMContext) -> None:
    await state.set_state(SupportForm.writing)
    await state.update_data(since=time.time(), ticket=False, limited=False)


@router.callback_query(SupportWrite.filter())
async def cb_support_write(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not settings.admin_ids:
        await safe_answer(callback, texts.support_unavailable(), show_alert=True)
        return
    await _open(state)
    # Новым сообщением, а не правкой: кнопка «Ответить» стоит под ответом поддержки,
    # и правка стёрла бы сам ответ
    await safe_send(
        callback.bot,
        callback.from_user.id,
        texts.support_prompt(),
        reply_markup=support_cancel_keyboard(),
    )
    await safe_answer(callback)


# --- сообщения клиента ---


async def _forward(
    message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings
) -> None:
    user = message.from_user
    # Альбом — это несколько апдейтов, которые бот обрабатывает одновременно: блокировка,
    # чтобы карточка ушла один раз, а не на каждую картинку
    async with _locks[user.id]:
        now = time.time()
        recent = _recent[user.id]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        data = await state.get_data()
        if len(recent) >= MAX_MESSAGES_PER_HOUR:
            if not data.get("limited"):
                await state.update_data(limited=True)
                await message.answer(texts.support_flood())
            return
        recent.append(now)

        first = not data.get("ticket")
        sub = await db.get_subscription(user.id)
        delivered = 0
        for admin_id in settings.admin_ids:
            if first:
                card = await _with_retry(
                    lambda a=admin_id: bot.send_message(
                        a, texts.support_ticket(user.id, user_label(user), sub)
                    )
                )
                if card is not None:
                    await db.add_support_message(admin_id, card.message_id, user.id)
            copy = await _with_retry(lambda a=admin_id: message.copy_to(a))
            if copy is None:
                continue
            await db.add_support_message(admin_id, copy.message_id, user.id)
            delivered += 1

        if not delivered:
            await message.answer(
                texts.support_unavailable(),
                reply_markup=support_keyboard(settings.support_username),
            )
            return
        await state.update_data(ticket=True, since=now)
        if first:
            await message.answer(texts.support_sent(), reply_markup=main_menu_for(sub))


@router.message(SupportForm.writing, F.content_type.in_(USER_CONTENT))
async def client_message(
    message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings
) -> None:
    data = await state.get_data()
    if time.time() - data.get("since", 0) > WAIT_SECONDS:
        # клиент давно ушёл из поддержки: это обычное сообщение боту
        await state.clear()
        raise SkipHandler
    await _forward(message, state, bot, db, settings)


def replies_to_answer(message: Message) -> bool:
    """Клиент ответил реплаем на «Ответ поддержки» — это продолжение переписки."""
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or not reply.from_user.is_bot:
        return False
    return (reply.text or reply.caption or "").startswith(ANSWER_HEAD)


@router.message(replies_to_answer, F.content_type.in_(USER_CONTENT), ~F.text.regexp(COMMAND))
async def client_reply(
    message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings
) -> None:
    if not settings.admin_ids:
        raise SkipHandler
    await _open(state)
    await _forward(message, state, bot, db, settings)
