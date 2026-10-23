"""Данные пользователей в SQLite: настройки, чаты с их перепиской и расход токенов.

У пользователя может быть много чатов, у каждого своя модель и своя история;
открыт — один (prefs.active). Обработчики работают в нескольких потоках, поэтому
одно соединение с базой защищено блокировкой.
"""

import os
import re
import sqlite3
import threading
import time
from collections import namedtuple

from models_bot.catalog import CUSTOM_ROLE, DEFAULT_MODEL, MODELS, ROLES

LANGS = {"ru": "русский", "en": "английский"}
DETAILS = {"full": "подробные", "short": "краткие"}
HISTORY_LIMIT = 20  # сообщений в одном чате, более старые забываются
MAX_CHATS = 50  # чатов на пользователя: при создании 51-го удаляется самый давний

_PREFS = {
    "model": DEFAULT_MODEL,  # основная модель: с ней /chat начинает новый чат
    "lang": "ru",
    "detail": "full",
    "role": "none",  # ключ из catalog.ROLES или CUSTOM_ROLE
    "role_text": "",  # инструкция своей роли
    "memory": True,
    "show_usage": True,
    "notify_models": True,
    "notify_limit": True,
    "chat": "",  # прежняя версия: модель единственного чата; переносится в chats при запуске
    "awaiting": "",  # "role" или "rename:<id чата>" — что станет следующим сообщением
    "blocked": False,  # пользователь заблокировал бота: уведомления ему не шлём
    "active": 0,  # id открытого чата; 0 — чат не открыт
    "balance": 0,  # купленные токены: тратятся, когда кончился бесплатный дневной лимит
}
AWAITING = re.compile(r"(role|rename:\d+)?")


class Prefs(namedtuple("Prefs", _PREFS, defaults=_PREFS.values())):
    __slots__ = ()

    @property
    def role_prompt(self) -> str:
        return self.role_text if self.role == CUSTOM_ROLE else ROLES[self.role].prompt

    @property
    def role_title(self) -> str:
        return "Своя роль" if self.role == CUSTOM_ROLE else ROLES[self.role].title


FIELDS = Prefs._fields
DEFAULTS = Prefs()
Turn = namedtuple("Turn", "author text tokens")  # author: user | bot
# messages — сколько вопросов задано в чате; updated — время последнего, unix
Chat = namedtuple("Chat", "id user_id model title messages updated")
CHAT_COLUMNS = "id, user_id, model, title, messages, updated"
# Ответ модели, к которому привязаны кнопки под ним; attempt — сколько раз перегенерирован
Answer = namedtuple("Answer", "id user_id chat_id question model attempt attachment")
REPLIES_LIMIT = 100  # ответов с рабочими кнопками на пользователя


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def next_value(options: dict[str, str], current: str) -> str:
    keys = list(options)
    return keys[(keys.index(current) + 1) % len(keys)]


