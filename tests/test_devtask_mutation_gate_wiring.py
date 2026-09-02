# -*- coding: utf-8 -*-
"""DEV-97: wiring of the generic mutation gate into the dev_task merge report.

``app/services/devtask/mutation_gate.py`` is the pure decision logic (own
test file). This file is the seam: does ``_devtask_run_targeted`` actually
call it, only after a green targeted run, and does a red mutation verdict
actually flip the merge gate back to red? All on mocks — no real pytest/git.
"""
from __future__ import annotations

import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


class _P:
    def __init__(self, stdout, returncode):
        self.stdout = stdout
        self.returncode = returncode


def _wire_targets(monkeypatch, changed=("app/services/devtask/queue.py",),
                   tests=("tests/test_queue.py",)):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: list(changed))
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: list(tests))
    monkeypatch.setattr(tt, "map_paths_to_tests", lambda *a, **k: list(tests))


# ── the mutation gate never runs on a RED targeted-test result ──────────────
def test_mutation_gate_is_not_invoked_when_targeted_tests_fail(monkeypatch):
    _wire_targets(monkeypatch)
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _P("1 failed, 2 passed in 0.1s", 1))
    calls = []
    monkeypatch.setattr(mod, "_devtask_run_mutation_gate",
                        lambda *a, **k: calls.append(a) or {})
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is False
    assert calls == []


# ── a green targeted run DOES invoke the mutation gate ──────────────────────
def test_mutation_gate_is_invoked_after_a_green_targeted_run(monkeypatch, tmp_path):
    _wire_targets(monkeypatch, changed=("chatter/x.py",), tests=("tests/test_x.py",))
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _P("3 passed in 0.1s", 0))
    (tmp_path / "chatter").mkdir()
    (tmp_path / "chatter" / "x.py").write_text("X = 1\n", encoding="utf-8")
    calls = []

    def fake_gate(worktree, source_files, guard_tests):
        calls.append((worktree, source_files, guard_tests))
        return {"status": "green", "reason": None, "text": "🧬 мутации: ✅ все 1 пойманы",
                "blocks_merge": False, "tested": 1, "total": 1}

    monkeypatch.setattr(mod, "_devtask_run_mutation_gate", fake_gate)
    res = mod._devtask_run_targeted(str(tmp_path), "base1")
    assert len(calls) == 1
    worktree, source_files, guard_tests = calls[0]
    assert worktree == str(tmp_path)
    assert source_files == ["chatter/x.py"]
    assert guard_tests == ["tests/test_x.py"]
    assert res["ok"] is True
    assert "🧬 мутации" in res["text"]
    assert res["mutation"]["status"] == "green"


# ── a surviving mutant flips a green targeted run back to red ───────────────
def test_a_red_mutation_gate_flips_ok_to_false(monkeypatch, tmp_path):
    _wire_targets(monkeypatch, changed=("chatter/x.py",), tests=("tests/test_x.py",))
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _P("3 passed in 0.1s", 0))
    (tmp_path / "chatter").mkdir()
    (tmp_path / "chatter" / "x.py").write_text("X = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        mod, "_devtask_run_mutation_gate",
        lambda *a, **k: {"status": "red", "reason": "survivors",
                          "text": "🧬 мутации: 🔴 выжило 1 из 1\n"
                                  "• chatter/x.py:1 — bool literal True -> False (не поймано: tests/test_x.py)",
                          "blocks_merge": True, "tested": 1, "total": 1})

    res = mod._devtask_run_targeted(str(tmp_path), "base1")
    assert res["ok"] is False
    assert "выжило" in res["text"]
    assert "chatter/x.py:1" in res["text"]


# ── a budget cut-off is informational, not a block ───────────────────────────
def test_a_partial_mutation_gate_does_not_block_merge(monkeypatch, tmp_path):
    _wire_targets(monkeypatch, changed=("chatter/x.py",), tests=("tests/test_x.py",))
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _P("3 passed in 0.1s", 0))
    (tmp_path / "chatter").mkdir()
    (tmp_path / "chatter" / "x.py").write_text("X = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        mod, "_devtask_run_mutation_gate",
        lambda *a, **k: {"status": "partial", "reason": "budget_exceeded",
                          "text": "🧬 мутации: ⚠️ НЕ завершены — бюджет 300s исчерпан (2/9 прогнано, все пойманы)",
                          "blocks_merge": False, "tested": 2, "total": 9})

    res = mod._devtask_run_targeted(str(tmp_path), "base1")
    assert res["ok"] is True
    assert "НЕ завершены" in res["text"]


# ── docs-only diff never reaches the mutation gate at all ───────────────────
def test_docs_only_diff_never_calls_the_mutation_gate(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["docs/MASTER-PLAN.md"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    calls = []
    monkeypatch.setattr(mod, "_devtask_run_mutation_gate",
                        lambda *a, **k: calls.append(a) or {})
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["mode"] == "docs_only"
    assert calls == []


# ── a green run with NOTHING mutable (no matching source file on disk) still
#    skips cleanly through the real select_source_files, without ever calling
#    the real collect_ok/run_guard IO seams ───────────────────────────────────
def test_mutation_gate_receives_empty_source_files_when_nothing_exists_on_disk(monkeypatch):
    _wire_targets(monkeypatch, changed=("app/services/devtask/queue.py",),
                  tests=("tests/test_queue.py",))
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _P("3 passed in 0.1s", 0))
    calls = []
    monkeypatch.setattr(mod, "_devtask_run_mutation_gate",
                        lambda *a, **k: calls.append(a) or {"status": "skipped", "reason": "empty_diff",
                                                             "text": "🧬 мутации: дифф пуст — гейт не запускается",
                                                             "blocks_merge": False, "tested": 0, "total": 0})
    res = mod._devtask_run_targeted("C:/wt-does-not-exist", "base1")
    assert calls[0][1] == []  # source_files: the file doesn't exist under this fake worktree
    assert res["ok"] is True
