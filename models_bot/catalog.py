"""Модели в меню бота: сначала топ (Claude, затем GPT),
остальное — за кнопкой «Остальные модели»."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    vendor: str
    about: str = ""


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
    Model("claude-fable-5-1", "Claude Fable 5.1", "Anthropic"),
    Model("claude-opus-5", "Claude Opus 5", "Anthropic"),
    Model("claude-opus-4-8", "Claude Opus 4.8", "Anthropic"),
    Model("gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI"),
    Model("gpt-6-sol-pro", "GPT-6 Sol Pro", "OpenAI"),
    Model("gpt-5.6-sol", "GPT-5.6 Sol", "OpenAI"),
    Model("gemini-4-argon", "Gemini 4 Argon", "Google"),
    Model("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Google"),
    Model("gemini-3.8-flash", "Gemini 3.8 Flash", "Google"),
    Model("grok-4.7", "Grok 4.7", "xAI"),
    Model("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "DeepSeek"),
    Model("qwen3.8-max-prime", "Qwen3.8 Max Prime", "Qwen"),
    Model("glm-5.3-prime", "GLM 5.3 Prime", "Z.ai"),
    Model("mistral-large-4-0", "Mistral Large 4", "Mistral"),
]

MODELS = {m.id: m for m in TOP_MODELS + OTHER_MODELS}
