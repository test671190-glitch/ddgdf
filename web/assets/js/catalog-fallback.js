// Fallback catalog: used when /api/catalog is unreachable (e.g. the folder is served as plain static files).
// Shape mirrors GET /api/catalog exactly.

const GPT = ["none", "low", "medium", "high", "xhigh", "max"];
const CLAUDE = ["low", "medium", "high", "xhigh", "max"];
const CLAUDE_NONE = ["none", "low", "medium", "high", "xhigh", "max"];
const GEMINI = ["low", "medium", "high"];
const GEMINI_MIN = ["minimal", "low", "medium", "high"];
const OTHER = ["low", "medium", "high"];

const t = (id, name, vendor, reasoning, def = "medium") => ({
  id, name, vendor, type: "text", reasoning, default_reasoning: def,
});
const m = (type) => (id, name, vendor) => ({ id, name, vendor, type, reasoning: null, default_reasoning: null });
const img = m("image");
const vid = m("video");

export const FALLBACK_CATALOG = Object.freeze({
  shop_name: "unknown shop",
  bot_username: "UnknownAiShopBot",
  api_base_url: "https://154-83-140-29.sslip.io/v1",
  subscription_days: 30,
  models: [
    t("gpt-6-astra", "GPT-6 Astra", "OpenAI", CLAUDE),
    t("gpt-6-sol", "GPT-6 Sol", "OpenAI", GPT),
    t("gpt-6-luna", "GPT-6 Luna", "OpenAI", GPT),
    t("gpt-5.6-sol", "GPT-5.6 Sol", "OpenAI", GPT),
    t("gpt-5.6-terra", "GPT-5.6 Terra", "OpenAI", GPT),
    t("gpt-5.6-luna", "GPT-5.6 Luna", "OpenAI", GPT),
    t("claude-opus-5-5", "Claude Opus 5.5", "Anthropic", CLAUDE),
    t("claude-fable-5-1", "Claude Fable 5.1", "Anthropic", CLAUDE, "high"),
    t("claude-opus-5", "Claude Opus 5", "Anthropic", CLAUDE_NONE, "high"),
    t("claude-fable-5", "Claude Fable 5", "Anthropic", CLAUDE, "high"),
    t("claude-opus-4-8", "Claude Opus 4.8", "Anthropic", CLAUDE_NONE, "none"),
    t("gemini-3.8-flash", "Gemini 3.8 Flash", "Google", GEMINI),
    t("gemini-3.7-flash", "Gemini 3.7 Flash", "Google", GEMINI),
    t("gemini-3.6-flash", "Gemini 3.6 Flash", "Google", GEMINI_MIN),
    t("gemini-3.1-pro-preview", "Gemini 3.1 Pro", "Google", GEMINI, "high"),
    t("grok-4.7", "Grok 4.7", "xAI", OTHER),
    t("gpt-6-sol-pro", "GPT-6 Sol Pro", "OpenAI", GPT),
    t("gpt-6-luna-pro", "GPT-6 Luna Pro", "OpenAI", GPT),
    t("qwen3.8-max-prime", "Qwen3.8 Max Prime", "Qwen", OTHER),
    t("glm-5.3-prime", "GLM 5.3 Prime", "Z.ai", OTHER),
    t("gpt-6-astra-pro", "GPT-6 Astra Pro", "OpenAI", GPT),
    t("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "DeepSeek", OTHER),
    t("glm-5.3-flashx", "GLM 5.3 FlashX", "Z.ai", OTHER),
    t("qwen3.8-omni-flash", "Qwen3.8 Omni Flash", "Qwen", OTHER),
    t("claude-sonnet-5.5", "Claude Sonnet 5.5", "Anthropic", CLAUDE),
    t("qwen3.8-max-0902", "Qwen3.8 Max (0902)", "Qwen", OTHER),
    t("gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI", GPT),
    t("gpt-6.1-sol-pro", "GPT-6.1 Sol Pro", "OpenAI", GPT),
    t("mistral-large-4-0", "Mistral Large 4", "Mistral", OTHER),
    t("claude-haiku-5.5", "Claude Haiku 5.5", "Anthropic", CLAUDE),
    t("gemini-4-argon", "Gemini 4 Argon", "Google", GEMINI),
    t("deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", "DeepSeek", OTHER),

    img("nano-banana-pro", "Nano Banana Pro", "Google"),
    img("nano-banana-2", "Nano Banana 2", "Google"),
    img("gpt-image-2.5", "GPT Image 2.5", "OpenAI"),
    img("gpt-image-2", "GPT Image 2", "OpenAI"),
    img("grok-imagine-image-2.0", "Grok Imagine Image 2.0", "xAI"),
    img("seedream-5.0-pro", "Seedream 5.0 Pro", "ByteDance"),
    img("recraft-v4.1", "Recraft V4.1", "Recraft"),
    img("flux-2", "FLUX.2", "Black Forest Labs"),

    vid("seedance-2.5", "Seedance 2.5", "ByteDance"),
    vid("seedance-2.0", "Seedance 2.0", "ByteDance"),
    vid("gemini-omni-flash-1.1", "Gemini Omni Flash 1.1", "Google"),
    vid("veo-3.1", "Veo 3.1", "Google"),
    vid("pixverse-v6", "PixVerse V6", "PixVerse"),
    vid("kling-3.0", "Kling 3.0", "Kuaishou"),
    vid("kling-2.6", "Kling 2.6", "Kuaishou"),
    vid("kling-3.0-motion-control", "Kling 3.0 Motion Control", "Kuaishou"),
    vid("kling-2.6-motion-control", "Kling 2.6 Motion Control", "Kuaishou"),
  ],
  tariffs: [
    {
      code: "pro",
      title: "Pro",
      emoji: "⚡️",
      tagline: "Для ежедневной работы в IDE и скриптах",
      price_usd: 50,
      stars: null,
      features: [
        "Безлимит на генерацию изображений",
        "Безлимит на текстовые модели 15 часов в день",
        "Безлимит на видео 10 часов в день",
        "Доступ к API",
      ],
      popular: true,
    },
    {
      code: "unlimited",
      title: "Unlimited",
      emoji: "👑",
      tagline: "Без ограничений, 24/7",
      price_usd: 100,
      stars: null,
      features: [
        "Полный безлимит на все модели",
        "Текст, изображения и видео круглосуточно",
        "Безлимитный доступ к API",
      ],
      popular: false,
    },
  ],
  faq: [
    {
      q: "Что входит в подписку?",
      a: "Все 49 моделей: текстовые, для изображений и для видео. Пользоваться ими можно в веб-чате и через API с одним ключом.",
    },
    {
      q: "Чем отличаются тарифы Pro и Unlimited?",
      a: "Набор моделей одинаковый, разница в лимитах. Pro даёт безлимит на изображения, 15 часов в день на текст и 10 часов на видео. Unlimited снимает ограничения полностью.",
    },
    {
      q: "Как оплатить?",
      a: "Криптовалютой прямо на сайте или Telegram Stars через нашего бота. Подписка действует 30 дней и сама не продлевается.",
    },
    {
      q: "Где хранится история чатов?",
      a: "Только в вашем браузере. Мы не сохраняем переписку на сервере, поэтому при смене устройства история не переносится.",
    },
  ],
});
