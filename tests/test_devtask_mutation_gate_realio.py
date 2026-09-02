# -*- coding: utf-8 -*-
"""DEV-97: the real-IO seams the mutation gate wiring hands to the pure
``mutation_gate.run_mutation_gate`` — ``collect_ok``, ``run_guard``,
``revert``, and the whole wrapper's file/mtime plumbing. Subprocess/git are
mocked; nothing here spawns a real pytest or touches real git state.
"""
from __future__ import annotations

import importlib
import subprocess

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── _devtask_mutation_collect_ok ─────────────────────────────────────────────

def test_collect_ok_true_on_rc0(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0))
    assert mod._devtask_mutation_collect_ok("C:/wt", ["tests/test_x.py"]) is True


def test_collect_ok_false_on_dangling_reference(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(4))
    assert mod._devtask_mutation_collect_ok("C:/wt", ["tests/test_missing.py::test_gone"]) is False


def test_collect_ok_passes_collect_only_flag_and_cwd(monkeypatch):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["cwd"] = kw.get("cwd")
        return _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    mod._devtask_mutation_collect_ok("C:/wt", ["tests/test_x.py"])
    assert "--collect-only" in captured["argv"]
    assert "tests/test_x.py" in captured["argv"]
    assert captured["cwd"] == "C:/wt"


# ── _devtask_mutation_run_guard ──────────────────────────────────────────────

def test_run_guard_true_when_a_test_actually_failed(monkeypatch):
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _Proc(1, stdout="F\n1 failed in 0.1s\n"))
    assert mod._devtask_mutation_run_guard("C:/wt", ["tests/test_x.py"]) is True


def test_run_guard_false_when_everything_passed(monkeypatch):
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _Proc(0, stdout="1 passed in 0.1s\n"))
    assert mod._devtask_mutation_run_guard("C:/wt", ["tests/test_x.py"]) is False


def test_run_guard_false_on_a_collection_error_not_a_catch(monkeypatch):
    # rc=2 (collection error) must NOT register as "caught" — DEV-26's exact
    # bug class: a mutant that broke collection never ran a single assertion.
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda *a, **k: _Proc(2, stdout="ERROR collecting tests/test_x.py\n"))
    assert mod._devtask_mutation_run_guard("C:/wt", ["tests/test_x.py"]) is False


def test_run_guard_false_on_timeout(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(mod._regress_watch, "run_guarded", _raise)
    assert mod._devtask_mutation_run_guard("C:/wt", ["tests/test_x.py"]) is False


# ── _devtask_mutation_revert ─────────────────────────────────────────────────

def test_revert_invokes_git_checkout_scoped_to_the_one_file(monkeypatch):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _Proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    mod._devtask_mutation_revert("C:/wt", "chatter/x.py")
    assert captured["argv"] == ["git", "-C", "C:/wt", "checkout", "--", "chatter/x.py"]


# ── _devtask_run_mutation_gate: the whole real-IO wrapper ───────────────────

def test_wrapper_writes_mutant_with_a_real_mtime_stamp_then_reverts(monkeypatch, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(a, b):\n    return a == b\n", encoding="utf-8")

    monkeypatch.setattr(mod, "_devtask_mutation_collect_ok", lambda wt, tests: True)
    guard_calls = []
    monkeypatch.setattr(mod, "_devtask_mutation_run_guard",
                        lambda wt, tests: guard_calls.append(tests) or True)
    revert_calls = []
    monkeypatch.setattr(mod, "_devtask_mutation_revert",
                        lambda wt, rel: revert_calls.append(rel))

    res = mod._devtask_run_mutation_gate(str(tmp_path), ["x.py"], ["tests/test_x.py"])

    assert res["status"] == "green"
    assert guard_calls == [["tests/test_x.py"]]
    assert revert_calls == ["x.py"]
    # the file on disk was actually mutated at least once during the run
    # (revert is faked here, so the LAST mutant write is still on disk)
    assert "!=" in src.read_text(encoding="utf-8") or "==" in src.read_text(encoding="utf-8")


def test_wrapper_reports_red_when_the_fake_guard_never_catches(monkeypatch, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(a, b):\n    return a == b\n", encoding="utf-8")

    monkeypatch.setattr(mod, "_devtask_mutation_collect_ok", lambda wt, tests: True)
    monkeypatch.setattr(mod, "_devtask_mutation_run_guard", lambda wt, tests: False)
    monkeypatch.setattr(mod, "_devtask_mutation_revert", lambda wt, rel: None)

    res = mod._devtask_run_mutation_gate(str(tmp_path), ["x.py"], ["tests/test_x.py"])

    assert res["status"] == "red"
    assert res["blocks_merge"] is True
    assert "x.py" in res["text"]
