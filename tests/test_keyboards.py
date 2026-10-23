from bot.catalog import FAQ, TARIFFS, TARIFFS_ON_SALE
from bot.keyboards.inline import (
    BuyTariff,
    Menu,
    TariffPage,
    faq_keyboard,
    main_menu,
    renew_keyboard,
    tariffs_keyboard,
)


def test_main_menu_sections():
    buttons = [b for row in main_menu().inline_keyboard for b in row]
    sections = {Menu.unpack(b.callback_data).section for b in buttons}
    assert sections == {"tariffs", "models", "profile", "api", "faq", "site", "support"}


def test_tariffs_keyboard_shows_only_tariffs_on_sale():
    buttons = [b for row in tariffs_keyboard().inline_keyboard for b in row]
    codes = {TariffPage.unpack(b.callback_data).code for b in buttons[:-1]}
    assert codes == {"pro", "unlimited"} == set(TARIFFS_ON_SALE)


def test_hidden_tariff_has_no_renew_button():
    texts = [b.text for row in renew_keyboard("start").inline_keyboard for b in row]
    assert texts == ["Тарифы"]


def test_faq_keyboard_has_every_question():
    buttons = [b for row in faq_keyboard().inline_keyboard for b in row]
    assert len(buttons) == len(FAQ) + 1


def test_callback_data_fits_telegram_limit():
    for code in TARIFFS:
        assert len(BuyTariff(code=code).pack().encode()) <= 64
