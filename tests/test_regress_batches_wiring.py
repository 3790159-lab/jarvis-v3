# -*- coding: utf-8 -*-
"""Wiring of the batched RAM-guarded regress into the bot (Этап 1, хвост #4).

Verifies that the full regress (merge gate + /regress) now runs as sequential
RAM-guarded batches through the watchdog, aggregates to one verdict, refuses to
start a batch into RAM starvation, and refreshes the baseline after a clean
full /regress run. $0, mocks only — no real pytest, no real psutil, no network.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
rb = mod._regress_batches
ADMIN = "237616472"


class _Proc:
    def __init__(self, out="1 passed in 0.1s"):
        self.returncode = 0
        self.stdout = out


# ── merge-gate full regress runs in batches through run_guarded ─────────────
def test_devtask_run_regress_batches_and_labels(monkeypatch):
    monkeypatch.setenv("REGRESS_BATCH_SIZE", "2")
    monkeypatch.setattr(mod, "_regress_discover_tests",
                        lambda root: ["tests/test_a.py", "tests/test_b.py",
                                      "tests/test_c.py", "tests/test_d.py"])
    monkeypatch.setattr(rb, "free_gb", lambda *a, **k: 8.0)   # plenty of RAM
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 0})
    monkeypatch.setattr(mod, "_devtask_pytest_env", lambda: {"E": "1"})
    calls = []

    def _fake_guarded(cmd, **kw):
        calls.append((cmd, kw.get("label")))
        return _Proc("0 failed, 2 passed in 0.1s")
    monkeypatch.setattr(mod._regress_watch, "run_guarded", _fake_guarded)

    res = mod._devtask_run_regress("C:/wt")

    assert len(calls) == 2                         # two batches of two files
    assert all("pytest" in c[0] for c in calls)
    assert any("merge-gate" in (lbl or "") for _c, lbl in calls)
    assert res["ok"] is True and "2/2" in res["text"]


# ── RAM-guard: a starved batch is NEVER started ─────────────────────────────
def test_devtask_run_regress_ram_guard_blocks(monkeypatch):
    monkeypatch.setenv("REGRESS_RAM_RETRIES", "2")
    monkeypatch.setattr(mod, "_regress_discover_tests",
                        lambda root: ["tests/test_a.py", "tests/test_b.py"])
    monkeypatch.setattr(rb, "free_gb", lambda *a, **k: 0.5)   # starved, never recovers
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 0})
    monkeypatch.setattr(mod, "_devtask_pytest_env", lambda: {})
    ran = []
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: ran.append(1) or _Proc())

    res = mod._devtask_run_regress("C:/wt")

    assert ran == []                               # no batch launched into swap
    assert res["ok"] is False and "RAM" in res["text"]


# ── /regress refreshes the baseline after a clean full run ──────────────────
def test_slash_regress_refreshes_baseline_when_clean(monkeypatch):
    monkeypatch.setattr(mod, "_run_regress_batched",
                        lambda **kw: {"status": "complete",
                                      "summary": {"failed": 0, "passed": 120, "errors": 0},
                                      "batches_run": 3, "batches_total": 3})
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)   # no baseline yet
    written = {}
    monkeypatch.setattr(rb, "write_baseline",
                        lambda path, summary: written.update(path=path, summary=summary))
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod._REGRESS_RUNNING = False

    mod._regress_run(ADMIN)

    assert written.get("summary") == {"failed": 0, "passed": 120, "errors": 0}
    assert sent and mod._REGRESS_RUNNING is False


def test_slash_regress_no_baseline_write_when_ram_exhausted(monkeypatch):
    monkeypatch.setattr(mod, "_run_regress_batched",
                        lambda **kw: {"status": "ram_exhausted",
                                      "summary": {"failed": 0, "passed": 10, "errors": 0},
                                      "batches_run": 1, "batches_total": 4,
                                      "free_gb": 0.7, "min_free_gb": 3.0, "ram_retries": 6})
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    wrote = []
    monkeypatch.setattr(rb, "write_baseline", lambda *a, **k: wrote.append(1))
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod._REGRESS_RUNNING = False

    mod._regress_run(ADMIN)

    assert wrote == []                              # aborted run must NOT set a floor
    assert any("RAM" in s for s in sent)            # honest failure surfaced


# ── slash /regress spawn still goes through the watchdog ─────────────────────
def test_run_regress_batched_uses_run_guarded(monkeypatch):
    monkeypatch.setattr(mod, "_regress_discover_tests", lambda root: ["tests/test_x.py"])
    monkeypatch.setattr(rb, "free_gb", lambda *a, **k: 8.0)
    captured = {}
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda cmd, **kw: captured.update(cmd=cmd, kw=kw) or _Proc())
    res = mod._run_regress_batched(cwd="C:/jarvis", env=None, timeout_s=600,
                                   creationflags=0, state_dir=mod._REGRESS_WATCH_DIR,
                                   label_prefix="regress")
    assert "pytest" in captured["cmd"] and captured["kw"]["cwd"] == "C:/jarvis"
    assert "regress" in captured["kw"]["label"]
    assert res["status"] == "complete"
