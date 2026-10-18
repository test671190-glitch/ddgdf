"""Тексты сообщений бота (HTML-разметка Telegram).

Эмодзи используются скупо: только там, где они несут смысл (успешная оплата, предупреждение).
"""

from datetime import timedelta
from html import escape

from bot.catalog import (
    IMAGE_MODELS,
    MODEL_CATEGORIES,
    SHOP_NAME,
    TARIFFS,
    TARIFFS_ON_SALE,
    TEXT_MODELS,
    TOTAL_MODELS,
    VIDEO_MODELS,
    Model,
    Tariff,
)
from bot.config import Settings
from bot.db import Activation, CryptoOrder, Subscription, utcnow
from bot.utils import fmt_dt

DIVIDER = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


def tariff_name(code: str) -> str:
    tariff = TARIFFS.get(code)
    return tariff.title if tariff else code


def days_left(sub: Subscription) -> int:
    # Вверх: сразу после покупки 30 дней — это 30, а не 29; в последние сутки — 1, а не 0
    return max(0, -(-(sub.expires_at - utcnow()) // timedelta(days=1)))


def progress_bar(left: int, total: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * left / total))) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def welcome(first_name: str | None, sub: Subscription | None) -> str:
    name = escape(first_name or "друг")
    if sub and sub.is_active:
        status = (
            f"Ваш тариф: <b>{tariff_name(sub.tariff)}</b> до {fmt_dt(sub.expires_at)}, "
            f"осталось {days_left(sub)} дн."
        )
    else:
        status = "Подписки пока нет. Выберите тариф, ключ придёт сразу после оплаты."
    return (
        f"<b>{SHOP_NAME}</b> · нейросети по API\n\n"
        f"Привет, {name}!\n"
        f"GPT-6, Claude Opus 5.5, Gemini, Grok и ещё {TOTAL_MODELS - 4} моделей для текста, "
        "картинок и видео по одному ключу.\n\n"
        "<blockquote>• API совместим с OpenAI и Anthropic\n"
        "• Работает в Cursor, Claude Code, Cline и ваших скриптах\n"
        "• Оплата Telegram Stars или криптой</blockquote>\n\n"
        f"{status}"
    )


HELP = (
    "<b>Команды</b>\n\n"
    "/start — главное меню\n"
    "/tariffs — тарифы и оплата\n"
    "/profile — подписка и API-ключ\n"
    "/api — как подключить\n"
    "/models — список моделей\n"
    "/faq — частые вопросы\n"
    "/paysupport — помощь с оплатой\n"
    "/terms — условия"
)


def tariffs_overview(settings: Settings) -> str:
    lines = [
        f"<b>Тарифы {SHOP_NAME}</b>",
        f"Подписка на {settings.subscription_days} дней. В каждом тарифе все {TOTAL_MODELS} "
        "моделей, отличаются только лимиты.",
    ]
    for t in TARIFFS_ON_SALE.values():
        lines += ["", f"<b>{t.title}</b> · <b>${t.price_usd}</b>"]
        if t.tagline:
            lines.append(f"<i>{escape(t.tagline)}</i>")
        lines.append(
            "<blockquote>" + "\n".join(f"• {escape(f)}" for f in t.features) + "</blockquote>"
        )
    lines += ["", "Оплата: Telegram Stars или криптовалюта через @CryptoBot."]
    return "\n".join(lines)


def tariff_details(tariff: Tariff, settings: Settings, current: Subscription | None) -> str:
    stars = tariff.stars(settings.stars_per_usd)
    lines = [f"<b>Тариф {tariff.title}</b>"]
    if tariff.tagline:
        lines.append(f"<i>{escape(tariff.tagline)}</i>")
    lines += [
        "",
        f"<b>${tariff.price_usd}</b> за {settings.subscription_days} дней · {stars} Stars",
        "",
        "<blockquote>" + "\n".join(f"• {escape(f)}" for f in tariff.features) + "</blockquote>",
        "",
        f"{len(TEXT_MODELS)} текстовых моделей, {len(IMAGE_MODELS)} для изображений, "
        f"{len(VIDEO_MODELS)} для видео.",
    ]
    if current and current.is_active:
        lines.append("")
        if current.tariff == tariff.code:
            lines.append(
                f"Подписка активна до {fmt_dt(current.expires_at)}. "
                f"Оплата добавит ещё {settings.subscription_days} дней."
            )
        else:
            lines.append(
                f"⚠️ Сейчас у вас {tariff_name(current.tariff)} до {fmt_dt(current.expires_at)}. "
                f"После оплаты тариф сменится на {tariff.title}, остаток дней не переносится."
            )
    lines += ["", "Выберите способ оплаты:"]
    return "\n".join(lines)