class Store:
    def __init__(self, path) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.known: set[int] = set()  # кого уже записали в prefs: touch без лишней записи
        self.db.executescript(
            # WAL — запись без блокировки чтения; кэш страниц поменьше: база крошечная
            "PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA cache_size = -512;"
            "CREATE TABLE IF NOT EXISTS prefs (user_id INTEGER PRIMARY KEY);"
            "CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id INTEGER, model TEXT, title TEXT, messages INTEGER, updated INTEGER);"
            "CREATE INDEX IF NOT EXISTS chats_user ON chats (user_id, updated);"
            "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id INTEGER, author TEXT, text TEXT, tokens INTEGER, chat_id INTEGER);"
            "CREATE TABLE IF NOT EXISTS usage (user_id INTEGER, day TEXT, tokens INTEGER,"
            " PRIMARY KEY (user_id, day));"
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
            # Ответы: по ним работают кнопки «перегенерировать», «другая модель», «короче»
            "CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id INTEGER, chat_id INTEGER, question TEXT, model TEXT, attempt INTEGER,"
            " attachment TEXT);"
            "CREATE INDEX IF NOT EXISTS replies_user ON replies (user_id, id);"
            # Оплаты Stars: charge_id нужен для возврата и чтобы не начислить дважды
            "CREATE TABLE IF NOT EXISTS payments (charge_id TEXT PRIMARY KEY, user_id INTEGER,"
            " pack TEXT, stars INTEGER, tokens INTEGER, created REAL);"
        )
        # Новая настройка — новый столбец. У прежних строк в нём NULL: это значение по умолчанию
        self._add_columns("prefs", FIELDS)
        self._add_columns("history", ["chat_id"])
        self.db.execute("CREATE INDEX IF NOT EXISTS history_chat ON history (chat_id, id)")
        self._migrate_single_chat()
        self.db.commit()

    def _add_columns(self, table: str, names) -> None:
        have = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
        for name in names:
            if name not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name}")

    def _migrate_single_chat(self) -> None:
        """В прежней версии чат у пользователя был один: модель — в prefs.chat, история —
        без chat_id. Такой чат становится первым в списке и остаётся открытым."""
        rows = self.db.execute(
            "SELECT user_id, chat FROM prefs WHERE chat IS NOT NULL AND chat != ''"
        ).fetchall()
        for user_id, model in rows:
            first = self.db.execute(
                "SELECT text FROM history WHERE user_id = ? AND chat_id IS NULL"
                " AND author = 'user' ORDER BY id LIMIT 1",
                (user_id,),
            ).fetchone()
            asked = self.db.execute(
                "SELECT count(*) FROM history WHERE user_id = ? AND chat_id IS NULL"
                " AND author = 'user'",
                (user_id,),
            ).fetchone()[0]
            chat_id = self.db.execute(
                "INSERT INTO chats (user_id, model, title, messages, updated)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    model,
                    chat_title_from(first[0]) if first else "",
                    asked,
                    time.time(),
                ),
            ).lastrowid
            self.db.execute(
                "UPDATE history SET chat_id = ? WHERE user_id = ? AND chat_id IS NULL",
                (chat_id, user_id),
            )
            self.db.execute(
                "UPDATE prefs SET active = ?, chat = '' WHERE user_id = ?", (chat_id, user_id)
            )
        # Переписка без чата (чат был закрыт) никому больше не видна
        self.db.execute("DELETE FROM history WHERE chat_id IS NULL")

    # Настройки

    def touch(self, user_id: int) -> None:
        """Запомнить пользователя: ему придут уведомления, даже если он ничего не менял."""
        with self.lock:
            if user_id in self.known:
                return
            self.db.execute("INSERT OR IGNORE INTO prefs (user_id) VALUES (?)", (user_id,))
            self.db.commit()
            self.known.add(user_id)

    def get(self, user_id: int) -> Prefs:
        with self.lock:
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
        if not isinstance(prefs.awaiting, str) or not AWAITING.fullmatch(prefs.awaiting):
            changes["awaiting"] = ""
        if not isinstance(prefs.active, int):
            changes["active"] = 0
        return prefs._replace(**changes)

    def update(self, user_id: int, **changes) -> Prefs:
        columns = ", ".join(FIELDS)
        marks = ", ".join(["?"] * len(FIELDS))
        assign = ", ".join(f"{name} = excluded.{name}" for name in FIELDS)
        with self.lock:
            prefs = self.get(user_id)._replace(**changes)
            self.db.execute(
                f"INSERT INTO prefs (user_id, {columns}) VALUES (?, {marks}) "
                f"ON CONFLICT(user_id) DO UPDATE SET {assign}",
                (user_id, *prefs),
            )
            self.db.commit()
            self.known.add(user_id)
        return prefs

    def users_with(self, flag: str) -> list[int]:
        """Пользователи, у которых включена настройка-флаг flag и бот не заблокирован."""
        if flag not in FIELDS or not isinstance(getattr(DEFAULTS, flag), bool):
            raise ValueError(flag)
        default = getattr(DEFAULTS, flag)
        with self.lock:
            rows = self.db.execute(f"SELECT user_id, {flag}, blocked FROM prefs").fetchall()
        return [
            uid
            for uid, value, blocked in rows
            if (default if value is None else bool(value)) and not blocked
        ]

    # Чаты

    def new_chat(self, user_id: int, model: str) -> tuple[Chat, list[Chat]]:
        """Создать чат. Возвращает его и чаты, удалённые сверх MAX_CHATS (самые давние)."""
        now = time.time()
        with self.lock:
            chat_id = self.db.execute(
                "INSERT INTO chats (user_id, model, title, messages, updated)"
                " VALUES (?, ?, '', 0, ?)",
                (user_id, model, now),
            ).lastrowid
            extra = self.db.execute(
                f"SELECT {CHAT_COLUMNS} FROM chats WHERE user_id = ?"
                " ORDER BY updated DESC, id DESC LIMIT -1 OFFSET ?",
                (user_id, MAX_CHATS),
            ).fetchall()
            for row in extra:
                self._delete(row[0])
            self.db.commit()
        return Chat(chat_id, user_id, model, "", 0, now), [Chat(*row) for row in extra]

    def chat(self, chat_id: int) -> Chat | None:
        with self.lock:
            row = self.db.execute(
                f"SELECT {CHAT_COLUMNS} FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return Chat(*row) if row else None

    def chats(self, user_id: int) -> list[Chat]:
        """Чаты пользователя, последние — первыми."""
        with self.lock:
            rows = self.db.execute(
                f"SELECT {CHAT_COLUMNS} FROM chats WHERE user_id = ?"
                " ORDER BY updated DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [Chat(*row) for row in rows]

    def rename_chat(self, chat_id: int, title: str) -> None:
        with self.lock:
            self.db.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
            self.db.commit()

    def note_question(self, chat_id: int, question: str) -> None:
        """Вопрос задан: чат поднимается в списке, а первый вопрос становится названием."""
        with self.lock:
            self.db.execute(
                "UPDATE chats SET messages = messages + 1, updated = ?,"
                " title = CASE WHEN title = '' THEN ? ELSE title END WHERE id = ?",
                (time.time(), chat_title_from(question), chat_id),
            )
            self.db.commit()

    def delete_chat(self, chat_id: int) -> None:
        with self.lock:
            self._delete(chat_id)
            self.db.commit()

    def _delete(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        self.db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    # Переписка чата

    def history(self, chat_id: int) -> list[Turn]:
        with self.lock:
            rows = self.db.execute(
                "SELECT author, text, tokens FROM history WHERE chat_id = ? ORDER BY id",
                (chat_id,),
            ).fetchall()
        return [Turn(*row) for row in rows]

    def remember(self, chat_id: int, user_id: int, *turns: Turn) -> None:
        with self.lock:
            self.db.executemany(
                "INSERT INTO history (chat_id, user_id, author, text, tokens)"
                " VALUES (?, ?, ?, ?, ?)",
                [(chat_id, user_id, *t) for t in turns],
            )
            self.db.execute(
                "DELETE FROM history WHERE chat_id = ? AND id NOT IN "
                "(SELECT id FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
                (chat_id, chat_id, HISTORY_LIMIT),
            )
            self.db.commit()

    def replace_last_answer(self, chat_id: int, text: str, tokens: int) -> None:
        """Перегенерированный ответ заменяет прошлый в памяти чата."""
        with self.lock:
            self.db.execute(
                "UPDATE history SET text = ?, tokens = ? WHERE id ="
                " (SELECT max(id) FROM history WHERE chat_id = ? AND author = 'bot')",
                (text, tokens, chat_id),
            )
            self.db.commit()

    # Ответы под кнопками

    def add_reply(
        self, user_id: int, chat_id: int, question: str, model: str, attachment: str | None
    ) -> int:
        with self.lock:
            reply_id = self.db.execute(
                "INSERT INTO replies (user_id, chat_id, question, model, attempt, attachment)"
                " VALUES (?, ?, ?, ?, 0, ?)",
                (user_id, chat_id, question, model, attachment),
            ).lastrowid
            self.db.execute(
                "DELETE FROM replies WHERE user_id = ? AND id NOT IN "
                "(SELECT id FROM replies WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
                (user_id, user_id, REPLIES_LIMIT),
            )
            self.db.commit()
        return reply_id

    def reply(self, reply_id: int) -> "Answer | None":
        with self.lock:
            row = self.db.execute(
                "SELECT id, user_id, chat_id, question, model, attempt, attachment"
                " FROM replies WHERE id = ?",
                (reply_id,),
            ).fetchone()
        return Answer(*row) if row else None

    def update_reply(self, reply_id: int, **changes) -> None:
        sets = ", ".join(f"{name} = ?" for name in changes)
        with self.lock:
            self.db.execute(
                f"UPDATE replies SET {sets} WHERE id = ?", (*changes.values(), reply_id)
            )
            self.db.commit()

    def last_reply_id(self, chat_id: int) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT max(id) FROM replies WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row[0] or 0

    # Баланс и оплаты

    def charge(self, user_id: int, tokens: int) -> int:
        """Списать купленные токены (не ниже нуля). Возвращает остаток."""
        with self.lock:
            self.touch(user_id)
            self.db.execute(
                "UPDATE prefs SET balance = max(0, coalesce(balance, 0) - ?) WHERE user_id = ?",
                (tokens, user_id),
            )
            self.db.commit()
            return self.get(user_id).balance

    def credit(
        self, charge_id: str, user_id: int, pack: str, stars: int, tokens: int
    ) -> int | None:
        """Начислить оплату. None — эта оплата уже начислена. Иначе — новый баланс."""
        with self.lock:
            self.touch(user_id)
            try:
                self.db.execute(
                    "INSERT INTO payments (charge_id, user_id, pack, stars, tokens, created)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (charge_id, user_id, pack, stars, tokens, time.time()),
                )
            except sqlite3.IntegrityError:
                return None
            self.db.execute(
                "UPDATE prefs SET balance = coalesce(balance, 0) + ? WHERE user_id = ?",
                (tokens, user_id),
            )
            self.db.commit()
            return self.get(user_id).balance

    # Расход токенов за сутки (UTC)

    def used_today(self, user_id: int) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT tokens FROM usage WHERE user_id = ? AND day = ?", (user_id, today())
            ).fetchone()
        return row[0] if row else 0

    def spend(self, user_id: int, tokens: int) -> tuple[int, int]:
        """Списать токены. Возвращает расход за сегодня до и после."""
        day = today()
        with self.lock:
            before = self.used_today(user_id)
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
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.db.commit()


TITLE_LENGTH = 40


def chat_title_from(text: str) -> str:
    """Название чата из первого вопроса: одна строка, до TITLE_LENGTH символов."""
    text = " ".join(text.split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"
