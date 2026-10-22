"""Данные пользователей в SQLite: настройки, история диалога и расход токенов."""

import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path

from models_bot.catalog import CUSTOM_ROLE, DEFAULT_MODEL, MODELS, ROLES

LANGS = {"ru": "русский", "en": "английский"}
DETAILS = {"full": "подробные", "short": "краткие"}
HISTORY_LIMIT = 20  # сообщений диалога на пользователя, более старые забываются


@dataclass(frozen=True)
class Prefs:
    model: str = DEFAULT_MODEL
    lang: str = "ru"
    detail: str = "full"
    role: str = "none"  # ключ из catalog.ROLES или CUSTOM_ROLE
    role_text: str = ""  # инструкция своей роли
    memory: bool = True
    show_usage: bool = True
    notify_models: bool = True
    notify_limit: bool = True

    @property
    def role_prompt(self) -> str:
        return self.role_text if self.role == CUSTOM_ROLE else ROLES[self.role].prompt

    @property
    def role_title(self) -> str:
        return "Своя роль" if self.role == CUSTOM_ROLE else ROLES[self.role].title


FIELDS = [f.name for f in fields(Prefs)]
DEFAULTS = Prefs()


@dataclass(frozen=True)
class Turn:
    author: str  # user | bot
    text: str
    tokens: int


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def next_value(options: dict[str, str], current: str) -> str:
    keys = list(options)
    return keys[(keys.index(current) + 1) % len(keys)]


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS prefs (user_id INTEGER PRIMARY KEY);"
            "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id INTEGER, author TEXT, text TEXT, tokens INTEGER);"
            "CREATE INDEX IF NOT EXISTS history_user ON history (user_id, id);"
            "CREATE TABLE IF NOT EXISTS usage (user_id INTEGER, day TEXT, tokens INTEGER,"
            " PRIMARY KEY (user_id, day));"
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
        )
        # Новая настройка — новый столбец. У прежних строк в нём NULL: это значение по умолчанию
        have = {row[1] for row in self.db.execute("PRAGMA table_info(prefs)")}
        for name in FIELDS:
            if name not in have:
                self.db.execute(f"ALTER TABLE prefs ADD COLUMN {name}")
        self.db.commit()

    # Настройки

    def touch(self, user_id: int) -> None:
        """Запомнить пользователя: ему придут уведомления, даже если он ничего не менял."""
        self.db.execute("INSERT OR IGNORE INTO prefs (user_id) VALUES (?)", (user_id,))
        self.db.commit()

    def get(self, user_id: int) -> Prefs:
        row = self.db.execute(
            f"SELECT {', '.join(FIELDS)} FROM prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return DEFAULTS
        values = {}
        for name, value in zip(FIELDS, row, strict=True):
            default = getattr(DEFAULTS, name)
            if value is None:
                value = default
            values[name] = bool(value) if isinstance(default, bool) else value
        prefs = Prefs(**values)
        # Модель или роль могли убрать из каталога — тогда берём значение по умолчанию
        changes = {}
        if prefs.model not in MODELS:
            changes["model"] = DEFAULTS.model
        if prefs.lang not in LANGS:
            changes["lang"] = DEFAULTS.lang
        if prefs.detail not in DETAILS:
            changes["detail"] = DEFAULTS.detail
        if prefs.role not in ROLES and not (prefs.role == CUSTOM_ROLE and prefs.role_text):
            changes["role"] = DEFAULTS.role
        return replace(prefs, **changes)

    def update(self, user_id: int, **changes) -> Prefs:
        prefs = replace(self.get(user_id), **changes)
        columns = ", ".join(FIELDS)
        marks = ", ".join(["?"] * len(FIELDS))
        assign = ", ".join(f"{name} = excluded.{name}" for name in FIELDS)
        self.db.execute(
            f"INSERT INTO prefs (user_id, {columns}) VALUES (?, {marks}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {assign}",
            (user_id, *(getattr(prefs, name) for name in FIELDS)),
        )
        self.db.commit()
        return prefs

    def users_with(self, flag: str) -> list[int]:
        """Пользователи, у которых включена настройка-флаг flag."""
        assert flag in FIELDS and isinstance(getattr(DEFAULTS, flag), bool)
        default = getattr(DEFAULTS, flag)
        rows = self.db.execute(f"SELECT user_id, {flag} FROM prefs").fetchall()
        return [uid for uid, value in rows if (default if value is None else bool(value))]

    # История диалога

    def history(self, user_id: int) -> list[Turn]:
        rows = self.db.execute(
            "SELECT author, text, tokens FROM history WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [Turn(*row) for row in rows]

    def remember(self, user_id: int, *turns: Turn) -> None:
        self.db.executemany(
            "INSERT INTO history (user_id, author, text, tokens) VALUES (?, ?, ?, ?)",
            [(user_id, t.author, t.text, t.tokens) for t in turns],
        )
        self.db.execute(
            "DELETE FROM history WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, HISTORY_LIMIT),
        )
        self.db.commit()

    def forget(self, user_id: int) -> None:
        self.db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        self.db.commit()

    # Расход токенов за сутки (UTC)

    def used_today(self, user_id: int) -> int:
        row = self.db.execute(
            "SELECT tokens FROM usage WHERE user_id = ? AND day = ?", (user_id, today())
        ).fetchone()
        return row[0] if row else 0

    def spend(self, user_id: int, tokens: int) -> tuple[int, int]:
        """Списать токены. Возвращает расход за сегодня до и после."""
        before = self.used_today(user_id)
        day = today()
        self.db.execute("DELETE FROM usage WHERE day < ?", (day,))
        self.db.execute(
            "INSERT INTO usage (user_id, day, tokens) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET tokens = tokens + excluded.tokens",
            (user_id, day, tokens),
        )
        self.db.commit()
        return before, before + tokens

    # Служебные значения

    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.db.commit()