def invoice_description(tariff: Tariff, settings: Settings) -> str:
    text = f"Доступ на {settings.subscription_days} дней: " + "; ".join(tariff.features)
    return text[:255]


def crypto_invoice(tariff: Tariff, order: CryptoOrder) -> str:
    until = f" до {fmt_dt(order.expires_at)}" if order.expires_at else ""
    return (
        f"<b>Оплата криптой · {tariff.title}</b>\n"
        f"Сумма: <b>${tariff.price_usd}</b> · заказ #{order.id}\n\n"
        "<blockquote>1. Нажмите «Оплатить» — откроется счёт в @CryptoBot.\n"
        "2. Оплатите в USDT, TON, BTC или другой монете.\n"
        "3. Бот сам увидит платёж и пришлёт API-ключ.</blockquote>\n\n"
        f"Счёт действует{until}."
    )


def web_login_confirm() -> str:
    return (
        f"<b>Вход на сайт {SHOP_NAME}</b>\n\n"
        "Кто-то хочет войти на сайт под вашим аккаунтом. Если это вы — подтвердите.\n\n"
        "<blockquote>⚠️ Если вы не нажимали «Войти» на сайте, не подтверждайте: "
        "иначе чужой человек получит доступ к вашей подписке и API-ключу.</blockquote>"
    )


def crypto_expired(order: CryptoOrder) -> str:
    return (
        f"<b>Счёт по заказу #{order.id} истёк</b>\n\n"
        "Откройте тариф заново, и бот выставит новый счёт."
    )


def key_block(key: str, settings: Settings) -> list[str]:
    return [
        "<b>API-ключ</b> (нажмите, чтобы скопировать)",
        f"<code>{escape(key)}</code>",
        "",
        f"Base URL: <code>{escape(settings.client_api_url)}</code>",
    ]


def activation(
    tariff: Tariff, act: Activation, settings: Settings, *, paid: bool, key: str | None
) -> str:
    head = "🎉 <b>Оплата прошла!</b>" if paid else "<b>Вам выдан доступ</b>"
    verb = "продлён" if act.extended else "активен"
    lines = [
        head,
        f"Тариф <b>{tariff.title}</b> {verb} до {fmt_dt(act.expires_at)}.",
        DIVIDER,
    ]
    if key:
        lines += key_block(key, settings)
        lines += ["", "Как подключить к IDE и скриптам — в разделе «Подключение»."]
    else:
        lines.append("API-ключ готовим — пришлём его сюда в ближайшее время.")
    return "\n".join(lines)


def key_issued(tariff_title: str, key: str, settings: Settings) -> str:
    lines = [f"<b>Ваш API-ключ для тарифа {tariff_title} готов</b>", DIVIDER]
    lines += key_block(key, settings)
    lines += ["", "Как подключить — в разделе «Подключение»."]
    return "\n".join(lines)


def profile(user_id: int, sub: Subscription | None, key: str | None, settings: Settings) -> str:
    lines = ["<b>Профиль</b>", f"ID: <code>{user_id}</code>", DIVIDER]
    if sub is None or not sub.is_active:
        if sub is not None:
            lines.append(f"Подписка закончилась {fmt_dt(sub.expires_at)}.")
        else:
            lines.append("Активной подписки пока нет.")
        lines.append("Выберите тариф, и API-ключ придёт сразу после оплаты.")
        return "\n".join(lines)

    left = days_left(sub)
    lines += [
        f"Тариф: <b>{tariff_name(sub.tariff)}</b>",
        f"До: {fmt_dt(sub.expires_at)}",
        f"{progress_bar(left, settings.subscription_days)}  осталось {left} дн.",
        DIVIDER,
    ]
    if key:
        lines += key_block(key, settings)
    else:
        lines.append("API-ключ готовим — пришлём его сюда в ближайшее время.")
    return "\n".join(lines)


def rotate_confirm() -> str:
    return (
        "<b>Перевыпустить ключ?</b>\n\n"
        "Вы получите новый ключ, а старый сразу перестанет работать. Его нужно будет "
        "заменить во всех программах, где он указан.\n\n"
        "Перевыпускайте ключ, если он мог попасть в чужие руки."
    )


def models_overview() -> str:
    return (
        f"<b>Модели</b> · всего {TOTAL_MODELS}\n\n"
        "<blockquote>"
        f"Текст — {len(TEXT_MODELS)}\n"
        f"Изображения — {len(IMAGE_MODELS)}\n"
        f"Видео — {len(VIDEO_MODELS)}"
        "</blockquote>\n\n"
        "Выберите категорию, чтобы увидеть ID для API."
    )


