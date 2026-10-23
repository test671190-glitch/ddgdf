"""Тестовые ответы: настоящие модели не подключены, бот отвечает заготовками.

Заготовка зависит от роли, инструмента и языка. Подробный ответ добавляет блок с тем, что учтено,
а с включённой памятью бот вспоминает прошлый вопрос. Токены считаются примерно,
как у настоящих моделей: четыре символа — один токен. Вход — это роль, история
диалога и сам вопрос, выход — текст ответа.
"""

import re
from collections import namedtuple

from models_bot.catalog import CUSTOM_ROLE, STYLES, TOOLS, Model
from models_bot.store import LANGS, Prefs, Turn

TEST_DAILY_LIMIT = 20_000  # бесплатных токенов в сутки на пользователя
WARN_SHARE = 0.8  # на этой доле лимита — предупреждение
STREAM_STEPS = 3  # ответ появляется по частям: столько правок сообщения
STREAM_DELAY = 0.25  # секунд между частями: первая часть уходит сразу
SHORT_ANSWER = 200  # символов: ответ короче печатается в две части, а не в STREAM_STEPS

CANNED: dict[str, dict[str, list[str]]] = {
    "none": {
        "ru": [
            "Это тестовый ответ. Модели пока не подключены, поэтому бот отвечает "
            "заготовками — так будет выглядеть переписка с настоящей нейросетью.",
            "Вопрос принят. Бот работает в тестовом режиме: ответ модели появится здесь, "
            "когда подключим API.",
        ],
        "en": [
            "This is a test reply. Models aren't connected yet, so the bot answers with "
            "prepared text — this is how a chat with a real model will look.",
            "Got your question. The bot is in test mode: the model's answer will appear here "
            "once the API is connected.",
        ],
    },
    "custom": {
        "ru": [
            "Отвечаю в вашей роли. В рабочем режиме модель будет следовать инструкции из настроек.",
        ],
        "en": [
            "Answering in your role. In live mode the model will follow the instruction "
            "from the settings.",
        ],
    },
}


# Ответы инструментов: {topic} — тема или текст пользователя, {about} — его фото или файл
TOOL_CANNED: dict[str, dict[str, str]] = {
    "styles": {
        "ru": "Здесь будет ваше {about} в стиле «{style}». В рабочем режиме модель "
        "перерисует его, сохранив композицию.",
        "en": "Your {about} in the «{style}» style will be here. In live mode the model "
        "will redraw it, keeping the composition.",
    },
    "draw": {
        "ru": "Здесь появится картинка 1024×1024 по описанию «{topic}». "
        "В рабочем режиме модель её нарисует.",
        "en": "A 1024×1024 picture of «{topic}» will appear here. In live mode the model "
        "will draw it.",
    },
    "edit": {
        "ru": "Здесь будет ваше {about} с изменениями: «{topic}». Остальное модель оставит "
        "как есть.",
        "en": "Your {about} with the changes «{topic}» will be here. The model will keep "
        "the rest as is.",
    },
    "brainstorm": {
        "ru": "Идеи по теме «{topic}»:\n"
        "1. Посмотреть на задачу глазами того, для кого она решается\n"
        "2. Сделать самый простой вариант за день и проверить\n"
        "3. Спросить тех, кто уже решал похожее\n"
        "4. Убрать лишнее и оставить главное\n"
        "5. Совместить две сильные идеи в одну\n"
        "В рабочем режиме модель предложит идеи именно под вашу задачу.",
        "en": "Ideas on «{topic}»:\n"
        "1. Look at it through the eyes of the person it's for\n"
        "2. Build the simplest version in a day and test it\n"
        "3. Ask people who solved something similar\n"
        "4. Cut everything but the core\n"
        "5. Merge two strong ideas into one\n"
        "In live mode the model will tailor ideas to your task.",
    },
    "explain": {
        "ru": "Объясняю «{topic}» простыми словами: сначала суть в одной фразе, потом — "
        "как это устроено по шагам и пример из жизни. В рабочем режиме здесь будет "
        "настоящее объяснение.",
        "en": "Explaining «{topic}» simply: the gist in one line, then how it works step "
        "by step and a real-life example. A real explanation will be here in live mode.",
    },
    "rewrite": {
        "ru": "Переписанный текст появится здесь: короче, яснее и без ошибок. Исходник: «{topic}».",
        "en": "The rewritten text will appear here: shorter, clearer, no mistakes. "
        "Original: «{topic}».",
    },
    "summary": {
        "ru": "Главное из «{topic}»:\n• О чём это\n• Что из этого следует\n• Что сделать дальше\n"
        "В рабочем режиме модель выделит суть по-настоящему.",
        "en": "The gist of «{topic}»:\n• What it's about\n• What follows from it\n• What to do "
        "next\nIn live mode the model will extract the real gist.",
    },
}


