from aiogram.filters.callback_data import CallbackData
from aiogram.types import CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.catalog import FAQ, FAQ_BUTTONS, MODEL_CATEGORIES, TARIFFS_ON_SALE, Tariff

# Цвета кнопок Telegram: primary — синяя, success — зелёная, danger — красная
PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"

BACK = "← В меню"


class Menu(CallbackData, prefix="menu"):
    section: str  # main | tariffs | models | profile | api | faq | site | support


class ModelsPage(CallbackData, prefix="models"):
    category: str


class TariffPage(CallbackData, prefix="tariff"):
    code: str


class BuyTariff(CallbackData, prefix="buy"):
    code: str


class CryptoPay(CallbackData, prefix="cpay"):
    code: str


class CryptoCheck(CallbackData, prefix="ccheck"):
    order_id: int


class FaqItem(CallbackData, prefix="faq"):
    index: int


class WebLogin(CallbackData, prefix="wlogin"):
    token: str


class RotateKey(CallbackData, prefix="rotate"):
    confirm: bool


class SupportWrite(CallbackData, prefix="support"):
    """Начать или отменить сообщение в поддержку."""

    cancel: bool = False


def rotate_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, перевыпустить", callback_data=RotateKey(confirm=True), style=DANGER)
    builder.button(text="Отмена", callback_data=Menu(section="profile"))
    builder.adjust(1)
    return builder.as_markup()


def web_login_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Да, это я вхожу на сайт", callback_data=WebLogin(token=token), style=SUCCESS
    )
    builder.button(text="Это не я", callback_data=Menu(section="main"), style=DANGER)
    builder.adjust(1)
    return builder.as_markup()


def main_menu(has_subscription: bool = False, renewable: bool = True) -> InlineKeyboardMarkup:
    """renewable=False — тариф клиента снят с продажи: продлить его нельзя, только сменить."""
    if has_subscription:
        label = "Продлить подписку" if renewable else "Сменить тариф"
    else:
        label = "Купить подписку"
    builder = InlineKeyboardBuilder()
    builder.button(text=label, callback_data=Menu(section="tariffs"), style=PRIMARY)
    builder.button(text="Профиль", callback_data=Menu(section="profile"))
    builder.button(text="Подключение", callback_data=Menu(section="api"))
    builder.button(text="Модели", callback_data=Menu(section="models"))
    builder.button(text="Вопросы", callback_data=Menu(section="faq"))
    builder.button(text="Сайт", callback_data=Menu(section="site"))
    builder.button(text="Поддержка", callback_data=Menu(section="support"))
    builder.adjust(1, 2, 2, 2)
    return builder.as_markup()


def back(section: str = "main", text: str = BACK) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=Menu(section=section))
    return builder.as_markup()


def tariffs_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in TARIFFS_ON_SALE.values():
        builder.button(
            text=f"{tariff.title} — ${tariff.price_usd}",
            callback_data=TariffPage(code=tariff.code),
            style=SUCCESS,
        )
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def tariff_keyboard(tariff: Tariff, stars: int, crypto_enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # «⭐» оставлен: Telegram рисует на его месте иконку Stars
    builder.button(
        text=f"Оплатить ⭐ {stars}",
        callback_data=BuyTariff(code=tariff.code),
        style=PRIMARY,
    )
    if crypto_enabled:
        builder.button(
            text=f"Оплатить криптой · ${tariff.price_usd}",
            callback_data=CryptoPay(code=tariff.code),
            style=SUCCESS,
        )
    builder.button(text="← Все тарифы", callback_data=Menu(section="tariffs"))
    builder.adjust(1)
    return builder.as_markup()


def crypto_pay_keyboard(tariff: Tariff, order_id: int, pay_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Оплатить ${tariff.price_usd}", url=pay_url, style=SUCCESS)
    builder.button(text="Проверить оплату", callback_data=CryptoCheck(order_id=order_id))
    builder.button(text="← Другой способ", callback_data=TariffPage(code=tariff.code))
    builder.adjust(1)
    return builder.as_markup()


def crypto_expired_keyboard(tariff_code: str) -> InlineKeyboardMarkup:
    """Счёт истёк, клиент ещё ничего не купил: предлагаем новый счёт, а не «Продлить»."""
    builder = InlineKeyboardBuilder()
    if tariff_code in TARIFFS_ON_SALE:
        builder.button(text="Новый счёт", callback_data=CryptoPay(code=tariff_code), style=PRIMARY)
    builder.button(text="← Тарифы", callback_data=Menu(section="tariffs"))
    builder.adjust(1)
    return builder.as_markup()


def renew_keyboard(tariff_code: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if tariff_code in TARIFFS_ON_SALE:
        builder.button(text="Продлить", callback_data=TariffPage(code=tariff_code), style=PRIMARY)
    builder.button(text="Тарифы", callback_data=Menu(section="tariffs"))
    builder.adjust(1)
    return builder.as_markup()


def profile_keyboard(
    has_subscription: bool, key: str | None = None, can_rotate: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if key:
        builder.button(text="Скопировать ключ", copy_text=CopyTextButton(text=key))
    if can_rotate:
        builder.button(text="Перевыпустить ключ", callback_data=RotateKey(confirm=False))
    builder.button(
        text="Продлить или сменить тариф" if has_subscription else "Выбрать тариф",
        callback_data=Menu(section="tariffs"),
        style=PRIMARY,
    )
    builder.button(text="Как подключить", callback_data=Menu(section="api"))
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def models_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, (title, models) in MODEL_CATEGORIES.items():
        builder.button(text=f"{title} · {len(models)}", callback_data=ModelsPage(category=code))
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def models_list_keyboard(current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    others = [(c, t) for c, (t, _) in MODEL_CATEGORIES.items() if c != current]
    for code, title in others:
        builder.button(text=title, callback_data=ModelsPage(category=code))
    builder.button(text="← Назад", callback_data=Menu(section="models"))
    builder.adjust(len(others), 1)
    return builder.as_markup()


def faq_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, (question, _) in enumerate(FAQ):
        builder.button(text=FAQ_BUTTONS.get(question, question), callback_data=FaqItem(index=i))
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def api_keyboard(docs_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if docs_url:
        builder.button(text="Документация API", url=docs_url, style=PRIMARY)
    builder.button(text="ID моделей", callback_data=Menu(section="models"))
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1, 2)
    return builder.as_markup()


def site_keyboard(site_url: str, docs_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if site_url:
        builder.button(text="Открыть сайт", url=site_url, style=PRIMARY)
    if docs_url:
        builder.button(text="Документация API", url=docs_url)
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def support_keyboard(support_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Написать в поддержку", callback_data=SupportWrite(), style=PRIMARY)
    if support_username:
        builder.button(text=f"@{support_username}", url=f"https://t.me/{support_username}")
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(1)
    return builder.as_markup()


def support_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data=SupportWrite(cancel=True))
    return builder.as_markup()


def support_answer_keyboard() -> InlineKeyboardMarkup:
    """Под ответом поддержки: продолжить переписку или вернуться в меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Ответить", callback_data=SupportWrite(), style=PRIMARY)
    builder.button(text=BACK, callback_data=Menu(section="main"))
    builder.adjust(2)
    return builder.as_markup()
