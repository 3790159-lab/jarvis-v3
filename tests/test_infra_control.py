# -*- coding: utf-8 -*-
"""DEV-12 infra escape hatch — app.services.infra_control.

$0, mocks only: no real subprocess/schtasks/service ever runs from these tests.
``run``/``popen``/``sleep``/``backend_check`` are injected fakes throughout —
see feedback memory jarvis-parallel-session-spec-conflicts / no-unrequested-
indirection: the allowlist below is the literal set from the task spec, not a
config lookup.
"""
from __future__ import annotations

import pytest

from app.services import infra_control as ic


# ---------------------------------------------------------------------------
# Allowlist (requirement 3: exactly these three, no arbitrary shell/input)
# ---------------------------------------------------------------------------

def test_allowed_targets_are_exactly_the_three_spec_targets():
    assert set(ic.ALLOWED_TARGETS) == {"cloudflared", "backend", "bot"}


@pytest.mark.parametrize("target", ["cloudflared", "backend", "bot"])
def test_is_allowed_target_true_for_spec_targets(target):
    assert ic.is_allowed_target(target) is True


@pytest.mark.parametrize("target", ["", "shell", "rm -rf /", "cloudflared; rm -rf /",
                                     "Cloudflared", "backend ", "BOT", "cmd.exe"])
def test_is_allowed_target_false_for_anything_else(target):
    assert ic.is_allowed_target(target) is False


def test_restart_raises_on_disallowed_target_and_never_calls_run():
    calls = []
    with pytest.raises(ValueError):
        ic.restart("rm -rf /", run=lambda *a, **k: calls.append(a) or _Res("Running"))
    assert calls == [], "must reject before ever touching subprocess"


# ---------------------------------------------------------------------------
# status readers — all read-only, no elevation needed
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_cloudflared_status_parses_running():
    run = lambda *a, **k: _Res("Running\r\n")
    assert ic.cloudflared_status(run=run) == "Running"


def test_cloudflared_status_parses_stoppending():
    run = lambda *a, **k: _Res("StopPending\r\n")
    assert ic.cloudflared_status(run=run) == "StopPending"


def test_cloudflared_status_empty_stdout_means_not_found():
    run = lambda *a, **k: _Res("")
    assert ic.cloudflared_status(run=run) == "NotFound"


def test_cloudflared_status_run_exception_is_unknown_not_a_crash():
    def run(*a, **k):
        raise OSError("powershell missing")
    assert ic.cloudflared_status(run=run) == "unknown"


def test_backend_status_running_when_check_true():
    assert ic.backend_status(check=lambda: True) == "Running"


def test_backend_status_down_when_check_false():
    assert ic.backend_status(check=lambda: False) == "Down"


def test_backend_status_down_when_check_raises():
    def check():
        raise ConnectionRefusedError("refused")
    assert ic.backend_status(check=check) == "Down"


def test_bot_status_running_when_heartbeat_fresh(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text("1000", encoding="utf-8")
    assert ic.bot_status(now=lambda: 1010.0, heartbeat_path=hb, max_age_sec=180) == "Running"


def test_bot_status_down_when_heartbeat_stale(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text("1000", encoding="utf-8")
    assert ic.bot_status(now=lambda: 5000.0, heartbeat_path=hb, max_age_sec=180) == "Down"


def test_bot_status_down_when_heartbeat_file_missing(tmp_path):
    hb = tmp_path / "does_not_exist.txt"
    assert ic.bot_status(now=lambda: 1.0, heartbeat_path=hb) == "Down"


def test_status_all_aggregates_all_three():
    out = ic.status_all(run=lambda *a, **k: _Res("Running"), backend_check=lambda: True)
    assert out == {"cloudflared": "Running", "backend": "Running", "bot": out["bot"]}
    assert set(out.keys()) == {"cloudflared", "backend", "bot"}


# ---------------------------------------------------------------------------
# restart_cloudflared — schtasks trigger + unprivileged poll (req 5, 6)
# ---------------------------------------------------------------------------

def test_restart_cloudflared_triggers_the_registered_scheduled_task():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return _Res("Running")

    ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                            sleep=lambda s: None)
    trigger_calls = [c for c in calls if c[:2] == ["schtasks", "/Run"]]
    assert len(trigger_calls) == 1
    assert "JarvisInfraRestartCloudflared" in trigger_calls[0]


def test_restart_cloudflared_reports_before_and_after():
    # first call = "before" status read, second = trigger, rest = polls
    stdouts = iter(["StopPending", "Running", "Running"])

    def run(cmd, **k):
        if cmd[:2] == ["schtasks", "/Run"]:
            return _Res("")
        return _Res(next(stdouts, "Running"))

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None)
    assert result["before"] == "StopPending"
    assert result["after"] == "Running"
    assert result["ok"] is True


