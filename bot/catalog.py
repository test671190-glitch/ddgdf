"""Каталог: модели, тарифы и FAQ. Всё, что меняется по бизнесу, правится здесь."""

from dataclasses import dataclass

SHOP_NAME = "unknown shop"


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    vendor: str


TEXT_MODELS: tuple[Model, ...] = (
    Model("gpt-6-astra", "GPT-6 Astra", "OpenAI"),
    Model("gpt-6-sol", "GPT-6 Sol", "OpenAI"),
    Model("gpt-6-luna", "GPT-6 Luna", "OpenAI"),
    Model("gpt-5.6-sol", "GPT-5.6 Sol", "OpenAI"),
    Model("gpt-5.6-terra", "GPT-5.6 Terra", "OpenAI"),
    Model("gpt-5.6-luna", "GPT-5.6 Luna", "OpenAI"),
    Model("claude-opus-5-5", "Claude Opus 5.5", "Anthropic"),
    Model("claude-fable-5-1", "Claude Fable 5.1", "Anthropic"),
    Model("claude-opus-5", "Claude Opus 5", "Anthropic"),
    Model("claude-fable-5", "Claude Fable 5", "Anthropic"),
    Model("claude-opus-4-8", "Claude Opus 4.8", "Anthropic"),
    Model("gemini-3.8-flash", "Gemini 3.8 Flash", "Google"),
    Model("gemini-3.7-flash", "Gemini 3.7 Flash", "Google"),
    Model("gemini-3.6-flash", "Gemini 3.6 Flash", "Google"),
    Model("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Google"),
    Model("grok-4.7", "Grok 4.7", "xAI"),
    Model("gpt-6-sol-pro", "GPT-6 Sol Pro", "OpenAI"),
    Model("gpt-6-luna-pro", "GPT-6 Luna Pro", "OpenAI"),
    Model("qwen3.8-max-prime", "Qwen3.8 Max Prime", "Qwen"),
    Model("glm-5.3-prime", "GLM 5.3 Prime", "Z.ai"),
    Model("gpt-6-astra-pro", "GPT-6 Astra Pro", "OpenAI"),
    Model("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "DeepSeek"),
    Model("glm-5.3-flashx", "GLM 5.3 FlashX", "Z.ai"),
    Model("qwen3.8-omni-flash", "Qwen3.8 Omni Flash", "Qwen"),
    Model("claude-sonnet-5.5", "Claude Sonnet 5.5", "Anthropic"),
    Model("qwen3.8-max-0902", "Qwen3.8 Max (0902)", "Qwen"),
    Model("gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI"),
    Model("gpt-6.1-sol-pro", "GPT-6.1 Sol Pro", "OpenAI"),
    Model("mistral-large-4-0", "Mistral Large 4", "Mistral"),
    Model("claude-haiku-5.5", "Claude Haiku 5.5", "Anthropic"),
    Model("gemini-4-argon", "Gemini 4 Argon", "Google"),
    Model("deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", "DeepSeek"),
)

IMAGE_MODELS: tuple[Model, ...] = (
    Model("nano-banana-pro", "Nano Banana Pro", "Google"),
    Model("nano-banana-2", "Nano Banana 2", "Google"),
    Model("gpt-image-2.5", "GPT Image 2.5", "OpenAI"),
    Model("gpt-image-2", "GPT Image 2", "OpenAI"),
    Model("grok-imagine-image-2.0", "Grok Imagine Image 2.0", "xAI"),
    Model("seedream-5.0-pro", "Seedream 5.0 Pro", "ByteDance"),
    Model("recraft-v4.1", "Recraft V4.1", "Recraft"),
    Model("flux-2", "FLUX.2", "Black Forest Labs"),
)

VIDEO_MODELS: tuple[Model, ...] = (
    Model("seedance-2.5", "Seedance 2.5", "ByteDance"),
    Model("seedance-2.0", "Seedance 2.0", "ByteDance"),
    Model("gemini-omni-flash-1.1", "Gemini Omni Flash 1.1", "Google"),
    Model("veo-3.1", "Veo 3.1", "Google"),
    Model("pixverse-v6", "PixVerse V6", "PixVerse"),
    Model("kling-3.0", "Kling 3.0", "Kuaishou"),
    Model("kling-2.6", "Kling 2.6", "Kuaishou"),
    Model("kling-3.0-motion-control", "Kling 3.0 Motion Control", "Kuaishou"),
    Model("kling-2.6-motion-control", "Kling 2.6 Motion Control", "Kuaishou"),
)

# code -> (заголовок, модели)
MODEL_CATEGORIES: dict[str, tuple[str, tuple[Model, ...]]] = {
    "text": ("Текстовые модели", TEXT_MODELS),
    "image": ("Изображения", IMAGE_MODELS),
    "video": ("Видео", VIDEO_MODELS),
}

TOTAL_MODELS = len(TEXT_MODELS) + len(IMAGE_MODELS) + len(VIDEO_MODELS)

# Допустимые reasoning_effort для текстовых моделей и значение по умолчанию (из документации API)
_GPT = ("none", "low", "medium", "high", "xhigh", "max")
_CLAUDE = ("low", "medium", "high", "xhigh", "max")
_STD = ("low", "medium", "high")
REASONING: dict[str, tuple[tuple[str, ...], str]] = {
    "gpt-6-astra": (_CLAUDE, "medium"),
    "gpt-6-sol": (_GPT, "medium"),
    "gpt-6-luna": (_GPT, "medium"),
    "gpt-5.6-sol": (_GPT, "medium"),
    "gpt-5.6-terra": (_GPT, "medium"),
    "gpt-5.6-luna": (_GPT, "medium"),
    "claude-opus-5-5": (_CLAUDE, "medium"),
    "claude-fable-5-1": (_CLAUDE, "high"),
    "claude-opus-5": (_GPT, "high"),
    "claude-fable-5": (_CLAUDE, "high"),
    "claude-opus-4-8": (_GPT, "none"),
    "gemini-3.8-flash": (_STD, "medium"),
    "gemini-3.7-flash": (_STD, "medium"),
    "gemini-3.6-flash": (("minimal", *_STD), "medium"),
    "gemini-3.1-pro-preview": (_STD, "high"),
    "grok-4.7": (_STD, "medium"),
    "gpt-6-sol-pro": (_GPT, "medium"),
    "gpt-6-luna-pro": (_GPT, "medium"),
    "qwen3.8-max-prime": (_STD, "medium"),
    "glm-5.3-prime": (_STD, "medium"),
    "gpt-6-astra-pro": (_GPT, "medium"),
    "deepseek-v4.1-flash": (_STD, "medium"),
    "glm-5.3-flashx": (_STD, "medium"),
    "qwen3.8-omni-flash": (_STD, "medium"),
    "claude-sonnet-5.5": (_CLAUDE, "medium"),
    "qwen3.8-max-0902": (_STD, "medium"),
    "gpt-6.1-sol": (_GPT, "medium"),
    "gpt-6.1-sol-pro": (_GPT, "medium"),
    "mistral-large-4-0": (_STD, "medium"),
    "claude-haiku-5.5": (_CLAUDE, "medium"),
    "gemini-4-argon": (_STD, "medium"),
    "deepseek-v4.1-flash-1017": (_STD, "medium"),
}


@dataclass(frozen=True)
class Tariff:
    code: str
    title: str
    price_usd: int
    features: tuple[str, ...]
    # False — тариф не продаётся (нужен чат, которого пока нет), но действующие подписки работают
    on_sale: bool = True
    emoji: str = "💎"
    tagline: str = ""

    def stars(self, stars_per_usd: float) -> int:
        return max(1, round(self.price_usd * stars_per_usd))


TARIFFS: dict[str, Tariff] = {
    t.code: t
    for t in (
        Tariff(
            "start",
            "Start",
            18,
            (
                "Безлимит на генерацию изображений",
                "Текстовые модели с дневным лимитом",
                "Видео с месячным лимитом",
                "История чатов",
            ),
            on_sale=False,
        ),
        Tariff(
            "creator",
            "Creator",
            25,
            (
                "Безлимит на генерацию изображений",
                "Безлимит на текстовые модели 10 часов в день",
                "Видео с месячным лимитом",
                "История чатов",
            ),
            on_sale=False,
        ),
        Tariff(
            "pro",
            "Pro",
            33,
            (
                "Безлимит на генерацию изображений",
                "Безлимит на текстовые модели 15 часов в день",
                "Безлимит на видео 10 часов в день",
                "Доступ к API",
            ),
            emoji="⚡️",
            tagline="Для ежедневной работы в IDE и скриптах",
        ),
        Tariff(
            "unlimited",
            "Unlimited",
            80,
            (
                "Полный безлимит на все модели",
                "Текст, изображения и видео круглосуточно",
                "Безлимитный доступ к API",
            ),
            emoji="👑",
            tagline="Без ограничений, 24/7",
        ),
    )
}

TARIFFS_ON_SALE: dict[str, Tariff] = {c: t for c, t in TARIFFS.items() if t.on_sale}

# Одновременных запросов на один ключ через эндпоинт магазина (gateway.py), сверх — ответ 429
MAX_CONCURRENT_REQUESTS = 20
# Запросов в секунду с одного IP-адреса (gateway.py), сверх — ответ 429
MAX_REQUESTS_PER_SECOND = 100


FAQ: tuple[tuple[str, str], ...] = (
    (
        "Какие модели доступны?",
        "Текстовые модели GPT-6, GPT-5.6, Claude Opus и Fable, Gemini, модели для изображений "
        "Nano Banana, GPT Image, Grok Imagine, Seedream, Recraft и FLUX.2, а для видео Seedance, "
        "Veo 3.1, Gemini Omni, PixVerse и Kling. Полный список с ID для API — в разделе «Модели»",
    ),
    (
        "В чём разница тарифов?",
        "Оба тарифа открывают через API все модели для текста, изображений и видео, "
        f"отличаются лимиты. Pro (${TARIFFS['pro'].price_usd}): изображения без ограничений, "
        "безлимит на текст 15 часов в день и на видео 10 часов в день. "
        f"Unlimited (${TARIFFS['unlimited'].price_usd}): полный безлимит на все модели "
        "круглосуточно",
    ),
    (
        "Действительно ли на тарифе Unlimited безлимит?",
        "Да, так и есть. На тарифе Unlimited нет ограничений на текст, изображения и видео, "
        "в том числе на запросы через API",
    ),
    (
        "Почему у вас безлимитные тарифы, если у других есть лимиты?",
        "Другие сервисы стремятся получить максимальную выгоду. Мы же стремимся расширить "
        "клиентскую базу, поэтому сейчас предлагаем безлимитные тарифы: так мы собираем "
        "сообщество и постоянных клиентов. Можете считать это нашей рекламной акцией",
    ),
    (
        "Есть ли ограничения на запросы в минуту или секунду?",
        f"С одного адреса — до {MAX_REQUESTS_PER_SECOND} запросов в секунду, одновременно на "
        f"один ключ обрабатывается до {MAX_CONCURRENT_REQUESTS} запросов. Если превысить, лишние "
        "запросы получат ответ 429 — их можно сразу повторить. Ограничений по числу запросов "
        "в минуту или в день нет",
    ),
    (
        "Какие нейросети под капотом текстовых моделей?",
        "Понимаем, откуда вопрос: недобросовестные агрегаторы заявляют популярные нейросети "
        "вроде GPT и Claude, а на деле отвечают китайские модели. У нас подмены нет, и это легко "
        "проверить: спросите модель, кто она, или измените промпт, чтобы добиться честного "
        "ответа. Вы всегда получите ответ именно от выбранной модели. Разницу видно и по "
        "качеству работы",
    ),
    (
        "Как работает доступ по тарифу?",
        "Для отправки запросов нужен активный тариф. Его статус и срок действия всегда видны "
        "в разделе «Профиль» этого бота",
    ),
    (
        "Можно ли передавать картинки и видео?",
        "Да. Модели изображений и видео принимают референсы в параметре images: до 16 картинок "
        "у GPT Image, до 14 у Nano Banana, до 5 у Grok. Для видео можно задать первый и "
        "последний кадр, а для Motion Control нужны фото персонажа и ролик с движением",
    ),
    (
        "Где хранится история диалогов?",
        "В API история передаётся в параметре messages при каждом запросе, поэтому храните её "
        "на своей стороне. IDE и чат-клиенты вроде Cursor, Cline или Claude Code делают это сами",
    ),
    (
        "Какие параметры можно настроить?",
        "Параметры у каждой модели свои, как у провайдера. Для текста: уровень рассуждения, "
        "веб-поиск, а у GPT ещё длина ответа. Для изображений: формат, разрешение до 4K, "
        "количество, у GPT Image и Grok качество, у Recraft вектор SVG. Для видео: формат, "
        "длительность до 30 секунд, разрешение до 4K и звук",
    ),
    (
        "Есть ли API?",
        "Да, документация уже опубликована. API можно полностью использовать в IDE и других "
        "программах, например в Cursor и Claude Code, или отправлять запросы через cURL и "
        f"работать с моделями {SHOP_NAME} напрямую. Запросы идут в рамках купленного тарифа, "
        "поэтому покупать отдельные кредиты для API, как в других сервисах, не нужно. API "
        "входит во все тарифы",
    ),
    (
        "Как отменить подписку?",
        "Подписка в боте оплачивается на 30 дней и не продлевается автоматически, поэтому "
        "отменять ничего не нужно: доступ сохраняется до конца оплаченного периода. За 3 дня "
        "до окончания бот напомнит о продлении",
    ),
    (
        "Как связаться с поддержкой?",
        "В боте откройте «Поддержка» и нажмите «Написать в поддержку». Опишите вопрос, "
        "можно со скриншотами. Ответ придёт в чат с ботом",
    ),
)

# Короткие подписи для кнопок FAQ в боте: длинный вопрос Telegram обрезает на телефоне
FAQ_BUTTONS = {
    "Действительно ли на тарифе Unlimited безлимит?": "Unlimited правда безлимит?",
    "Почему у вас безлимитные тарифы, если у других есть лимиты?": "Почему безлимит?",
    "Есть ли ограничения на запросы в минуту или секунду?": "Лимиты на запросы",
    "Какие нейросети под капотом текстовых моделей?": "Нет ли подмены моделей?",
    "Можно ли передавать картинки и видео?": "Картинки и видео в запросах",
}
