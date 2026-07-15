# -*- coding: utf-8 -*-
"""Independent ops watchdog decision functions (native replacement for Uptime
Kuma, DEV-17 follow-up).

Loaded via importlib exactly like ``test_regress_watch_check`` / ``test_boot_watch``:
``scripts/ops_watchdog.py`` is a stdlib-only standalone script (no package import)
so it survives even a merge that breaks the bot's own imports, and can Telegram
the admin about a dead backend/bot without depending on either being alive.

Only the pure decision / probe / text functions are unit-tested here; the actual
urllib TG-send and JSON state file IO are stdlib-only and exercised live by the
scheduled task (JarvisOpsWatchdog).
"""
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "ops_watchdog",
    _P(__file__).resolve().parent.parent / "scripts" / "ops_watchdog.py")
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)


# ── evaluate(): debounce + dedup + recovery ────────────────────────────────
def test_evaluate_all_ok_no_alerts():
    probes = {"backend": {"ok": True, "detail": "200"},
              "disk": {"ok": True, "detail": "50GB free"}}
    alerts, state = ow.evaluate({}, probes, debounce=2)
    assert alerts == []
    assert state["backend"] == {"fail": 0, "alerted": False}


def test_evaluate_debounce_suppresses_first_failure():
    # One failed cycle must NOT alert (debounce=2) — a 45s deploy restart of the
    # backend should never page.
    probes = {"backend": {"ok": False, "detail": "connection refused"}}
    alerts, state = ow.evaluate({}, probes, debounce=2)
    assert alerts == []
    assert state["backend"]["fail"] == 1
    assert state["backend"]["alerted"] is False


def test_evaluate_alerts_after_debounce_threshold():
    prev = {"backend": {"fail": 1, "alerted": False}}
    probes = {"backend": {"ok": False, "detail": "connection refused"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert len(alerts) == 1
    assert "🚨" in alerts[0] and "connection refused" in alerts[0]
    assert state["backend"]["alerted"] is True
    assert state["backend"]["fail"] == 2


def test_evaluate_dedup_no_repeat_while_down():
    prev = {"backend": {"fail": 2, "alerted": True}}
    probes = {"backend": {"ok": False, "detail": "connection refused"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert alerts == []                       # already alerted, stay silent
    assert state["backend"]["alerted"] is True


def test_evaluate_recovery_alert_after_down():
    prev = {"backend": {"fail": 5, "alerted": True}}
    probes = {"backend": {"ok": True, "detail": "200"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert len(alerts) == 1
    assert "✅" in alerts[0]
    assert state["backend"] == {"fail": 0, "alerted": False}


def test_evaluate_transient_below_threshold_no_recovery_spam():
    # Fail once (below debounce, never alerted) then recover -> total silence.
    prev = {"backend": {"fail": 1, "alerted": False}}
    probes = {"backend": {"ok": True, "detail": "200"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert alerts == []
    assert state["backend"] == {"fail": 0, "alerted": False}


def test_evaluate_absent_check_state_frozen():
    # When the backend is down the IO layer omits the ops sub-checks entirely
    # (they are unreachable, not "recovered"); their prior state must carry over
    # untouched so no spurious recovery/alert fires for them.
    prev = {"bot_heartbeat": {"fail": 2, "alerted": True}}
    probes = {"backend": {"ok": False, "detail": "refused"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    # only the backend transition (its first fail) is considered; heartbeat frozen
    assert all("heartbeat" not in a.lower() and "бот" not in a.lower() for a in alerts)
    assert state["bot_heartbeat"] == {"fail": 2, "alerted": True}


def test_evaluate_multiple_checks_alert_together():
    prev = {"backend": {"fail": 1, "alerted": False},
            "disk": {"fail": 1, "alerted": False}}
    probes = {"backend": {"ok": False, "detail": "refused"},
              "disk": {"ok": False, "detail": "4.0GB free (min 10.0GB)"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert len(alerts) == 2
    assert state["backend"]["alerted"] and state["disk"]["alerted"]


# ── probe_all(): HTTP + disk IO composed via injection ─────────────────────
def _fake_disk(free_gb):
    return lambda _p: (500 * 1024**3, 0, int(free_gb * 1024**3))


def test_probe_all_backend_up_probes_ops():
    seen = {}

    def http_get(path):
        seen[path] = True
        return {"/health": 200,
                "/api/jarvis/ops/heartbeat": 200,
                "/api/jarvis/ops/cloudflared": 200,
                "/api/jarvis/ops/restarts": 200}[path]

    probes = ow.probe_all(http_get, _fake_disk(50), min_disk_gb=10.0)
    assert probes["backend"]["ok"] is True
    for k in ("bot_heartbeat", "cloudflared", "restarts", "disk"):
        assert k in probes and probes[k]["ok"] is True
    assert "/api/jarvis/ops/heartbeat" in seen


def test_probe_all_backend_down_omits_subchecks():
    def http_get(path):
        if path == "/health":
            return None            # connection refused / timeout
        raise AssertionError("must not probe ops endpoints when backend is down")

    probes = ow.probe_all(http_get, _fake_disk(50), min_disk_gb=10.0)
    assert probes["backend"]["ok"] is False
    assert "bot_heartbeat" not in probes and "cloudflared" not in probes
    assert probes["disk"]["ok"] is True     # disk is local, always checked


def test_probe_all_ops_503_is_not_ok():
    def http_get(path):
        return {"/health": 200,
                "/api/jarvis/ops/heartbeat": 503,      # bot heartbeat stale
                "/api/jarvis/ops/cloudflared": 200,
                "/api/jarvis/ops/restarts": 503}[path]  # restart storm

    probes = ow.probe_all(http_get, _fake_disk(50), min_disk_gb=10.0)
    assert probes["bot_heartbeat"]["ok"] is False
    assert probes["restarts"]["ok"] is False
    assert probes["cloudflared"]["ok"] is True


def test_probe_all_disk_low_flagged():
    def http_get(path):
        return 200
    probes = ow.probe_all(http_get, _fake_disk(4.2), min_disk_gb=10.0)
    assert probes["disk"]["ok"] is False
    assert "4.2" in probes["disk"]["detail"]


# ── build_alert(): text ────────────────────────────────────────────────────
def test_build_alert_down_has_siren_and_detail():
    txt = ow.build_alert("backend", "down", "connection refused")
    assert "🚨" in txt and "connection refused" in txt
    assert "backend" in txt.lower() or "8010" in txt


def test_build_alert_recovered_has_check():
    txt = ow.build_alert("disk", "recovered", "12.0GB free")
    assert "✅" in txt and "12.0GB" in txt


# ── parse_token(): pure .env extraction (mirrors boot_watch_check) ─────────
def test_parse_token_reads_telegram_bot_token():
    env = 'FOO=bar\nTELEGRAM_BOT_TOKEN=123:abc\nBAZ=qux\n'
    assert ow.parse_token(env) == "123:abc"


def test_parse_token_missing_returns_empty():
    assert ow.parse_token("NOTHING=here\n") == ""