def test_restart_cloudflared_times_out_if_never_running():
    def run(cmd, **k):
        if cmd[:2] == ["schtasks", "/Run"]:
            return _Res("")
        return _Res("StopPending")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None)
    assert result["ok"] is False
    assert result["after"] == "StopPending"


def test_restart_cloudflared_trigger_failure_is_honest_not_silent():
    def run(cmd, **k):
        if cmd[:2] == ["schtasks", "/Run"]:
            raise OSError("schtasks not found")
        return _Res("Stopped")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None)
    assert result["ok"] is False
    assert result["before"] == result["after"] == "Stopped"
    assert "failed" in result["detail"]


# ---------------------------------------------------------------------------
# restart_backend — direct action script + poll (no elevation, no scheduled task)
# ---------------------------------------------------------------------------

def test_restart_backend_runs_the_action_script():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return _Res("")

    ic.restart_backend(run=run, poll_timeout=0.01, poll_interval=0.001,
                        sleep=lambda s: None, backend_check=lambda: True)
    assert any("infra_restart_backend.ps1" in str(part) for c in calls for part in c)


def test_restart_backend_reports_before_and_after():
    checks = iter([False, False, True])

    def run(cmd, **k):
        return _Res("")

    result = ic.restart_backend(run=run, poll_timeout=0.01, poll_interval=0.001,
                                 sleep=lambda s: None,
                                 backend_check=lambda: next(checks, True))
    assert result["before"] == "Down"
    assert result["after"] == "Running"
    assert result["ok"] is True


def test_restart_backend_action_script_failure_is_honest():
    def run(cmd, **k):
        raise OSError("powershell not found")

    result = ic.restart_backend(run=run, poll_timeout=0.01, poll_interval=0.001,
                                 sleep=lambda s: None, backend_check=lambda: False)
    assert result["ok"] is False
    assert result["before"] == result["after"] == "Down"


# ---------------------------------------------------------------------------
# restart_bot — self-referential; spawns a DETACHED watcher, no synchronous poll
# ---------------------------------------------------------------------------

def test_restart_bot_spawns_the_detached_watcher_script():
    calls = []

    def popen(cmd, **k):
        calls.append(cmd)
        return object()

    ic.restart_bot(popen=popen)
    assert len(calls) == 1
    assert any("infra_restart_bot_watcher.ps1" in str(part) for part in calls[0])


def test_restart_bot_never_blocks_waiting_for_the_watcher():
    """popen (not run/call/check_call) must be used — a blocking wait would
    hang forever because the watcher kills this very process."""
    waited = []

    def popen(cmd, **k):
        class _Proc:
            def wait(self_inner, *a, **k2):
                waited.append(True)
        return _Proc()

    ic.restart_bot(popen=popen)
    assert waited == [], "restart_bot must not wait() on the spawned watcher"


def test_restart_bot_trigger_failure_is_honest():
    def popen(cmd, **k):
        raise OSError("powershell not found")

    result = ic.restart_bot(popen=popen)
    assert result["ok"] is False


def test_restart_bot_reports_before_but_after_is_unknown():
    result = ic.restart_bot(popen=lambda cmd, **k: object())
    assert result["before"] in ("Running", "Down")
    assert result["after"] is None


# ---------------------------------------------------------------------------
# is_elevated — read-only WindowsPrincipal probe (DEV-12a)
# ---------------------------------------------------------------------------

def test_is_elevated_true_when_windows_principal_says_true():
    run = lambda *a, **k: _Res("True\r\n")
    assert ic.is_elevated(run=run) is True


def test_is_elevated_false_when_windows_principal_says_false():
    run = lambda *a, **k: _Res("False\r\n")
    assert ic.is_elevated(run=run) is False


def test_is_elevated_false_on_run_exception_never_assumes_elevation():
    def run(*a, **k):
        raise OSError("powershell missing")
    assert ic.is_elevated(run=run) is False


# ---------------------------------------------------------------------------
# restart_cloudflared cascade (DEV-12a): elevated -> path A, else -> path B
# ---------------------------------------------------------------------------

def _script(cmd):
    return cmd[-1] if cmd and cmd[0] == "powershell" else ""


def test_restart_cloudflared_path_a_used_when_elevated_no_scheduled_task_touched():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return _Res("Running")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: True)
    assert result["path"] == "A"
    assert result["ok"] is True
    assert not any(c[:2] == ["schtasks", "/Run"] for c in calls)
    assert not any(c[:3] == ["schtasks", "/Query", "/TN"] for c in calls)


def test_restart_cloudflared_path_a_kills_wedged_process_when_stoppending():
    calls = []
    statuses = iter(["StopPending", "Running"])

    def run(cmd, **k):
        calls.append(cmd)
        if "Get-Service" in _script(cmd):
            return _Res(next(statuses, "Running"))
        return _Res("")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: True)
    assert result["ok"] is True
    kill_calls = [c for c in calls if "Stop-Process" in _script(c)]
    assert len(kill_calls) == 1
    start_calls = [c for c in calls if "Start-Service" in _script(c)]
    assert len(start_calls) == 1


