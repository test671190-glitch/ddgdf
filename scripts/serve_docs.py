"""Локальная проверка сайта с заголовками, как на Netlify (docs/dist/_headers, в том числе CSP).

Запуск: .venv\\Scripts\\python.exe scripts/serve_docs.py [порт]  → http://127.0.0.1:8765/
"""

import functools
import http.server
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "docs" / "dist"


def read_headers() -> list[tuple[str, list[tuple[str, str]]]]:
    """Правила _headers: строка без отступа — путь (/* — всё), с отступом — «Имя: значение»."""
    rules: list[tuple[str, list[tuple[str, str]]]] = []
    for line in (DIST / "_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            rules.append((line.strip(), []))
        elif rules:
            name, _, value = line.strip().partition(":")
            rules[-1][1].append((name.strip(), value.strip()))
    return rules


def matches(pattern: str, path: str) -> bool:
    return path.startswith(pattern[:-1]) if pattern.endswith("*") else path == pattern


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        for pattern, headers in read_headers():
            if matches(pattern, path):
                for name, value in headers:
                    self.send_header(name, value)
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=str(DIST))
    print(f"http://127.0.0.1:{port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
