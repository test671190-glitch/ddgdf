"""Тестовые ответы: настоящие модели не подключены, бот отвечает заготовками.

Заготовка зависит от роли и языка. Подробный ответ добавляет блок с тем, что учтено,
а с включённой памятью бот вспоминает прошлый вопрос. Токены считаются примерно,
как у настоящих моделей: четыре символа — один токен. Вход — это роль, история
диалога и сам вопрос, выход — текст ответа.
"""

import re
from dataclasses import dataclass
from html import escape, unescape

from models_bot.catalog import ROLES, Model
from models_bot.store import LANGS, Prefs, Turn

TEST_DAILY_LIMIT = 20_000  # тестовых токенов в сутки на пользователя
WARN_SHARE = 0.8  # на этой доле лимита — предупреждение
TYPING_SECONDS = 0.8  # пауза «печатает…» перед ответом

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


@dataclass(frozen=True)
class Reply:
    html: str
    question_tokens: int
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


def plain_text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


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


def compose(model: Model, prefs: Prefs, question: str, history: list[Turn]) -> Reply:
    """history — прошлые сообщения диалога (пусто, если память выключена)."""
    en = prefs.lang == "en"
    variants = CANNED[prefs.role][prefs.lang]
    lines = [f"<b>{escape(model.label)}</b> · {'test reply' if en else 'тестовый ответ'}", ""]
    asked = [t.text for t in history if t.author == "user"]
    if asked:
        quote = escape(shorten(asked[-1]))
        lines += [
            f"I remember our chat: you asked «{quote}»."
            if en
            else f"Помню наш разговор: вы спрашивали «{quote}».",
            "",
        ]
    lines.append(variants[sum(map(ord, question)) % len(variants)])
    if prefs.detail == "full":
        points = considered(model, prefs, history)
        lines += ["", "<blockquote>" + "\n".join(escape(p) for p in points) + "</blockquote>"]
    html = "\n".join(lines)
    question_tokens = count_tokens(question)
    context = count_tokens(prefs.role_prompt) + sum(t.tokens for t in history)
    return Reply(html, question_tokens, question_tokens + context, count_tokens(plain_text(html)))


def usage_line(reply: Reply, today_total: int) -> str:
    return (
        f"<i>↳ {plural(reply.total, 'токен', 'токена', 'токенов')} · "
        f"вход {num(reply.input_tokens)} · ответ {num(reply.output_tokens)} · "
        f"сегодня {num(today_total)} из {num(TEST_DAILY_LIMIT)}</i>"
    )


def limit_notices(before: int, after: int) -> list[str]:
    """Предупреждения, если этот ответ перешёл порог лимита."""
    warn = int(TEST_DAILY_LIMIT * WARN_SHARE)
    if before < TEST_DAILY_LIMIT <= after:
        return [limit_reached()]
    if before < warn <= after:
        return [
            f"ⓘ Израсходовано {round(WARN_SHARE * 100)}% тестового лимита на сегодня: "
            f"{num(after)} из {num(TEST_DAILY_LIMIT)} токенов."
        ]
    return []


def limit_reached() -> str:
    return (
        f"ⓘ Тестовый лимит на сегодня исчерпан: {num(TEST_DAILY_LIMIT)} токенов. "
        "Он обновится в 03:00 по Москве."
    )