def test_restart_cloudflared_path_a_skips_kill_when_not_stoppending():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if "Get-Service" in _script(cmd):
            return _Res("Stopped")
        return _Res("")

    ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                            sleep=lambda s: None, elevated_check=lambda: True)
    assert not any("Stop-Process" in _script(c) for c in calls)


def test_restart_cloudflared_path_a_start_service_failure_is_honest():
    def run(cmd, **k):
        script = _script(cmd)
        if "Get-Service" in script:
            return _Res("Stopped")
        if "Start-Service" in script:
            raise OSError("access denied")
        return _Res("")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: True)
    assert result["ok"] is False
    assert result["path"] == "A"
    assert result["before"] == result["after"] == "Stopped"


def test_restart_cloudflared_path_b_skips_registration_when_task_already_registered():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if cmd[:3] == ["schtasks", "/Query", "/TN"]:
            return _Res("", returncode=0)
        if cmd[:2] == ["schtasks", "/Run"]:
            return _Res("")
        return _Res("Running")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: False)
    assert result["ok"] is True
    assert result["path"] == "B"
    assert not any("register_infra_restart_tasks.ps1" in _script(c) for c in calls)


def test_restart_cloudflared_path_b_registers_missing_task_on_the_fly_then_triggers():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if cmd[:3] == ["schtasks", "/Query", "/TN"]:
            return _Res("", returncode=1)
        if "register_infra_restart_tasks.ps1" in _script(cmd):
            return _Res("", returncode=0)
        if cmd[:2] == ["schtasks", "/Run"]:
            return _Res("")
        return _Res("Running")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: False)
    assert result["path"] == "B"
    assert result["ok"] is True
    register_calls = [c for c in calls if "register_infra_restart_tasks.ps1" in _script(c)]
    assert len(register_calls) == 1
    trigger_calls = [c for c in calls if c[:2] == ["schtasks", "/Run"]]
    assert len(trigger_calls) == 1


def test_restart_cloudflared_path_b_honest_failure_when_registration_also_fails():
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if cmd[:3] == ["schtasks", "/Query", "/TN"]:
            return _Res("", returncode=1)
        if "register_infra_restart_tasks.ps1" in _script(cmd):
            return _Res("", returncode=1)
        return _Res("Stopped")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None, elevated_check=lambda: False)
    assert result["ok"] is False
    assert result["path"] == "B"
    assert "физическ" in result["detail"].lower()
    assert not any(c[:2] == ["schtasks", "/Run"] for c in calls), \
        "must never fire /Run against an unregistered task"


def test_restart_cloudflared_defaults_to_is_elevated_probe_when_not_injected():
    def run(cmd, **k):
        script = _script(cmd)
        if "IsInRole" in script:
            return _Res("True")
        return _Res("")

    result = ic.restart_cloudflared(run=run, poll_timeout=0.01, poll_interval=0.001,
                                     sleep=lambda s: None)
    assert result["path"] == "A"


# ---------------------------------------------------------------------------
# restart() dispatcher — routes to exactly one target-specific function
# ---------------------------------------------------------------------------

def test_restart_dispatches_cloudflared_only(monkeypatch):
    called = []
    monkeypatch.setattr(ic, "restart_cloudflared", lambda **k: called.append("cloudflared") or {"ok": True})
    monkeypatch.setattr(ic, "restart_backend", lambda **k: called.append("backend") or {"ok": True})
    monkeypatch.setattr(ic, "restart_bot", lambda **k: called.append("bot") or {"ok": True})
    ic.restart("cloudflared")
    assert called == ["cloudflared"]


def test_restart_dispatches_backend_only(monkeypatch):
    called = []
    monkeypatch.setattr(ic, "restart_cloudflared", lambda **k: called.append("cloudflared") or {"ok": True})
    monkeypatch.setattr(ic, "restart_backend", lambda **k: called.append("backend") or {"ok": True})
    monkeypatch.setattr(ic, "restart_bot", lambda **k: called.append("bot") or {"ok": True})
    ic.restart("backend")
    assert called == ["backend"]


def test_restart_dispatches_bot_only(monkeypatch):
    called = []
    monkeypatch.setattr(ic, "restart_cloudflared", lambda **k: called.append("cloudflared") or {"ok": True})
    monkeypatch.setattr(ic, "restart_backend", lambda **k: called.append("backend") or {"ok": True})
    monkeypatch.setattr(ic, "restart_bot", lambda **k: called.append("bot") or {"ok": True})
    ic.restart("bot")
    assert called == ["bot"]
