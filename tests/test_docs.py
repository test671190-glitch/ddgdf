"""Сайт: заголовки безопасности для Netlify (docs/dist/_headers)."""

import base64
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_docs", ROOT / "scripts" / "build_docs.py")
build_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_docs)


def pages() -> list[str]:
    return [build_docs.standalone(build_docs.render(name)) for name in build_docs.PAGES]


def csp_line() -> str:
    lines = build_docs.headers_file(pages()).splitlines()
    assert lines[0] == "/*"
    return next(line for line in lines if line.startswith("  Content-Security-Policy: "))


def test_every_inline_script_is_allowed_by_hash():
    """Скрипт без хеша в CSP браузер не выполнит: пропали бы 3D-фон (importmap и модуль)
    и карта моделей."""
    csp = csp_line()
    for html in pages():
        # importmap — ровно текст между тегами, как его хеширует браузер
        importmap = html.split('<script type="importmap">', 1)[1].split("</script>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(importmap.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in csp
        assert html.count("<script") == len(build_docs.script_hashes(html))
        assert build_docs.script_hashes(html) <= set(csp.replace(";", " ").split())


def test_csp_blocks_injected_scripts_and_framing():
    csp = csp_line()
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    for directive in ("default-src 'none'", "frame-ancestors 'none'", "base-uri 'none'"):
        assert directive in csp
    assert "  X-Frame-Options: DENY" in build_docs.headers_file(pages()).splitlines()
