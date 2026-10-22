"""Модели в меню бота: сначала топ (Claude, затем GPT), остальное — за кнопкой
«Остальные модели»."""

from dataclasses import dataclass

# Знак разработчика — монохромный символ, похожий на его логотип:
# ✻ — искра Claude, ⌬ — шестигранник OpenAI, ✧ — четырёхлучевая звезда Gemini
VENDOR_MARKS = {"Anthropic": "✻", "OpenAI": "⌬", "Google": "✧"}


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    vendor: str
    about: str

    @property
    def mark(self) -> str:
        return VENDOR_MARKS.get(self.vendor, "·")

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
    Model("gpt-6-sol", "GPT-6 Sol", "OpenAI", "Флагман OpenAI для сложных задач."),
    Model("gpt-6-astra", "GPT-6 Astra", "OpenAI", "Универсальная модель GPT-6."),
    Model("gpt-6-luna", "GPT-6 Luna", "OpenAI", "Быстрая и экономная модель GPT-6."),
]

OTHER_MODELS = [
    Model("gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI", "Обновлённая версия GPT-6 Sol."),
    Model("gemini-4-argon", "Gemini 4 Argon", "Google", "Новое поколение Gemini."),
    Model("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Google", "Gemini для сложных задач."),
    Model("gemini-3.8-flash", "Gemini 3.8 Flash", "Google", "Быстрая модель Gemini."),
    Model("gemini-3.7-flash", "Gemini 3.7 Flash", "Google", "Быстрая модель Gemini."),
    Model("gemini-3.6-flash", "Gemini 3.6 Flash", "Google", "Быстрая модель Gemini."),
]

MODELS = {m.id: m for m in TOP_MODELS + OTHER_MODELS}
DEFAULT_MODEL = TOP_MODELS[0].id
