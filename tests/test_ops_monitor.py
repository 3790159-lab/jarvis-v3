# -*- coding: utf-8 -*-
"""DEV-17 monitoring & alerts: app.services.ops_monitor.

Pure, injectable checks (mirrors app.services.infra_control's DI style —
``run``/``now``/``usage`` are always fakes here, no real subprocess/disk/
service call ever runs from this suite). These are the building blocks the
new ``/api/jarvis/ops/*`` endpoints expose for Uptime Kuma to poll — each
check must be independently testable without a live backend/bot/Kuma.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.services import ops_monitor as om


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, free_bytes):
        self.free_bytes = free_bytes

    def __call__(self, path):
        total = 100 * 1024 ** 3
        return (total, total - self.free_bytes, self.free_bytes)


def test_check_disk_ok_when_free_above_min():
    result = om.check_disk(min_gb=4.0, usage=_Usage(10 * 1024 ** 3))
    assert result["ok"] is True
    assert result["free_gb"] == pytest.approx(10.0, abs=0.01)


def test_check_disk_not_ok_when_free_below_min():
    result = om.check_disk(min_gb=4.0, usage=_Usage(1 * 1024 ** 3))
    assert result["ok"] is False
    assert result["free_gb"] == pytest.approx(1.0, abs=0.01)


def test_check_disk_usage_exception_is_not_ok_not_a_crash():
    def usage(path):
        raise OSError("no such drive")
    result = om.check_disk(usage=usage)
    assert result["ok"] is False
    assert result["free_gb"] is None


# ---------------------------------------------------------------------------
# check_bot_heartbeat
# ---------------------------------------------------------------------------

def test_check_bot_heartbeat_ok_when_fresh(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text("1000", encoding="utf-8")
    result = om.check_bot_heartbeat(hb, max_age_sec=180, now=lambda: 1000 + 30)
    assert result["ok"] is True
    assert result["age_sec"] == pytest.approx(30.0)


def test_check_bot_heartbeat_not_ok_when_stale(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text("1000", encoding="utf-8")
    result = om.check_bot_heartbeat(hb, max_age_sec=180, now=lambda: 1000 + 300)
    assert result["ok"] is False


def test_check_bot_heartbeat_missing_file_is_not_ok_not_a_crash(tmp_path):
    result = om.check_bot_heartbeat(tmp_path / "missing.txt")
    assert result["ok"] is False
    assert result["age_sec"] is None


# ---------------------------------------------------------------------------
# check_cloudflared (delegates to infra_control.cloudflared_status)
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, stdout=""):
        self.stdout = stdout


def test_check_cloudflared_ok_when_running():
    run = lambda *a, **k: _Res("Running\r\n")
    result = om.check_cloudflared(run=run)
    assert result["ok"] is True
    assert result["status"] == "Running"


def test_check_cloudflared_not_ok_when_stopped():
    run = lambda *a, **k: _Res("Stopped\r\n")
    result = om.check_cloudflared(run=run)
    assert result["ok"] is False
    assert result["status"] == "Stopped"


def test_check_cloudflared_not_ok_when_run_raises():
    def run(*a, **k):
        raise OSError("powershell missing")
    result = om.check_cloudflared(run=run)
    assert result["ok"] is False
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# count_recent_restarts / check_restart_storm
# ---------------------------------------------------------------------------

_GUARDIAN_LOG = """2026-07-15 01:00:00 | bot guardian started (PID 1234), heartbeat<=180s every 5s, debounce=3
2026-07-15 02:00:00 | bot alive
2026-07-15 02:10:00 | bot DOWN - restarting
2026-07-15 02:40:00 | bot alive
2026-07-15 02:50:00 | bot DOWN - restarting
2026-07-15 03:10:00 | bot alive
2026-07-15 03:14:00 | bot DOWN - restarting
2026-07-15 03:20:00 | bot alive
2026-07-15 03:25:00 | bot DOWN - restarting
"""


def _write_log(tmp_path, content=_GUARDIAN_LOG):
    log = tmp_path / "bot_guardian.stdout.log"
    log.write_text(content, encoding="utf-8")
    return log


def test_count_recent_restarts_counts_only_within_window(tmp_path):
    log = _write_log(tmp_path)
    # "now" just after the last restart: all four restarting-lines fall within
    # a 3h window starting at 02:10, but only the last three are within 1h.
    now = datetime(2026, 7, 15, 3, 30, 0)
    count = om.count_recent_restarts(log, window_sec=3600, now=now)
    assert count == 3


def test_count_recent_restarts_zero_when_log_missing(tmp_path):
    count = om.count_recent_restarts(tmp_path / "missing.log", now=datetime(2026, 7, 15, 3, 30, 0))
    assert count == 0


def test_count_recent_restarts_ignores_non_restart_lines(tmp_path):
    log = _write_log(tmp_path, content="2026-07-15 03:00:00 | bot alive\n")
    count = om.count_recent_restarts(log, now=datetime(2026, 7, 15, 3, 30, 0))
    assert count == 0


def test_check_restart_storm_ok_when_under_threshold(tmp_path):
    log = _write_log(tmp_path)
    now = datetime(2026, 7, 15, 2, 45, 0)  # only 1 restart (02:10) within the last hour
    result = om.check_restart_storm(log, threshold=3, window_sec=3600, now=now)
    assert result["ok"] is True
    assert result["count"] == 1


def test_check_restart_storm_not_ok_when_over_threshold(tmp_path):
    log = _write_log(tmp_path)
    now = datetime(2026, 7, 15, 3, 30, 0)  # 3 restarts within the last hour -> > threshold of 2
    result = om.check_restart_storm(log, threshold=2, window_sec=3600, now=now)
    assert result["ok"] is False
    assert result["count"] == 3


def test_check_restart_storm_ok_at_exact_threshold_boundary(tmp_path):
    log = _write_log(tmp_path)
    now = datetime(2026, 7, 15, 3, 30, 0)  # exactly 3 restarts within the last hour
    result = om.check_restart_storm(log, threshold=3, window_sec=3600, now=now)
    assert result["ok"] is True
    assert result["count"] == 3
