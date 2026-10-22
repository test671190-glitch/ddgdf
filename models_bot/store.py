"""Настройки пользователей в SQLite: основная модель, язык и подробность ответов."""

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from models_bot.catalog import DEFAULT_MODEL, MODELS

LANGS = {"ru": "русский", "en": "английский"}
DETAILS = {"full": "подробные", "short": "краткие"}


@dataclass(frozen=True)
class Prefs:
    model: str = DEFAULT_MODEL
    lang: str = "ru"
    detail: str = "full"


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS prefs ("
            "user_id INTEGER PRIMARY KEY, model TEXT, lang TEXT, detail TEXT)"
        )
        self.db.commit()

    def get(self, user_id: int) -> Prefs:
        row = self.db.execute(
            "SELECT model, lang, detail FROM prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return Prefs()
        model, lang, detail = row
        # Модель могли убрать из каталога — тогда возвращаемся к модели по умолчанию
        return Prefs(
            model if model in MODELS else DEFAULT_MODEL,
            lang if lang in LANGS else "ru",
            detail if detail in DETAILS else "full",
        )

    def update(self, user_id: int, **changes: str) -> Prefs:
        prefs = replace(self.get(user_id), **changes)
        self.db.execute(
            "INSERT INTO prefs (user_id, model, lang, detail) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "model = excluded.model, lang = excluded.lang, detail = excluded.detail",
            (user_id, prefs.model, prefs.lang, prefs.detail),
        )
        self.db.commit()
        return prefs


def next_value(options: dict[str, str], current: str) -> str:
    keys = list(options)
    return keys[(keys.index(current) + 1) % len(keys)]
