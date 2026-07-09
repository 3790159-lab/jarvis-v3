# -*- coding: utf-8 -*-
"""Standalone runaway-regress killer decision functions (Этап 1, хвост #6).

Loaded via importlib the same way test_boot_watch loads boot_watch_check — it is
a stdlib-only script (no package import) so it survives a merge that breaks the
bot's own imports. Only the pure decision/text functions are unit-tested; the
taskkill / tasklist / TG io is stdlib-only and exercised live by the guardian.
"""
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "regress_watch_check",
    _P(__file__).resolve().parent.parent / "scripts" / "regress_watch_check.py")
rwc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(rwc)


def test_is_expired():
    assert rwc.is_expired({"deadline_epoch": 100}, now=101) is True
    assert rwc.is_expired({"deadline_epoch": 100}, now=99) is False


def test_is_orphaned():
    assert rwc.is_orphaned({"bot_pid": 5}, parent_alive=False) is True
    assert rwc.is_orphaned({"bot_pid": 5}, parent_alive=True) is False


def test_runaway_reason_orphan_first_then_deadline():
    w = {"deadline_epoch": 100}
    assert rwc.runaway_reason(w, now=200, parent_alive=False) == "orphan"
    assert rwc.runaway_reason(w, now=200, parent_alive=True) == "deadline"
    assert rwc.runaway_reason(w, now=50, parent_alive=True) is None


def test_alert_text_names_pid_and_reason():
    w = {"pid": 4242, "label": "merge-gate"}
    txt = rwc.alert_text(w, "orphan")
    assert "4242" in txt and "убит" in txt and "сирота" in txt
    assert "merge-gate" in txt
