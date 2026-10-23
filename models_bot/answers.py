"""Тестовые ответы: настоящие модели не подключены, бот отвечает заготовками.

Заготовка зависит от роли и языка. Подробный ответ добавляет блок с тем, что учтено,
а с включённой памятью бот вспоминает прошлый вопрос. Токены считаются примерно,
как у настоящих моделей: четыре символа — один токен. Вход — это роль, история
диалога и сам вопрос, выход — текст ответа.
"""

import re
from collections import namedtuple

from models_bot.catalog import ROLES, Model
from models_bot.store import LANGS, Prefs, Turn

TEST_DAILY_LIMIT = 20_000  # бесплатных токенов в сутки на пользователя
WARN_SHARE = 0.8  # на этой доле лимита — предупреждение
TYPING_SECONDS = 0.4  # пауза «печатает…» перед первым куском ответа
STREAM_STEPS = 3  # ответ появляется по частям: столько правок сообщения
STREAM_DELAY = 0.5  # секунд между частями: Telegram не любит правки чаще

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
    "lawyer": {
        "ru": [
            "С юридической точки зрения ответ зависит от деталей: договора, сроков и сторон. "
            "В рабочем режиме модель в этой роли сошлётся на нормы закона и отметит риски.",
        ],
        "en": [
            "Legally, the answer depends on the details: the contract, deadlines and parties. "
            "In live mode the model in this role will cite the law and point out the risks.",
        ],
    },
    "coder": {
        "ru": [
            "Набросок решения:\n"
            '<pre><code class="language-python">def solve(task: str) -&gt; str:\n'
            "    # здесь будет код от модели\n"
            '    return "готово"</code></pre>\n'
            "В роли программиста модель отвечает кратко и с примерами кода.",
        ],
        "en": [
            "A sketch of the solution:\n"
            '<pre><code class="language-python">def solve(task: str) -&gt; str:\n'
            "    # the model's code will be here\n"
            '    return "done"</code></pre>\n'
            "As a developer, the model answers briefly and with code examples.",
        ],
    },
    "translator": {
        "ru": [
            "Здесь появится перевод. В роли переводчика модель сохраняет смысл и стиль "
            "оригинала и отмечает спорные места.",
        ],
        "en": [
            "The translation will appear here. As a translator, the model keeps the meaning "
            "and style of the original and marks ambiguous spots.",
        ],
    },
    "marketer": {
        "ru": [
            "Три варианта заголовка:\n"
            "1. Нейросети без лишних шагов\n"
            "2. Claude и GPT в одном окне\n"
            "3. Спросите — и получите ответ\n"
            "В рабочем режиме модель подберёт идеи под ваш продукт.",
        ],
        "en": [
            "Three headline options:\n"
            "1. AI without extra steps\n"
            "2. Claude and GPT in one place\n"
            "3. Ask and get an answer\n"
            "In live mode the model will tailor ideas to your product.",
        ],
    },
    "editor": {
        "ru": [
            "Здесь будет отредактированный текст: без ошибок, короче и понятнее. В рабочем "
            "режиме модель покажет, что именно изменила.",
        ],
        "en": [
            "The edited text will be here: no mistakes, shorter and clearer. In live mode the "
            "model will show exactly what it changed.",
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


def considered(model: Model, prefs: Prefs, history: list[Turn]) -> list[str]:
    """Что учтено в ответе: показывает, что настройки работают."""
    if prefs.lang == "en":
        role = ROLES[prefs.role].title_en if prefs.role in ROLES else "custom"
        if not prefs.memory:
            memory = "each question on its own"
        elif history:
            memory = f"{len(history)} messages in context"
        else:
            memory = "new chat"
        return [
            f"Model · {model.name}",
            f"Role · {role}",
            f"Memory · {memory}",
            "Language · English",
        ]
    if not prefs.memory:
        memory = "каждый вопрос отдельно"
    elif history:
        memory = "в контексте " + plural(len(history), "сообщение", "сообщения", "сообщений")
    else:
        memory = "новый диалог"
    return [
        f"Модель · {model.name}",
        f"Роль · {prefs.role_title.lower()}",
        f"Память · {memory}",
        f"Язык · {LANGS[prefs.lang]}",
    ]


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
) -> Reply:
    """history — прошлые сообщения диалога (пусто, если память выключена).
    attempt — номер перегенерации: другая заготовка; brief — «короче»: одна фраза."""
    en = prefs.lang == "en"
    variants = CANNED[prefs.role][prefs.lang]
    lines = [f"<b>{escape(model.label)}</b> · {'test reply' if en else 'тестовый ответ'}", ""]
    asked = [t.text for t in history if t.author == "user"]
    if asked and not brief:
        quote = escape(shorten(asked[-1]))
        lines += [
            f"I remember our chat: you asked «{quote}»."
            if en
            else f"Помню наш разговор: вы спрашивали «{quote}».",
            "",
        ]
    if attachment:
        lines.append(escape(SEEN[attachment.kind][en].format(about=attachment.about)))
    body = variants[(sum(map(ord, question)) + attempt) % len(variants)]
    lines.append(first_sentence(plain_text(body)) if brief else body)
    if prefs.detail == "full" and not brief:
        points = considered(model, prefs, history)
        lines += ["", "<blockquote>" + "\n".join(escape(p) for p in points) + "</blockquote>"]
    html = "\n".join(lines)
    question_tokens = count_tokens(question) + (attachment.tokens if attachment else 0)
    context = count_tokens(prefs.role_prompt) + sum(t.tokens for t in history)
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
