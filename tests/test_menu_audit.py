# -*- coding: utf-8 -*-
"""Menu-audit REGRESS SENTINEL — joins the full suite, so it runs in the
/dev_task [Мердж] gate (tools/...:_devtask_run_regress runs `pytest tests/`
unfiltered vs baseline) as well as the manual /regress and any full run.

Guards the /browse_check bug CLASS across all ~110 menu commands: a dev-task that
introduces a DEAD command, a broken import, an undefined-global-in-a-body, or a
read-only command that no longer executes → this file goes red → +1 vs baseline
→ merge blocked.

Green NOW on documented pre-existing issues (menu_audit.KNOWN_* baseline); fails
only on findings BEYOND that baseline. $0, no network (dry-calls hit only the
curated read-only allowlist). See spec §4.2.
"""
from app.services.audit import menu_audit as ma

_REPORT = ma.run_audit()


def test_no_new_dead_menu_commands():
    dead = ma.new_findings(_REPORT)["dead"]
    assert dead == [], "мёртвые команды меню (нет ветки в handle_command): %s" % dead


def test_no_new_undefined_globals_or_broken_imports():
    nf = ma.new_findings(_REPORT)
    assert nf["undefined_globals"] == [], (
        "НОВЫЕ undefined globals (класс /browse_check-бага): %s" % nf["undefined_globals"])
    assert nf["broken_imports"] == [], (
        "НОВЫЕ битые импорты (наш код): %s" % nf["broken_imports"])


def test_readonly_allowlist_commands_execute_cleanly():
    failed = [(r["cmd"], r["dry"]["error"]) for r in _REPORT["rows"]
              if r["dry"] is not None and not r["dry"]["ok"]]
    assert failed == [], "read-only allowlist-команды упали в dry-вызове: %s" % failed


def test_gate_ok_overall():
    assert ma.gate_ok(_REPORT), "аудит нашёл находки сверх baseline: %s" % ma.new_findings(_REPORT)