def models_list(category: str) -> str:
    title, models = MODEL_CATEGORIES[category]
    by_vendor: dict[str, list[Model]] = {}
    for model in models:
        by_vendor.setdefault(model.vendor, []).append(model)
    lines = [
        f"<b>{title}</b>",
        "Нажмите на ID, чтобы скопировать его в параметр <code>model</code>.",
        "",
    ]
    for vendor, items in by_vendor.items():
        lines.append(f"<b>{escape(vendor)}</b>")
        lines += [f"{escape(m.name)} · <code>{escape(m.id)}</code>" for m in items]
        lines.append("")
    return "\n".join(lines).rstrip()


def api_info(settings: Settings) -> str:
    base = escape(settings.client_api_url)
    anthropic = escape(settings.client_anthropic_url)
    return (
        "<b>Подключение</b>\n\n"
        "<blockquote>"
        f"OpenAI: <code>{base}</code>\n"
        f"Anthropic: <code>{anthropic}</code>"
        "</blockquote>\n"
        "Ключ — в заголовке <code>Authorization: Bearer KEY</code> или <code>x-api-key</code>.\n\n"
        "<b>Пример запроса</b>\n"
        '<pre><code class="language-bash">'
        f"curl {base}/chat/completions \\\n"
        '  -H "Authorization: Bearer $UNKNOWN_SHOP_API_KEY" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"model": "claude-opus-5-5", '
        '"messages": [{"role": "user", "content": "Привет!"}]}\''
        "</code></pre>\n\n"
        "<b>Claude Code</b>\n"
        '<pre><code class="language-bash">'
        f'export ANTHROPIC_BASE_URL="{anthropic}"\n'
        'export ANTHROPIC_AUTH_TOKEN="ВАШ_КЛЮЧ"\n'
        'export ANTHROPIC_MODEL="claude-opus-5-5"\n'
        "claude"
        "</code></pre>\n\n"
        "<b>Cursor</b> — Settings → Models: ключ в OpenAI API Key, включите Override OpenAI "
        f"Base URL → <code>{base}</code>, затем Add Custom Model.\n\n"
        "<b>Cline / Roo Code / Kilo Code</b> — провайдер OpenAI Compatible, "
        f"Base URL <code>{base}</code>."
    )


def faq_overview() -> str:
    return "<b>Частые вопросы</b>\n\nВыберите вопрос:"


def faq_answer(question: str, answer: str) -> str:
    return f"<b>{escape(question)}</b>\n\n<blockquote>{escape(answer)}</blockquote>"


def support(settings: Settings) -> str:
    contact = (
        f"Напишите нам: @{escape(settings.support_username)}"
        if settings.support_username
        else "Контакт поддержки скоро появится."
    )
    return (
        "<b>Поддержка</b>\n\n"
        f"{contact}\n\n"
        "<blockquote>По вопросам оплаты укажите ваш ID из «Профиля», способ оплаты и номер "
        "заказа, если платили криптой.</blockquote>"
    )


def paysupport(settings: Settings) -> str:
    contact = (
        f"@{escape(settings.support_username)}"
        if settings.support_username
        else "администратору бота"
    )
    return (
        "<b>Помощь с оплатой</b>\n\n"
        f"Если оплата прошла, но доступ не появился, или нужен возврат — напишите {contact}.\n\n"
        "<blockquote>Укажите ваш ID из «Профиля» и дату платежа. Чек об оплате Stars есть в "
        "истории переводов Telegram, чек крипто-оплаты — в @CryptoBot.</blockquote>"
    )


def terms(settings: Settings) -> str:
    return (
        f"<b>Условия {SHOP_NAME}</b>\n\n"
        f"1. Подписка даёт доступ к API на {settings.subscription_days} дней с момента оплаты "
        "и не продлевается автоматически.\n"
        "2. Оплата принимается в Telegram Stars или криптовалютой через @CryptoBot. "
        "После оплаты бот выдаёт API-ключ.\n"
        "3. Ключ личный, его нельзя публиковать и передавать третьим лицам.\n"
        "4. По вопросам оплаты и возврата — /paysupport."
    )


def reminder(sub: Subscription) -> str:
    return (
        f"Подписка <b>{tariff_name(sub.tariff)}</b> закончится {fmt_dt(sub.expires_at)}.\n"
        "Продлите заранее, чтобы ключ продолжил работать без перерыва."
    )


def expired(sub: Subscription) -> str:
    return (
        f"Подписка <b>{tariff_name(sub.tariff)}</b> закончилась, API-ключ будет отключён.\n"
        "Чтобы продолжить, оформите тариф заново."
    )
