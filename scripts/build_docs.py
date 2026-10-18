"""Собирает сайт: «Главное» (тарифы + FAQ) и «API» (документация).

Исходники:
  docs/pages/home.html, docs/pages/api.html  — страницы
  docs/partials/*.html                         — общие части (стили, 3D-фон, подвал, скрипты)

Метки в страницах:
  <!-- @include partials/x.html -->  — вставить файл
  <!-- @header home|api -->          — шапка с активной вкладкой
  <!-- @plans -->                    — карточки тарифов из bot/catalog.py (кнопки ведут в бота)
  <!-- @faq -->                      — вопросы и ответы из bot/catalog.py

Результат:
  docs/dist/index.html, docs/dist/api.html     — полные HTML-документы для Netlify
  docs/preview/index.html, docs/preview/api.html — то же без <html>/<head> (для превью)

Запуск: .venv\\Scripts\\python.exe scripts/build_docs.py
"""

import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.catalog import FAQ, TARIFFS_ON_SALE  # noqa: E402

DOCS = ROOT / "docs"
DIST = DOCS / "dist"
PREVIEW = DOCS / "preview"
PAGES = {"home": "index.html", "api": "api.html"}

BOT_URL = "https://t.me/UnknownAiShopBot"
FEATURED = "pro"
ICONS = {
    "pro": (
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.6 2.2 5 13.1c-.3.4 0 1 .5 1h5.3'
        'l-1.5 7.4c-.1.6.6.9 1 .4l8.6-10.9c.3-.4 0-1-.5-1h-5.3l1.5-7.4c.1-.6-.6-.9-1-.4Z"/></svg>'
    ),
    "unlimited": (
        '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.2 8.3c-.2-.7.6-1.2 1.2-.8l3.9 2.9 '
        "2.9-5c.4-.6 1.2-.6 1.6 0l2.9 5 3.9-2.9c.6-.4 1.4.1 1.2.8l-1.9 8.6c-.1.4-.5.7-.9.7H6c-.4 0-.8"
        '-.3-.9-.7L3.2 8.3Z"/><rect x="5.5" y="19" width="13" height="2.2" rx="1.1"/></svg>'
    ),
}
# Ответы FAQ написаны для бота; на сайте часть формулировок указывает на страницы сайта
FAQ_SITE_FIXES = {
    "в разделе «Модели»": 'в разделе <a href="api.html#models">Модели</a>',
    "в разделе «Профиль» этого бота": (
        f'в <a href="{BOT_URL}" target="_blank" rel="noopener">Telegram-боте</a>, раздел «Профиль»'
    ),
    "документация уже опубликована": 'документация на странице <a href="api.html">API</a>',
}

HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:dark}body{margin:0}[hidden]{display:none!important}</style>
"""


def header(active: str) -> str:
    def tab(key: str, href: str, label: str) -> str:
        current = ' aria-current="page"' if key == active else ""
        return f'<a href="{href}"{current}>{label}</a>'

    return f"""<header class="topbar">
  <div class="bar glass">
    <a class="wordmark" href="./"><span class="redact">unknown</span> shop</a>
    <nav class="topnav" aria-label="Страницы">
      {tab("home", "./", "Главное")}
      {tab("api", "api.html", "API")}
    </nav>
    <a class="pill-btn" href="{BOT_URL}" target="_blank" rel="noopener">↗ <span>Telegram-бот</span></a>
  </div>
</header>"""


def plans() -> str:
    cards = []
    for t in TARIFFS_ON_SALE.values():
        classes = ["plan", "glass", "tilt"]
        if t.code == FEATURED:
            classes.append("featured")
        if t.code != FEATURED:
            classes.append("gold")
        badge = ' <span class="badge">Популярный</span>' if t.code == FEATURED else ""
        icon = ICONS.get(t.code, "")
        features = "\n".join(f"          <li>{escape(f)}</li>" for f in t.features)
        cards.append(f"""        <div class="{" ".join(classes)}">
          <h3><span class="plan-ico" aria-hidden="true">{icon}</span>{escape(t.title)}{badge}</h3>
          <div class="price">${t.price_usd} <small>/ 30 дней</small></div>
          <ul>
{features}
          </ul>
          <a class="pill-btn buy" href="{BOT_URL}?start=buy_{t.code}" target="_blank" rel="noopener">Купить {escape(t.title)} в боте ↗</a>
        </div>""")
    return '      <div class="plans">\n' + "\n".join(cards) + "\n      </div>"


def faq() -> str:
    items = []
    for i, (question, answer) in enumerate(FAQ):
        text = escape(answer)
        for old, new in FAQ_SITE_FIXES.items():
            text = text.replace(escape(old), new)
        opened = " open" if i == 0 else ""
        items.append(
            f'      <details class="tool faq"{opened}>\n'
            f"        <summary>{escape(question)}</summary>\n"
            f'        <div class="body"><p>{text}.</p></div>\n'
            f"      </details>"
        )
    return "\n".join(items)


def render(name: str) -> str:
    html = (DOCS / "pages" / f"{name}.html").read_text(encoding="utf-8")
    html = re.sub(
        r"<!-- @include (\S+) -->",
        lambda m: (DOCS / m.group(1)).read_text(encoding="utf-8").rstrip("\n"),
        html,
    )
    html = re.sub(r"<!-- @header (\w+) -->", lambda m: header(m.group(1)), html)
    html = html.replace("<!-- @plans -->", plans())
    html = html.replace("<!-- @faq -->", faq())
    leftover = re.findall(r"<!-- @\w+[^>]*-->", html)
    if leftover:
        raise SystemExit(f"Необработанные метки в {name}: {leftover}")
    return html


def standalone(body: str) -> str:
    head_part, _, rest = body.partition("</style>")
    return f"{HEAD}{head_part}</style>\n</head>\n<body>\n{rest.lstrip()}\n</body>\n</html>\n"


def build() -> list[Path]:
    DIST.mkdir(parents=True, exist_ok=True)
    PREVIEW.mkdir(parents=True, exist_ok=True)
    out = []
    for name, filename in PAGES.items():
        body = render(name)
        (PREVIEW / filename).write_text(body, encoding="utf-8")
        target = DIST / filename
        target.write_text(standalone(body), encoding="utf-8")
        out.append(target)
    return out


if __name__ == "__main__":
    for path in build():
        print(f"Готово: {path.relative_to(ROOT)}")
