"""Журнал в stderr. На сервере его собирает journald (journalctl -u models-bot).

Стандартный logging стоит ~1,3 МБ памяти, а боту нужен только вывод строк.
"""

import os
import sys
import threading
import time

_lock = threading.Lock()
# Под systemd время ставит journald, локально — пишем сами
_STAMP = "INVOCATION_ID" not in os.environ


def _write(level: str, text: str) -> None:
    prefix = time.strftime("%Y-%m-%d %H:%M:%S ") if _STAMP else ""
    with _lock:
        sys.stderr.write(f"{prefix}[{level}] {text}\n")
        sys.stderr.flush()


def info(msg: str, *args) -> None:
    _write("INFO", msg % args if args else msg)


def warning(msg: str, *args) -> None:
    _write("WARNING", msg % args if args else msg)


def error(msg: str, *args) -> None:
    _write("ERROR", msg % args if args else msg)


def exception(msg: str, *args) -> None:
    """Сообщение и трассировка текущего исключения."""
    import traceback  # только при ошибке: модуль тянет за собой ещё несколько

    _write("ERROR", (msg % args if args else msg) + "\n" + traceback.format_exc().rstrip())