class Reply(namedtuple("Reply", "html question_tokens input_tokens output_tokens")):
    __slots__ = ()

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


# Свои escape и plain_text вместо модуля html: тот грузит таблицу из 2 000 сущностей
TAG = re.compile(r"<[^>]+>")
ENTITY = re.compile(r"&(?:lt|gt|amp|quot|#x27|#39);")
ENTITIES = {"&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&#x27;": "'", "&#39;": "'"}


def escape(text: str) -> str:
    """Экранирование для HTML-разметки Telegram: ей достаточно &, < и >."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plain_text(html: str) -> str:
    return ENTITY.sub(lambda m: ENTITIES[m.group()], TAG.sub("", html))


def count_tokens(text: str) -> int:
    return -(-len(text) // 4)  # вверх: короткое «да» — тоже токен


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 токен», «3 токена», «25 токенов»."""
    rest = abs(n) % 100
    if 11 <= rest <= 14:
        word = many
    elif rest % 10 == 1:
        word = one
    elif 2 <= rest % 10 <= 4:
        word = few
    else:
        word = many
    return f"{num(n)} {word}"


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def shorten(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def considered(
    model: Model, prefs: Prefs, history: list[Turn], tool: str | None = None
) -> list[str]:
    """Что учтено в ответе: показывает, что настройки работают."""
    key = (tool or "").partition(":")[0]
    if prefs.lang == "en":
        role = "custom" if prefs.role == CUSTOM_ROLE else "no role"
        if not prefs.memory:
            memory = "each question on its own"
        elif history:
            memory = f"{len(history)} messages in context"
        else:
            memory = "new chat"
        points = [f"Model · {model.name}", f"Role · {role}", f"Memory · {memory}"]
        if key in TOOLS:
            points.append(f"Tool · {TOOLS[key].title}")
        return points + ["Language · English"]
    if not prefs.memory:
        memory = "каждый вопрос отдельно"
    elif history:
        memory = "в контексте " + plural(len(history), "сообщение", "сообщения", "сообщений")
    else:
        memory = "новый диалог"
    points = [
        f"Модель · {model.name}",
        f"Роль · {prefs.role_title.lower()}",
        f"Память · {memory}",
    ]
    if key in TOOLS:
        points.append(f"Инструмент · {TOOLS[key].title.lower()}")
    return points + [f"Язык · {LANGS[prefs.lang]}"]


# Вложения: что модель «видит» и сколько токенов это стоит (примерно как у настоящих)
Attachment = namedtuple("Attachment", "kind about tokens")  # kind: image | file | voice


def attachment_of(message: dict) -> Attachment | None:
    """Фото, документ или голосовое из сообщения Telegram; None — если вложения нет."""
    if photos := message.get("photo"):
        best = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
        size = f"{best.get('width', 0)}×{best.get('height', 0)}"
        return Attachment("image", f"изображение {size}", 800)
    if voice := message.get("voice") or message.get("audio") or message.get("video_note"):
        seconds = int(voice.get("duration", 0))
        return Attachment(
            "voice", f"голосовое {seconds // 60}:{seconds % 60:02d}", 30 + 25 * seconds
        )
    if document := message.get("document"):
        name = document.get("file_name") or "файл"
        size = int(document.get("file_size", 0))
        kind = "image" if (document.get("mime_type") or "").startswith("image/") else "file"
        about = f"файл «{name}», {max(1, size // 1024)} КБ"
        return Attachment(kind, about, min(50_000, 200 + size // 8))
    return None


def pack_attachment(a: Attachment | None) -> str | None:
    return f"{a.kind}|{a.tokens}|{a.about}" if a else None


def unpack_attachment(text: str | None) -> Attachment | None:
    if not text:
        return None
    kind, tokens, about = text.split("|", 2)
    return Attachment(kind, about, int(tokens))


SEEN = {
    "image": ("Вижу {about}.", "I can see the {about}."),
    "file": ("Прочитал {about}.", "I've read the {about}."),
    "voice": ("Прослушал {about}.", "I've listened to the {about}."),
}


def first_sentence(text: str) -> str:
    match = re.search(r"[.!?…](\s|$)", text)
    return text[: match.end()].strip() if match else text


def compose(
    model: Model,
    prefs: Prefs,
    question: str,
    history: list[Turn],
    attempt: int = 0,
    brief: bool = False,
    attachment: Attachment | None = None,
    tool: str | None = None,
) -> Reply:
    """history — прошлые сообщения диалога (пусто, если память выключена).
    attempt — номер перегенерации: другая заготовка; brief — «короче»: одна фраза;
    tool — инструмент («brainstorm», «styles:anime»): его готовый промпт и ответ."""
    en = prefs.lang == "en"
    key, _, style = (tool or "").partition(":")
    kit = TOOLS.get(key)
    if kit:
        title = f"{kit.mark} {kit.title}" + (f" · вариант {attempt + 1}" if attempt else "")
    else:
        title = "test reply" if en else "тестовый ответ"
    lines = [f"<b>{escape(model.label)}</b> · {escape(title)}", ""]
    asked = [t.text for t in history if t.author == "user"]
    if asked and not brief and not kit:
        quote = escape(shorten(asked[-1]))
        lines += [
            f"I remember our chat: you asked «{quote}»."
            if en
            else f"Помню наш разговор: вы спрашивали «{quote}».",
            "",
        ]
    if attachment:
        lines.append(escape(SEEN[attachment.kind][en].format(about=attachment.about)))
    if kit:
        topic = shorten(question or (attachment.about if attachment else ""), 80)
        body = escape(
            TOOL_CANNED[key]["en" if en else "ru"].format(
                topic=topic,
                about=attachment.about if attachment else "фото",
                style=STYLES.get(style, style),
            )
        )
    else:
        variants = CANNED[prefs.role][prefs.lang]
        body = variants[(sum(map(ord, question)) + attempt) % len(variants)]
    lines.append(first_sentence(plain_text(body)) if brief else body)
    if prefs.detail == "full" and not brief:
        points = considered(model, prefs, history, tool)
        lines += ["", "<blockquote>" + "\n".join(escape(p) for p in points) + "</blockquote>"]
    html = "\n".join(lines)
    question_tokens = count_tokens(question) + (attachment.tokens if attachment else 0)
    context = count_tokens(prefs.role_prompt) + sum(t.tokens for t in history)
    if kit:
        context += count_tokens(kit.prompt)  # готовый промпт инструмента — тоже вход
    return Reply(html, question_tokens, question_tokens + context, count_tokens(plain_text(html)))


# Токены: бесплатный дневной лимит, сверх него — купленный баланс


def can_answer(used_today: int, balance: int) -> bool:
    return used_today < TEST_DAILY_LIMIT or balance > 0


def paid_part(before: int, after: int) -> int:
    """Сколько токенов из этого ответа пришлось сверх бесплатного лимита."""
    return max(0, after - TEST_DAILY_LIMIT) - max(0, before - TEST_DAILY_LIMIT)


def usage_line(reply: Reply, today_total: int, balance: int = 0) -> str:
    free = (
        f"сегодня {num(min(today_total, TEST_DAILY_LIMIT))} из {num(TEST_DAILY_LIMIT)} бесплатных"
    )
    paid = f" · баланс {num(balance)}" if balance or today_total > TEST_DAILY_LIMIT else ""
    return (
        f"<i>↳ {plural(reply.total, 'токен', 'токена', 'токенов')} · "
        f"вход {num(reply.input_tokens)} · ответ {num(reply.output_tokens)} · {free}{paid}</i>"
    )


def limit_notices(before: int, after: int, balance: int = 0, spent_paid: int = 0) -> list[str]:
    """Предупреждения, если этот ответ перешёл порог бесплатного лимита или съел баланс.
    balance — остаток после ответа, spent_paid — сколько с него списано сейчас."""
    warn = int(TEST_DAILY_LIMIT * WARN_SHARE)
    if before < TEST_DAILY_LIMIT <= after:
        if balance:
            return [
                f"ⓘ Бесплатные {num(TEST_DAILY_LIMIT)} токенов на сегодня закончились. "
                f"Дальше токены списываются с баланса: осталось {num(balance)}."
            ]
        return [limit_reached()]
    if spent_paid and not balance:
        return [BALANCE_EMPTY]
    if before < warn <= after:
        return [
            f"ⓘ Израсходовано {round(WARN_SHARE * 100)}% бесплатного лимита на сегодня: "
            f"{num(after)} из {num(TEST_DAILY_LIMIT)} токенов."
        ]
    return []


def limit_reached() -> str:
    return (
        f"ⓘ Бесплатный лимит на сегодня исчерпан: {num(TEST_DAILY_LIMIT)} токенов. "
        "Он обновится в 03:00 по Москве, а продолжить сразу можно, пополнив баланс."
    )


BALANCE_EMPTY = (
    "ⓘ Купленные токены закончились. Бесплатный лимит обновится в 03:00 по Москве, "
    "или пополните баланс."
)
