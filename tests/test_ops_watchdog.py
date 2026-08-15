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


# ── причина алерта: вторая, ДРУГАЯ причина обязана прозвучать ──────────────
# Дефект найден фактом 2026-08-11: чек worktree был красным 1669 циклов подряд
# из-за законной правки тумблера, alerted=True — и настоящий недеплоенный код
# второго алерта уже НЕ дал бы. Сторож, поставленный ровно на это, был выключен
# собственным законным срабатыванием. Дефект общий для всех девяти проверок.

def test_a_second_different_reason_alerts_again():
    prev = {"worktree": {"fail": 9, "alerted": True, "alerted_reason": "dirty:a.yaml"}}
    probes = {"worktree": {"ok": False, "detail": "модифицировано 2 (a.yaml, b.py)",
                           "reason": "dirty:a.yaml|b.py"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert len(alerts) == 1, "новая причина утонула в дедупе старой"
    assert "b.py" in alerts[0]
    assert state["worktree"]["alerted_reason"] == "dirty:a.yaml|b.py"


def test_the_second_alert_says_it_is_a_new_reason_not_a_new_outage():
    """Иначе владелец видит второе 🚨 и решает, что упало дважды."""
    prev = {"disk": {"fail": 9, "alerted": True, "alerted_reason": "low_space"}}
    probes = {"disk": {"ok": False, "detail": "проверка упала: WinError 5",
                       "reason": "unreadable"}}
    alerts, _ = ow.evaluate(prev, probes, debounce=2)
    assert "DOWN" not in alerts[0]
    assert "причин" in alerts[0].lower()


def test_the_same_reason_stays_silent():
    prev = {"worktree": {"fail": 9, "alerted": True, "alerted_reason": "dirty:a.yaml"}}
    probes = {"worktree": {"ok": False, "detail": "модифицировано 1 (a.yaml)",
                           "reason": "dirty:a.yaml"}}
    alerts, _ = ow.evaluate(prev, probes, debounce=2)
    assert alerts == []


def test_a_changing_detail_with_a_stable_reason_stays_silent():
    """Ловушка этой правки: сравнивать `detail` нельзя. У диска в нём гигабайты,
    у heartbeat — секунды, и они меняются КАЖДЫЙ цикл. Сравнение по тексту
    превратило бы починку дедупа в шторм раз в 30 секунд."""
    prev = {"disk": {"fail": 9, "alerted": True, "alerted_reason": "low_space"}}
    alerts, _ = ow.evaluate(
        prev, {"disk": {"ok": False, "detail": "8.1GB free (min 10.0GB)",
                        "reason": "low_space"}}, debounce=2)
    assert alerts == []


def test_a_probe_without_a_reason_keeps_the_one_shot_behaviour():
    """Проба, не объявившая причину, ведёт себя ровно как раньше: один алерт на
    падение. Молчаливое «раз причины нет, значит она каждый раз новая» дало бы
    шторм на пробах, которых эта правка не касалась."""
    prev = {"backend": {"fail": 9, "alerted": True, "alerted_reason": "backend"}}
    alerts, _ = ow.evaluate(
        prev, {"backend": {"ok": False, "detail": "HTTP 502"}}, debounce=2)
    assert alerts == []


def test_legacy_state_without_a_reason_does_not_alert_on_upgrade():
    """Первый цикл после выкатки: в стейте на диске поля ещё нет. Причина
    ПРИНИМАЕТСЯ молча — иначе выкатка сама разошлёт 🚨 по каждому красному чеку,
    и владелец получит шторм ровно за то, что мы починили дедуп."""
    prev = {"worktree": {"fail": 1669, "alerted": True}}
    probes = {"worktree": {"ok": False, "detail": "модифицировано 1 (settings.yaml)",
                           "reason": "dirty:settings.yaml"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert alerts == []
    assert state["worktree"]["alerted_reason"] == "dirty:settings.yaml"


def test_recovery_clears_the_remembered_reason():
    """Иначе следующее падение по ТОЙ ЖЕ причине промолчит: она осталась
    «уже объявленной»."""
    prev = {"worktree": {"fail": 9, "alerted": True, "alerted_reason": "dirty:a.yaml"}}
    alerts, state = ow.evaluate(
        prev, {"worktree": {"ok": True, "detail": "транк, чисто"}}, debounce=2)
    assert len(alerts) == 1 and "✅" in alerts[0]
    assert state["worktree"] == {"fail": 0, "alerted": False}


def test_the_reason_is_remembered_on_the_very_first_down_alert():
    prev = {"worktree": {"fail": 1, "alerted": False}}
    probes = {"worktree": {"ok": False, "detail": "модифицировано 1 (a.yaml)",
                           "reason": "dirty:a.yaml"}}
    alerts, state = ow.evaluate(prev, probes, debounce=2)
    assert len(alerts) == 1 and "🚨" in alerts[0] and "DOWN" in alerts[0]
    assert state["worktree"]["alerted_reason"] == "dirty:a.yaml"


def test_suppressed_down_remembers_nothing():
    """Окно загрузки: считаем, но молчим. Запомнить причину, о которой владельцу
    не сказали, значит промолчать и потом."""
    probes = {"backend": {"ok": False, "detail": "refused", "reason": "no_response"}}
    alerts, state = ow.evaluate({"backend": {"fail": 5, "alerted": False}}, probes,
                                debounce=2, suppress_down=True)
    assert alerts == []
    assert "alerted_reason" not in state["backend"]


def test_disk_reason_is_stable_while_free_space_drifts():
    """Проба обязана давать причину, не зависящую от замера."""
    a = ow.probe_all(lambda p: 200, _fake_disk(4.0))["disk"]
    b = ow.probe_all(lambda p: 200, _fake_disk(3.5))["disk"]
    assert a["detail"] != b["detail"], "предпосылка теста сломана"
    assert a["reason"] == b["reason"] == "low_space"


def test_backend_down_and_backend_erroring_are_different_reasons():
    """«не отвечает» и «отвечает 500» — разные аварии: первая про процесс,
    вторая про код внутри живого процесса."""
    refused = ow.probe_all(lambda p: None, _fake_disk(50))["backend"]
    broken = ow.probe_all(lambda p: 500, _fake_disk(50))["backend"]
    assert refused["reason"] != broken["reason"]
