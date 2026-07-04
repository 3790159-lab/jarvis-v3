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
