"""Тексты и кнопки. Кнопки — обычные dict в формате Bot API.

Чат без лишних шагов: вы всегда в чате. Просто напишите — ответит текущая модель,
а если чата ещё нет, он создаётся сам. Внизу всегда клавиатура:
«+ Новый чат», «☰ Мои чаты», «⁂ Модель», «⛭ Меню», а в поле ввода видно, кому вы пишете.
Модель меняется прямо в чате: переписка сохраняется, дальше отвечает новая модель.
«☰ Мои чаты» — список: нажатие переключает в чат, «✎ Изменить» — переименовать или удалить.

Меню (⛭ Меню, /start → кнопки): Инструменты, Модели, Мои чаты, Мой баланс, Настройки,
«❖ Пригласить» — личная ссылка t.me/<бот>?start=ref_<id> и бонусы за приглашённых.

Данные кнопок: nav:<экран>, card:<модель>:<откуда>, pick:<модель>:<откуда> (сменить модель
чата), chat:<модель>[:<откуда>] (новый чат с моделью), tgl:<настройка>, role:<роль>,
chats:<страница>:<s|e>, open:<чат>:<l|i|h>, info:<чат>, ren:<чат>, del:<чат>, delok:<чат>,
newchat, buy:<пакет>, ans:<ответ>:<r|m|s|back>, ansm:<ответ>:<модель>.

Ответ модели «печатается»: первая часть приходит сразу, дальше — правки того же
сообщения; под ним — «↻ Перегенерировать», «⇄ Другая модель», «− Короче».

Оформление монохромное: только символы Unicode без эмодзи-представления, тонкие
и одного веса. Знаки разработчиков — в catalog.VENDOR_MARKS, остальное здесь:
⁂ модели, ⛭ меню и настройки, ☰ чаты, + новый чат, ▸ открыть, ✎ изменить, ✕ удалить,
◈ баланс, ❖ приглашения, ↻ ⇄ − кнопки ответа, ⓘ уведомление, → ← ↓ ↳ переходы,
✓ выбранное, · разделитель.
"""

import time
from urllib.parse import quote

from models_bot.answers import TEST_DAILY_LIMIT, WARN_SHARE, escape, num, plural, shorten
from models_bot.catalog import (
    CUSTOM_ROLE,
    INPUTS,
    MODELS,
    OTHER_MODELS,
    PACKS,
    REF_BONUS,
    REF_PREFIX,
    ROLE_MAX_LENGTH,
    STYLES,
    TOOLS,
    TOP_MODELS,
    Model,
)
from models_bot.store import DETAILS, LANGS, MAX_CHATS, TITLE_LENGTH, Chat, Prefs

BRAND = "unknown shop"
MODELS_TITLE = "⁂ Модели"
SETTINGS = "⛭ Настройки"
MY_CHATS = "☰ Мои чаты"
NEW_CHAT = "+ Новый чат"
MODEL = "⁂ Модель"
MENU = "⛭ Меню"
TOOLS_TITLE = "⌘ Инструменты"
KEYBOARD_BUTTONS = (NEW_CHAT, MY_CHATS, MODEL, MENU)  # клавиатура внизу
# Кнопки клавиатуры прошлой версии: у кого-то она ещё на экране
OLD_CHATS, OLD_NEW_DIALOG, OLD_END_CHAT = "☰ Чаты", "↺ Новый диалог", "■ Завершить чат"
OLD_BUTTONS = (OLD_CHATS, OLD_NEW_DIALOG, OLD_END_CHAT)
BALANCE = "◈ Мой баланс"
INVITE = "❖ Пригласить"
REGENERATE = "↻ Перегенерировать"
OTHER_MODEL = "⇄ Другая модель"
SHORTER = "− Короче"
CURSOR = " ▍"  # конец текста, пока ответ ещё «печатается»
TO_MENU = "← В меню"
BACK = "← Назад"
CHATS_PAGE = 8  # чатов на странице списка
MSK = 3 * 3600  # время в списке чатов — московское, как и сброс лимита

