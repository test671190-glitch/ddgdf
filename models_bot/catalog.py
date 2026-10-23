"""Модели в меню бота и готовые роли.

Модели тестовые: настоящий API не подключён, отвечают заготовками (answers.py).
Сначала топ (Claude, затем GPT), остальное — за кнопкой «Остальные модели».
Новую модель достаточно добавить в список: после перезапуска бот сообщит о ней
тем, у кого включены уведомления о новых моделях.

Записи — namedtuple, а не dataclass: модуль dataclasses стоит ~2 МБ памяти.
"""

from collections import namedtuple

# Знак разработчика — монохромный символ, похожий на его логотип:
# ✻ — искра Claude, ⌬ — шестигранник OpenAI, ✧ — четырёхлучевая звезда Gemini
VENDOR_MARKS = {"Anthropic": "✻", "OpenAI": "⌬", "Google": "✧"}

# Что модель понимает кроме текста
INPUTS = {"image": "фото", "file": "PDF и документы", "voice": "голосовые"}
VENDOR_INPUTS = {
    "Anthropic": ("image", "file"),
    "OpenAI": ("image", "file", "voice"),
    "Google": ("image", "file", "voice"),
}


class Model(namedtuple("Model", "id name vendor about")):
    __slots__ = ()

    @property
    def mark(self) -> str:
        return VENDOR_MARKS.get(self.vendor, "·")

    @property
    def inputs(self) -> tuple[str, ...]:
        return VENDOR_INPUTS.get(self.vendor, ())

    @property
    def label(self) -> str:
        return f"{self.mark} {self.name}"


TOP_MODELS = [
    Model(
        "claude-opus-5-5",
        "Claude Opus 5.5",
        "Anthropic",
        "Самая сильная модель Claude: код, анализ, длинные задачи.",
    ),
    Model(
        "claude-sonnet-5.5",
        "Claude Sonnet 5.5",
        "Anthropic",
        "Баланс скорости и качества на каждый день.",
    ),
    Model("claude-haiku-5.5", "Claude Haiku 5.5", "Anthropic", "Быстрая и лёгкая модель Claude."),
    Model("gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI", "Новый флагман OpenAI для сложных задач."),
    Model("gpt-6-astra", "GPT-6 Astra", "OpenAI", "Универсальная модель GPT-6."),
    Model("gpt-6-luna", "GPT-6 Luna", "OpenAI", "Быстрая и экономная модель GPT-6."),
]

OTHER_MODELS = [
    Model("gpt-6-sol", "GPT-6 Sol", "OpenAI", "Прошлый флагман OpenAI, до GPT-6.1 Sol."),
    Model("gemini-4-argon", "Gemini 4 Argon", "Google", "Новое поколение Gemini."),
    Model("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Google", "Gemini для сложных задач."),
    Model("gemini-3.8-flash", "Gemini 3.8 Flash", "Google", "Быстрая модель Gemini."),
    Model("gemini-3.7-flash", "Gemini 3.7 Flash", "Google", "Быстрая модель Gemini."),
    Model("gemini-3.6-flash", "Gemini 3.6 Flash", "Google", "Быстрая модель Gemini."),
]

MODELS = {m.id: m for m in TOP_MODELS + OTHER_MODELS}
DEFAULT_MODEL = TOP_MODELS[0].id


# Роль — только своя: инструкция, которую пользователь пишет сам (системный промпт)
NO_ROLE = "none"
CUSTOM_ROLE = "custom"
ROLE_MAX_LENGTH = 500
# Готовые роли прошлых версий: у кого они были выбраны, они становятся своей ролью
LEGACY_ROLES = {
    "lawyer": "Отвечай как юрист: ссылайся на нормы закона и предупреждай о рисках.",
    "coder": "Отвечай как опытный программист: кратко и с примерами кода.",
    "translator": "Переводи точно, сохраняя смысл и стиль исходного текста.",
    "marketer": "Отвечай как маркетолог: предлагай идеи, заголовки и рекламные тексты.",
    "editor": "Редактируй текст: исправляй ошибки и делай его понятнее.",
}

# Инструменты: готовые задачи для текущей модели и чата.
# needs — что прислать: text — текст, photo — фото, text_or_file — текст или документ
Tool = namedtuple("Tool", "key mark title needs ask prompt")
TOOLS = {
    t.key: t
    for t in [
        Tool(
            "styles",
            "◐",
            "Фотостили",
            "photo",
            "Пришлите фото — сделаю его в стиле «{style}».",
            "Перерисуй фото в заданном стиле, сохранив композицию и узнаваемость.",
        ),
        Tool(
            "draw",
            "▣",
            "Нарисовать",
            "text",
            "Опишите картинку одним сообщением: что на ней, в каком стиле, какие цвета.",
            "Нарисуй изображение 1024×1024 по описанию пользователя.",
        ),
        Tool(
            "edit",
            "◩",
            "Изменить фото",
            "photo",
            "Пришлите фото, а в подписи напишите, что в нём изменить.",
            "Отредактируй фото по просьбе пользователя, остальное оставь как есть.",
        ),
        Tool(
            "brainstorm",
            "✶",
            "Брейншторм",
            "text",
            "Напишите тему или задачу — предложу идеи.",
            "Предложи 5–7 разных и конкретных идей по теме, от простых к смелым.",
        ),
        Tool(
            "explain",
            "◎",
            "Объяснить тему",
            "text",
            "Какую тему объяснить? Можно добавить, для кого: «для школьника».",
            "Объясни тему простыми словами, по шагам, с примером и аналогией.",
        ),
        Tool(
            "rewrite",
            "✐",
            "Переписать текст",
            "text",
            "Пришлите текст — перепишу его яснее и без ошибок.",
            "Перепиши текст: короче, яснее, без ошибок, сохранив смысл и тон.",
        ),
        Tool(
            "summary",
            "≡",
            "Пересказ",
            "text_or_file",
            "Пришлите текст или PDF — перескажу главное.",
            "Перескажи главное в 3–5 пунктах, без воды.",
        ),
    ]
}
STYLES = {
    "anime": "аниме",
    "oil": "масляная живопись",
    "pencil": "карандашный рисунок",
    "watercolor": "акварель",
    "cyberpunk": "киберпанк",
    "pixel": "пиксель-арт",
}
# Пакеты токенов за Telegram Stars
Pack = namedtuple("Pack", "key tokens stars")
PACKS = {
    p.key: p for p in [Pack("s", 100_000, 50), Pack("m", 500_000, 200), Pack("l", 2_000_000, 700)]
}
