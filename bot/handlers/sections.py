"""Информационные разделы: меню, модели, профиль, API, FAQ, поддержка."""

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from bot import texts
from bot.catalog import FAQ, MODEL_CATEGORIES, TARIFFS_ON_SALE
from bot.config import Settings
from bot.db import Database
from bot.handlers.payments import render_tariff
from bot.keyboards.inline import (
    FaqItem,
    Menu,
    ModelsPage,
    RotateKey,
    WebLogin,
    api_keyboard,
    back,
    faq_keyboard,
    main_menu,
    models_keyboard,
    models_list_keyboard,
    profile_keyboard,
    rotate_keyboard,
    support_keyboard,
    web_login_keyboard,
)
from bot.services.access import shown_key
from bot.services.crypto import CryptoPayments
from bot.utils import safe_answer, show

router = Router(name="sections")

# Токен входа с сайта (secrets.token_urlsafe). /start можно набрать руками, а слишком
# длинный токен или «:» в нём не влезают в callback_data и роняли обработчик
LOGIN_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,43}")


async def render_profile(
    user_id: int, db: Database, settings: Settings
) -> tuple[str, InlineKeyboardMarkup]:
    sub = await db.get_subscription(user_id)
    has_sub = sub is not None and sub.is_active
    upstream = await db.get_active_key(user_id) if has_sub else None
    key = await shown_key(db, settings, user_id, upstream.key if upstream else None)
    return (
        texts.profile(user_id, sub, key, settings),
        profile_keyboard(has_sub, key, can_rotate=settings.uses_gateway and key is not None),
    )


async def render_main(user: User, db: Database) -> tuple[str, InlineKeyboardMarkup]:
    sub = await db.get_subscription(user.id)
    has_sub = sub is not None and sub.is_active
    return texts.welcome(user.first_name, sub), main_menu(has_sub)


# --- главное меню ---


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
    crypto: CryptoPayments | None,
) -> None:
    args = command.args or ""
    # Вход на сайт: t.me/<bot>?start=login_<token>. Подтверждение кнопкой защищает от того,
    # что кто-то пришлёт свою ссылку и войдёт на сайт под чужим аккаунтом.
    if args.startswith("login_") and LOGIN_TOKEN.fullmatch(args[len("login_") :]):
        await message.answer(
            texts.web_login_confirm(), reply_markup=web_login_keyboard(args[len("login_") :])
        )
        return
    # Покупка с сайта: t.me/<bot>?start=buy_<тариф>
    if args.startswith("buy_") and args[len("buy_") :] in TARIFFS_ON_SALE:
        tariff = TARIFFS_ON_SALE[args[len("buy_") :]]
        text, markup = await render_tariff(message.from_user.id, tariff, db, settings, crypto)
        await message.answer(text, reply_markup=markup)
        return
    text, markup = await render_main(message.from_user, db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(WebLogin.filter())
async def cb_web_login(callback: CallbackQuery, callback_data: WebLogin, db: Database) -> None:
    ok = await db.confirm_web_login(callback_data.token, callback.from_user.id)
    text = (
        "<b>Вход подтверждён</b>\n\nВернитесь в браузер — сайт откроется сам."
        if ok
        else "Ссылка для входа устарела. Нажмите «Войти» на сайте ещё раз."
    )
    await show(callback, text, back())


@router.message(Command("help"))
async def cmd_help(message: Message, db: Database) -> None:
    _, markup = await render_main(message.from_user, db)
    await message.answer(texts.HELP, reply_markup=markup)


@router.callback_query(Menu.filter(F.section == "main"))
async def cb_main(callback: CallbackQuery, db: Database) -> None:
    text, markup = await render_main(callback.from_user, db)
    await show(callback, text, markup)


# --- модели ---


@router.message(Command("models"))
async def cmd_models(message: Message) -> None:
    await message.answer(texts.models_overview(), reply_markup=models_keyboard())


@router.callback_query(Menu.filter(F.section == "models"))
async def cb_models(callback: CallbackQuery) -> None:
    await show(callback, texts.models_overview(), models_keyboard())


@router.callback_query(ModelsPage.filter(F.category.in_(MODEL_CATEGORIES)))
async def cb_models_list(callback: CallbackQuery, callback_data: ModelsPage) -> None:
    await show(
        callback,
        texts.models_list(callback_data.category),
        models_list_keyboard(callback_data.category),
    )


# --- профиль ---


@router.message(Command("profile"))
async def cmd_profile(message: Message, db: Database, settings: Settings) -> None:
    text, markup = await render_profile(message.from_user.id, db, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Menu.filter(F.section == "profile"))
async def cb_profile(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    text, markup = await render_profile(callback.from_user.id, db, settings)
    await show(callback, text, markup)


@router.callback_query(RotateKey.filter())
async def cb_rotate_key(
    callback: CallbackQuery, callback_data: RotateKey, db: Database, settings: Settings
) -> None:
    if not settings.uses_gateway:
        await safe_answer(callback, "Перевыпуск ключа недоступен", show_alert=True)
        return
    if not callback_data.confirm:
        await show(callback, texts.rotate_confirm(), rotate_keyboard())
        return
    await db.rotate_client_key(callback.from_user.id)
    text, markup = await render_profile(callback.from_user.id, db, settings)
    await show(callback, "<b>Ключ перевыпущен.</b> Старый больше не работает.\n\n" + text, markup)


# --- API ---


@router.message(Command("api"))
async def cmd_api(message: Message, settings: Settings) -> None:
    await message.answer(texts.api_info(settings), reply_markup=api_keyboard(settings.docs_url))


@router.callback_query(Menu.filter(F.section == "api"))
async def cb_api(callback: CallbackQuery, settings: Settings) -> None:
    await show(callback, texts.api_info(settings), api_keyboard(settings.docs_url))


# --- FAQ ---


@router.message(Command("faq"))
async def cmd_faq(message: Message) -> None:
    await message.answer(texts.faq_overview(), reply_markup=faq_keyboard())


@router.callback_query(Menu.filter(F.section == "faq"))
async def cb_faq(callback: CallbackQuery) -> None:
    await show(callback, texts.faq_overview(), faq_keyboard())


@router.callback_query(FaqItem.filter(F.index.in_(range(len(FAQ)))))
async def cb_faq_item(callback: CallbackQuery, callback_data: FaqItem) -> None:
    question, answer = FAQ[callback_data.index]
    await show(callback, texts.faq_answer(question, answer), back("faq", "← К вопросам"))


# --- поддержка и условия (/paysupport и /terms обязательны для оплаты в Stars) ---


@router.callback_query(Menu.filter(F.section == "support"))
async def cb_support(callback: CallbackQuery, settings: Settings) -> None:
    await show(callback, texts.support(settings), support_keyboard(settings.support_username))


@router.message(Command("support"))
async def cmd_support(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.support(settings),
        reply_markup=support_keyboard(settings.support_username),
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.paysupport(settings),
        reply_markup=support_keyboard(settings.support_username),
    )


@router.message(Command("terms"))
async def cmd_terms(message: Message, settings: Settings) -> None:
    await message.answer(texts.terms(settings), reply_markup=back())