COMMANDS = [
    {"command": "start", "description": "⌂ Начало"},
    {"command": "new", "description": "+ Новый чат"},
    {"command": "chats", "description": "☰ Мои чаты: переключиться"},
    {"command": "model", "description": "⁂ Сменить модель"},
    {"command": "tools", "description": "⌘ Инструменты: идеи, пересказ, картинки"},
    {"command": "menu", "description": "⛭ Меню"},
    {"command": "settings", "description": "⛭ Настройки"},
    {"command": "balance", "description": "◈ Мой баланс и пополнение"},
    {"command": "invite", "description": "❖ Пригласить друга: бонусные токены"},
    {"command": "help", "description": "ⓘ Помощь"},
    {"command": "paysupport", "description": "ⓘ Вопрос по оплате"},
]

# Переключатели в настройках: подпись на кнопке
TOGGLES = {
    "memory": "Память",
    "show_usage": "Расход",
    "lang": "Язык",
    "detail": "Ответы",
    "notify_models": "Новые модели",
    "notify_limit": "Окончание лимита",
}


def button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def inline(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [row for row in rows if row]}


def column(buttons: list[dict]) -> list[list[dict]]:
    return [[b] for b in buttons]


def pairs(buttons: list[dict]) -> list[list[dict]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def on(flag: bool) -> str:
    return "вкл" if flag else "выкл"


def toggle_value(key: str, prefs: Prefs) -> str:
    if key == "lang":
        return LANGS[prefs.lang]
    if key == "detail":
        return DETAILS[prefs.detail]
    return on(getattr(prefs, key))


def toggle_note(key: str, prefs: Prefs) -> str:
    return f"✓ {TOGGLES[key]} · {toggle_value(key, prefs)}"


def notify_summary(prefs: Prefs) -> str:
    parts = [
        name
        for flag, name in ((prefs.notify_models, "новые модели"), (prefs.notify_limit, "лимит"))
        if flag
    ]
    return " и ".join(parts) if parts else "выключены"


def messages(n: int) -> str:
    return plural(n, "сообщение", "сообщения", "сообщений")


def chat_name(chat: Chat) -> str:
    """Название чата: первый вопрос или своё, а у пустого — «Новый чат»."""
    return chat.title or "Новый чат"


def chat_label(chat: Chat) -> str:
    model = MODELS.get(chat.model)
    return f"{model.mark if model else '·'} {chat_name(chat)}"


def when(ts: float, now: float | None = None) -> str:
    """«сегодня 19:40», «вчера 08:15» или «21.10» — по Москве."""
    now = time.time() if now is None else now
    day = time.gmtime(ts + MSK)
    days = int((now + MSK) // 86400) - int((ts + MSK) // 86400)
    clock = time.strftime("%H:%M", day)
    if days == 0:
        return f"сегодня {clock}"
    if days == 1:
        return f"вчера {clock}"
    return time.strftime("%d.%m", day)


# Клавиатура внизу — всегда на экране


def keyboard(model: Model) -> dict:
    """Главная клавиатура: в поле ввода видно, какой модели вы пишете."""
    return {
        "keyboard": [
            [{"text": NEW_CHAT}, {"text": MY_CHATS}],
            [{"text": MODEL}, {"text": MENU}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": f"Сообщение для {model.name}"[:64],
    }


# Тексты


def welcome(first_name: str | None) -> str:
    """Строка перед меню на /start: с ней приходит клавиатура внизу, меню — следом."""
    return f"Привет, {escape(first_name or 'друг')}. Пишите вопрос или выберите в меню ↓"


def menu_text(model: Model, chat: Chat | None, prefs: Prefs) -> str:
    now = f"Чат «{escape(chat_name(chat))}»" if chat and chat.messages else "Новый чат"
    return (
        f"<b>{BRAND}</b> · меню\n\n"
        f"<blockquote>Сейчас — {now} · {escape(model.label)}\n"
        f"Баланс — {num(prefs.balance)} токенов, бесплатно сегодня — {num(TEST_DAILY_LIMIT)}"
        "</blockquote>"
    )


def models_text() -> str:
    return (
        f"<b>{MODELS_TITLE}</b>\nТоп нейросетей: Claude и GPT\n\n"
        "Выберите модель, чтобы узнать о ней и писать ей ↓"
    )


def other_text() -> str:
    return "<b>Остальные модели</b>\nGPT-6 Sol и линейка Gemini\n\nВыберите модель ↓"


def model_card(model: Model, current: str = "") -> str:
    """current — модель, которая отвечает сейчас."""
    lines = [
        f"<b>{escape(model.label)}</b>",
        f"<i>{escape(model.vendor)} · тестовая модель</i>",
        "",
        f"<blockquote>{escape(model.about)}</blockquote>",
        "Понимает: " + ", ".join(["текст"] + [INPUTS[k] for k in model.inputs]),
        "",
        f"ID · <code>{escape(model.id)}</code>",
    ]
    if current == model.id:
        lines.append("✓ Сейчас отвечает она")
    return "\n".join(lines)


def picker_text(model: Model) -> str:
    return (
        f"<b>{MODEL}</b>\n"
        f"Сейчас отвечает <b>{escape(model.label)}</b>.\n\n"
        "Выберите другую — она продолжит этот же чат с его перепиской ↓"
    )


def model_switched(model: Model, chat: Chat | None) -> str:
    where = f" в чате «{escape(chat_name(chat))}»" if chat and chat.messages else ""
    return f"⁂ Теперь{where} отвечает <b>{escape(model.label)}</b>. Пишите."


def new_chat_text(model: Model, previous: Chat | None, dropped: list[Chat] = ()) -> str:
    lines = [f"<b>{NEW_CHAT} · {escape(model.label)}</b>"]
    if previous:
        lines.append(f"<i>Прошлый чат «{escape(chat_name(previous))}» сохранён в «{MY_CHATS}».</i>")
    for chat in dropped:
        lines.append(
            f"<i>Самый давний чат «{escape(chat_name(chat))}» удалён: храню до {MAX_CHATS}.</i>"
        )
    lines.append(f"Пишите, {escape(model.name)} ответит.")
    return "\n".join(lines)


def already_new(model: Model) -> str:
    return f"Это уже новый чат: пишите, {escape(model.name)} ответит."


def chat_opened(chat: Chat, model: Model, last_question: str | None, prefs: Prefs) -> str:
    lines = [
        f"<b>▸ Чат «{escape(chat_name(chat))}»</b>",
        f"<i>{escape(model.label)} · {messages(chat.messages)} · {when(chat.updated)}</i>",
    ]
    if last_question:
        lines += [
            "",
            f"<blockquote>Последний вопрос: «{escape(shorten(last_question))}»</blockquote>",
        ]
    lines.append("")
    if last_question and prefs.memory:
        lines.append(f"Продолжайте: {escape(model.name)} помнит эту переписку.")
    else:
        lines.append(f"Пишите, {escape(model.name)} ответит.")
    return "\n".join(lines)


def chats_text(chats: list[Chat], active: Chat | None, edit: bool) -> str:
    if not chats:
        return (
            f"<b>{MY_CHATS}</b>\n\n"
            "Здесь будут ваши чаты. Напишите любой вопрос — первый чат начнётся сам."
        )
    head = f"<b>{'✎' if edit else '☰'} Мои чаты</b> · {len(chats)}"
    hint = (
        "Выберите чат, чтобы переименовать или удалить ↓"
        if edit
        else "Нажмите на чат, чтобы перейти в него ↓ Текущий отмечен ✓"
    )
    return f"{head}\n\n{hint}"


def chat_info_text(chat: Chat, active_id: int, last_question: str | None) -> str:
    model = MODELS.get(chat.model)
    lines = [
        f"<b>{escape(chat_label(chat))}</b>",
        f"<i>{escape(model.name) if model else 'Модель больше недоступна'} · "
        f"{messages(chat.messages)} · {when(chat.updated)}</i>",
    ]
    if chat.id == active_id:
        lines.append("✓ Текущий чат")
    if last_question:
        lines += [
            "",
            f"<blockquote>Последний вопрос: «{escape(shorten(last_question))}»</blockquote>",
        ]
    return "\n".join(lines)


def rename_text(chat: Chat) -> str:
    return (
        "<b>✎ Название чата</b>\n"
        f"Сейчас: «{escape(chat_name(chat))}»\n\n"
        f"Пришлите новое название одним сообщением, до {TITLE_LENGTH} символов."
    )


def title_too_long(length: int) -> str:
    return (
        f"Слишком длинно: {plural(length, 'символ', 'символа', 'символов')} "
        f"из {TITLE_LENGTH}. Пришлите название покороче."
    )


TITLE_SAVED = "✓ Название сохранено"


def delete_text(chat: Chat) -> str:
    return (
        f"<b>✕ Удалить чат «{escape(chat_name(chat))}»?</b>\n"
        f"{messages(chat.messages).capitalize()} удалятся, вернуть их не получится."
    )


def chat_deleted(chat: Chat) -> str:
    return f"✕ Текущий чат «{escape(chat_name(chat))}» удалён. Следующее сообщение начнёт новый."


CHAT_GONE = "Этого чата больше нет"
KEYBOARD_UPDATED = (
    "<b>Чаты стали проще</b>\n"
    "Теперь вы всегда в чате: просто пишите, и модель ответит. Открывать и закрывать "
    f"чат не нужно.\n\n<blockquote>{NEW_CHAT} — начать с чистого листа\n"
    f"{MY_CHATS} — переключиться на прошлый чат\n"
    f"{MODEL} — сменить модель прямо в чате\n"
    f"{MENU} — модели, настройки, баланс</blockquote>"
)
NO_CLOSING = (
    f"Чат закрывать не нужно: просто пишите. Новый — «{NEW_CHAT}», "
    f"прошлые — в «{MY_CHATS}». Клавиатуру внизу я обновил."
)


def settings_text(prefs: Prefs, used_today: int) -> str:
    return (
        f"<b>{SETTINGS}</b>\n\n"
        f"<blockquote>Модель — {escape(MODELS[prefs.model].label)}\n"
        f"Роль — {escape(prefs.role_title.lower())}\n"
        f"Память диалога — {'включена' if prefs.memory else 'выключена'}\n"
        f"Расход токенов — {'показывать' if prefs.show_usage else 'не показывать'}\n"
        f"Уведомления — {notify_summary(prefs)}\n"
        f"Язык ответов — {LANGS[prefs.lang]}\n"
        f"Ответы — {DETAILS[prefs.detail]}</blockquote>\n\n"
        f"Сегодня израсходовано {num(used_today)} токенов, бесплатно — {num(TEST_DAILY_LIMIT)}, "
        f"на балансе — {num(prefs.balance)}.\n"
        "Нажмите на пункт, чтобы изменить. Всё сразу действует и в текущем чате."
    )


def role_text(prefs: Prefs, note: str = "") -> str:
    head = f"{note}\n\n" if note else ""
    now = (
        f"<blockquote>{escape(prefs.role_prompt)}</blockquote>"
        if prefs.role_prompt
        else "Сейчас не задана: модель отвечает без особой инструкции."
    )
    return (
        f"{head}<b>⛭ Роль</b>\n"
        "Своя инструкция для модели — она получает её перед каждым вопросом во всех чатах.\n\n"
        f"{now}"
    )


def role_input_text() -> str:
    return (
        "<b>✎ Своя роль</b>\n"
        f"Пришлите инструкцию одним сообщением, до {ROLE_MAX_LENGTH} символов.\n\n"
        "<blockquote>Например: «Ты — опытный маркетолог. Отвечай коротко, по пунктам, "
        "с примерами для малого бизнеса».</blockquote>"
    )


def role_too_long(length: int) -> str:
    return (
        f"Слишком длинно: {plural(length, 'символ', 'символа', 'символов')} "
        f"из {ROLE_MAX_LENGTH}. Сократите инструкцию и пришлите ещё раз."
    )


def notify_text() -> str:
    return (
        "<b>⛭ Уведомления</b>\n\n"
        "<blockquote>Новые модели — сообщу, когда в боте появится модель\n"
        f"Окончание лимита — предупрежу на {round(WARN_SHARE * 100)}% и 100% "
        "дневного лимита</blockquote>"
    )


def new_models_text(models: list[Model]) -> str:
    names = "\n".join(escape(m.label) for m in models)
    return (
        "<b>ⓘ Новые модели в боте</b>\n\n"
        f"<blockquote>{names}</blockquote>\n\n"
        "Откройте карточку ↓\n"
        "<i>Отключить: ⛭ Настройки → Уведомления.</i>"
    )


HELP = (
    "<b>ⓘ Помощь</b>\n\n"
    "Просто напишите вопрос — ответит текущая модель. Можно прислать фото, PDF или голосовое "
    "с подписью. Под каждым ответом — «↻ Перегенерировать», «⇄ Другая модель» и «− Короче».\n\n"
    f"<blockquote>{NEW_CHAT} или /new — начать с чистого листа\n"
    f"{MY_CHATS} или /chats — переключиться на прошлый чат\n"
    f"{MODEL} или /model — сменить модель в текущем чате\n"
    f"{MENU} или /menu — инструменты, модели, настройки, баланс\n"
    f"{TOOLS_TITLE} или /tools — идеи, объяснение, пересказ, картинки\n"
    "/balance — баланс и пополнение\n"
    f"/invite — пригласить друга, +{num(REF_BONUS)} токенов за каждого</blockquote>"
)


def tools_text(model: Model) -> str:
    return (
        f"<b>{TOOLS_TITLE}</b>\n"
        f"Готовые задачи для {escape(model.label)} в текущем чате. "
        "Выберите, затем пришлите текст или фото ↓"
    )


def styles_text() -> str:
    return f"<b>{TOOLS['styles'].mark} {TOOLS['styles'].title}</b>\nВыберите стиль ↓"


def tool_ask_text(tool_key: str, style: str = "") -> str:
    tool = TOOLS[tool_key]
    ask = tool.ask.format(style=STYLES.get(style, ""))
    return f"<b>{tool.mark} {tool.title}</b>\n{escape(ask)}"


def tool_needs(tool_key: str) -> str:
    tool = TOOLS[tool_key]
    what = {"photo": "фото", "text": "текст", "text_or_file": "текст или файл"}[tool.needs]
    return (
        f"Для «{tool.title}» нужно прислать {what}. Пришлите его или выберите другое "
        f"в «{TOOLS_TITLE}»."
    )


ONLY_TEXT = "Такое пока не понимаю: пришлите текст, фото, PDF или голосовое."


def cant_read(model: Model, kind: str) -> str:
    able = [m.name for m in TOP_MODELS + OTHER_MODELS if kind in m.inputs]
    return (
        f"{escape(model.name)} не понимает {INPUTS[kind]}. "
        f"Их понимают: {escape(', '.join(able))} — смените модель кнопкой «{MODEL}» "
        "или пришлите текст."
    )


def balance_text(prefs: Prefs, used_today: int) -> str:
    free_left = max(0, TEST_DAILY_LIMIT - used_today)
    return (
        f"<b>{BALANCE}</b>\n\n"
        f"<blockquote>Бесплатно сегодня — осталось {num(free_left)} из {num(TEST_DAILY_LIMIT)}\n"
        f"Купленные токены — {num(prefs.balance)}</blockquote>\n\n"
        "Сначала тратятся бесплатные токены, они обновляются в 03:00 по Москве. "
        "Когда они кончатся, токены списываются с баланса.\n\n"
        "Пополнить баланс за Telegram Stars ↓\n"
        "<i>Тестовый режим: модели пока отвечают заготовками.</i>"
    )


def invoice(pack) -> dict:
    title = f"{num(pack.tokens)} токенов"
    return {
        "title": title,
        "description": (
            f"Пополнение баланса {BRAND} на {num(pack.tokens)} токенов. "
            "Сейчас модели работают в тестовом режиме и отвечают заготовками."
        ),
        "payload": f"pack:{pack.key}",
        "currency": "XTR",
        "prices": [{"label": title, "amount": pack.stars}],
    }


def paid(tokens: int, balance: int) -> str:
    return f"✓ Оплата прошла: +{num(tokens)} токенов. На балансе — {num(balance)}."


PAY_SUPPORT = (
    "<b>ⓘ Вопрос по оплате</b>\n\n"
    "Если Stars списались, а токены не пришли, или хотите вернуть оплату — опишите ситуацию "
    "владельцу бота и приложите чек из истории Stars в Telegram. Возврат приходит обратно в Stars."
)


def ref_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start={REF_PREFIX}{user_id}"


def invite_text(link: str, invited: int, earned: int) -> str:
    return (
        f"<b>{INVITE} друга</b>\n"
        f"За каждого, кто впервые откроет бота по вашей ссылке, — +{num(REF_BONUS)} токенов "
        "на баланс.\n\n"
        f"<blockquote>Приглашено — {num(invited)}\n"
        f"Получено бонусов — {num(earned)} токенов</blockquote>\n\n"
        f"Ваша ссылка:\n<code>{escape(link)}</code>"
    )


def invite_share(link: str) -> str:
    """Ссылка «поделиться»: Telegram предложит выбрать, кому отправить."""
    note = f"{BRAND}: Claude, GPT и Gemini в одном боте"
    return f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(note, safe='')}"


def referral_joined(first_name: str | None, tokens: int, balance: int) -> str:
    who = f" — {escape(first_name)}" if first_name else ""
    return (
        f"{INVITE[0]} По вашей ссылке новый пользователь{who}: +{num(tokens)} токенов. "
        f"На балансе — {num(balance)}."
    )


ROLE_ONLY_TEXT = "Пришлите инструкцию текстом или нажмите «← Отмена»."
TITLE_ONLY_TEXT = "Пришлите название текстом или нажмите «← Отмена»."
STALE_BUTTON = "Кнопка устарела, открываю меню"
ROLE_SAVED = "✓ Своя роль сохранена"
ROLE_RESET = "✓ Роль сброшена"


# Кнопки под сообщениями


def menu_keyboard() -> dict:
    return inline(
        [button(TOOLS_TITLE, "nav:tools"), button(MODELS_TITLE, "nav:models")],
        [button(MY_CHATS, "nav:chats"), button(BALANCE, "nav:balance")],
        [button(SETTINGS, "nav:settings"), button(INVITE, "nav:invite")],
    )


def tools_keyboard() -> dict:
    tools = [button(f"{t.mark} {t.title}", f"tool:{t.key}") for t in TOOLS.values()]
    return inline(*pairs(tools + [button(TO_MENU, "nav:main")]))


def styles_keyboard() -> dict:
    styles = [button(name.capitalize(), f"style:{key}") for key, name in STYLES.items()]
    return inline(*pairs(styles), [button(f"← {TOOLS_TITLE[2:]}", "nav:tools")])


def tool_ask_keyboard() -> dict:
    return inline([button(f"← {TOOLS_TITLE[2:]}", "nav:tools")])


def answer_keyboard(reply_id: int) -> dict:
    """Под ответом модели."""
    return inline(
        [button(REGENERATE, f"ans:{reply_id}:r")],
        [button(OTHER_MODEL, f"ans:{reply_id}:m"), button(SHORTER, f"ans:{reply_id}:s")],
    )


def other_model_keyboard(reply_id: int, current: str) -> dict:
    models = [
        button(m.label, f"ansm:{reply_id}:{m.id}")
        for m in TOP_MODELS + OTHER_MODELS
        if m.id != current
    ]
    return inline(*column(models), [button(BACK, f"ans:{reply_id}:back")])


def balance_keyboard() -> dict:
    packs = [
        button(f"+ {num(p.tokens)} токенов · {p.stars} Stars", f"buy:{p.key}")
        for p in PACKS.values()
    ]
    return inline(*column(packs), [button(TO_MENU, "nav:main")])


def buy_keyboard() -> dict:
    """Под сообщением «лимит исчерпан»."""
    return inline([button(f"{BALANCE} · пополнить", "nav:balance")])


def invite_keyboard(link: str) -> dict:
    return inline(
        [{"text": "→ Поделиться ссылкой", "url": invite_share(link)}],
        [button(TO_MENU, "nav:main")],
    )


def picker_keyboard(current: str) -> dict:
    """Смена модели текущего чата: все модели, у текущей — ✓."""
    models = [
        button(f"{m.label}  ✓" if m.id == current else m.label, f"pick:{m.id}:p")
        for m in TOP_MODELS + OTHER_MODELS
    ]
    return inline(*column(models), [button(MODELS_TITLE + " · подробнее", "nav:models")])


def models_keyboard() -> dict:
    models = [button(m.label, f"card:{m.id}:models") for m in TOP_MODELS]
    return inline(
        *column(models),
        [button("Остальные модели →", "nav:other")],
        [button(TO_MENU, "nav:main")],
    )


def other_keyboard() -> dict:
    models = [button(m.label, f"card:{m.id}:other") for m in OTHER_MODELS]
    return inline(*column(models), [button(BACK, "nav:models")])


def card_keyboard(model: Model, src: str, current: str = "") -> dict:
    back = "nav:other" if src == "other" else "nav:models"
    pick = (
        [button("✓ Сейчас отвечает она", f"pick:{model.id}:{src}")]
        if current == model.id
        else [button("▸ Писать ей в текущем чате", f"pick:{model.id}:{src}")]
    )
    return inline(
        pick,
        [button(f"{NEW_CHAT} с ней", f"chat:{model.id}:{src or 'models'}")],
        [button(BACK, back)],
    )


def chats_keyboard(chats: list[Chat], active_id: int, page: int, edit: bool) -> dict:
    mode = "e" if edit else "s"
    pages = max(1, -(-len(chats) // CHATS_PAGE))
    page = min(max(page, 0), pages - 1)
    rows = []
    for chat in chats[page * CHATS_PAGE : (page + 1) * CHATS_PAGE]:
        label = chat_label(chat) + ("  ✓" if chat.id == active_id else "")
        rows.append([button(label, f"info:{chat.id}" if edit else f"open:{chat.id}:l")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(button("←", f"chats:{page - 1}:{mode}"))
        nav.append(button(f"{page + 1} из {pages}", f"chats:{page}:{mode}"))
        if page < pages - 1:
            nav.append(button("→", f"chats:{page + 1}:{mode}"))
        rows.append(nav)
    if chats:
        rows.append(
            [
                button(NEW_CHAT, "newchat"),
                button(
                    "✓ Готово" if edit else "✎ Изменить", f"chats:{page}:{'s' if edit else 'e'}"
                ),
            ]
        )
    else:
        rows.append([button(NEW_CHAT, "newchat")])
    rows.append([button(TO_MENU, "nav:main")])
    return inline(*rows)


def chat_info_keyboard(chat: Chat, active_id: int) -> dict:
    go = "▸ Продолжить чат" if chat.id == active_id else "▸ Перейти в чат"
    rows = []
    if chat.model in MODELS:
        rows.append([button(go, f"open:{chat.id}:i")])
    rows += [
        [button("✎ Переименовать", f"ren:{chat.id}"), button("✕ Удалить", f"del:{chat.id}")],
        [button("← Все чаты", "chats:0:e")],
    ]
    return inline(*rows)


def rename_keyboard(chat: Chat) -> dict:
    return inline([button("← Отмена", f"info:{chat.id}")])


def delete_keyboard(chat: Chat) -> dict:
    return inline([button("✕ Да, удалить", f"delok:{chat.id}"), button("← Нет", f"info:{chat.id}")])


def _toggle(key: str, prefs: Prefs) -> dict:
    return button(f"{TOGGLES[key]} · {toggle_value(key, prefs)}", f"tgl:{key}")


def settings_keyboard(prefs: Prefs) -> dict:
    return inline(
        *column(
            [
                button(f"Модель · {MODELS[prefs.model].name}", "nav:picker"),
                button(f"Роль · {prefs.role_title}", "nav:role"),
                _toggle("memory", prefs),
                _toggle("show_usage", prefs),
                button("Уведомления →", "nav:notify"),
                _toggle("lang", prefs),
                _toggle("detail", prefs),
                button(TO_MENU, "nav:main"),
            ]
        )
    )


def role_keyboard(prefs: Prefs) -> dict:
    if prefs.role == CUSTOM_ROLE:
        return inline(
            [button("✎ Изменить", f"role:{CUSTOM_ROLE}"), button("✕ Сбросить", "role:reset")],
            [button(BACK, "nav:settings")],
        )
    return inline(
        [button("✎ Написать свою роль", f"role:{CUSTOM_ROLE}")], [button(BACK, "nav:settings")]
    )


def role_input_keyboard() -> dict:
    return inline([button("← Отмена", "nav:role")])


def notify_keyboard(prefs: Prefs) -> dict:
    return inline(
        [_toggle("notify_models", prefs)],
        [_toggle("notify_limit", prefs)],
        [button(BACK, "nav:settings")],
    )


def new_models_keyboard(models: list[Model]) -> dict:
    return inline(
        *column(
            [
                button(m.label, f"card:{m.id}:{'models' if m in TOP_MODELS else 'other'}")
                for m in models[:8]
            ]
        )
    )
