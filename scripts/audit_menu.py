# -*- coding: utf-8 -*-
"""Меню-аудитор (CLI): жив / сломан / мёртв по всем ~110 командам реестра.

Тонкая обёртка над app.services.audit.menu_audit.run_audit — вся логика там
(и покрыта tests/test_menu_audit_lib.py). Печатает таблицу в stdout, пишет
markdown-снимок в state/menu_audit_report.md, exit-code 0 если всё зелёно
(включая известные пре-существующие), иначе 1.

    python scripts/audit_menu.py

См. docs/superpowers/specs/2026-07-06-menu-audit-design.md.
"""
import sys
from pathlib import Path

# repo root on path so `app`/`tools` import when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.audit import menu_audit as ma


def main() -> int:
    report = ma.run_audit()
    text = ma.render_report(report)
    print(text)

    snap = Path("state/menu_audit_report.md")
    try:
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(text, encoding="utf-8")
        print("\n(снимок: %s)" % snap)
    except Exception as exc:                       # snapshot best-effort
        print("\n(не удалось записать снимок: %s)" % exc)

    return 0 if ma.audit_ok(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
