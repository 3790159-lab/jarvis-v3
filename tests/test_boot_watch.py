# -*- coding: utf-8 -*-
"""boot_watch (crash-loop guard) — durable markers + standalone checker logic."""
import json

import app.services.devtask.boot_watch as bw


def test_write_and_read_boot_watch(tmp_path):
    bw.write_boot_watch(tmp_path, "T1", "oldsha", "newsha", now=1000, watch_s=180)
    data = json.loads((tmp_path / "boot_watch.json").read_text(encoding="utf-8"))
    assert data["old_head"] == "oldsha" and data["new_head"] == "newsha"
    assert data["deadline_epoch"] == 1180
    assert "reset --hard oldsha" in data["rollback_cmds"]


def test_clear_boot_watch(tmp_path):
    bw.write_boot_watch(tmp_path, "T1", "o", "n", now=0)
    bw.clear_boot_watch(tmp_path)
    assert not (tmp_path / "boot_watch.json").exists()


def test_pending_restart_roundtrip_single_shot(tmp_path):
    bw.write_pending_restart(tmp_path, "T1", "o", "n")
    got = bw.read_pending_restart(tmp_path)
    assert got["task_id"] == "T1" and got["old_head"] == "o"
    bw.clear_pending_restart(tmp_path)
    assert bw.read_pending_restart(tmp_path) is None


# ── Task 7: standalone crash-loop checker decision functions ───────────────
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "boot_watch_check", _P(__file__).resolve().parent.parent / "scripts" / "boot_watch_check.py")
bwc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(bwc)


def test_is_overdue():
    assert bwc.is_overdue({"deadline_epoch": 100}, now=101) is True
    assert bwc.is_overdue({"deadline_epoch": 100}, now=99) is False


def test_should_alert_only_when_overdue_stale_and_not_alerted():
    w = {"deadline_epoch": 100, "alerted": False}
    assert bwc.should_alert(w, now=200, hb_fresh=False) is True     # overdue + stale
    assert bwc.should_alert(w, now=200, hb_fresh=True) is False     # bot alive -> no alert
    assert bwc.should_alert(w, now=50, hb_fresh=False) is False     # not overdue yet
    assert bwc.should_alert({"deadline_epoch": 100, "alerted": True}, now=200, hb_fresh=False) is False


def test_alert_text_has_rollback():
    w = {"task_id": "T9", "rollback_cmds": "git reset --hard OLD"}
    assert "T9" in bwc.alert_text(w) and "reset --hard OLD" in bwc.alert_text(w)
