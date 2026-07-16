# -*- coding: utf-8 -*-
"""chatter_watch_check — the standalone stdlib-only DOWN alerter for the chatter
Telethon runner (invoked by JarvisChatterGuardian). Only the pure decision
functions are unit-tested here; the urllib TG-send and marker file IO are
stdlib-only and exercised live by the guardian, exactly like
test_boot_watch / test_ops_watchdog."""
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "chatter_watch_check",
    _P(__file__).resolve().parent.parent / "scripts" / "chatter_watch_check.py")
cw = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def test_heartbeat_fresh_true_only_when_recent_and_numeric():
    now = 1000.0
    assert cw.is_heartbeat_fresh("970", now=now, max_age=60) is True    # 30s old
    assert cw.is_heartbeat_fresh("939", now=now, max_age=60) is False   # 61s old
    assert cw.is_heartbeat_fresh("", now=now, max_age=60) is False
    assert cw.is_heartbeat_fresh("garbage", now=now, max_age=60) is False
    assert cw.is_heartbeat_fresh(None, now=now, max_age=60) is False


def test_should_alert_only_when_down_and_cooldown_elapsed():
    now = 10_000.0
    # down + never alerted -> alert
    assert cw.should_alert(is_down=True, last_alert_ts=None, now=now, cooldown=3600) is True
    # down + alerted 10 min ago (cooldown 1h) -> suppressed
    assert cw.should_alert(is_down=True, last_alert_ts=now - 600, now=now, cooldown=3600) is False
    # down + alerted 2h ago -> alert again
    assert cw.should_alert(is_down=True, last_alert_ts=now - 7200, now=now, cooldown=3600) is True
    # healthy -> never alert regardless of history
    assert cw.should_alert(is_down=False, last_alert_ts=None, now=now, cooldown=3600) is False


def test_alert_text_mentions_chatter_and_recovery():
    txt = cw.alert_text()
    assert "chatter" in txt.lower()
    assert "JarvisChatterGuardian" in txt or "guardian" in txt.lower()
