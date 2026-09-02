# -*- coding: utf-8 -*-
"""DEV-97: generic mutation gate for the dev_task merge report.

All orchestration here runs on injected fakes (no real pytest/git spawned) —
see ``app/services/devtask/mutation_gate.py`` module docstring for why that is
the point, not a shortcut.
"""
from __future__ import annotations

from app.services.devtask import mutation_gate as mg


# ── select_source_files: what this gate is even allowed to mutate ──────────

def test_select_source_files_keeps_existing_non_test_py_files():
    changed = ["chatter/payments/pricing.py", "tests/test_pricing.py",
               "docs/notes.md", "chatter/clients/x/settings.yaml"]
    got = mg.select_source_files(changed, exists=lambda rel: True)
    assert got == ["chatter/payments/pricing.py"]


def test_select_source_files_excludes_tests_at_any_depth():
    changed = ["tests/chatter/test_pricing.py", "app/x/test_helpers.py"]
    got = mg.select_source_files(changed, exists=lambda rel: True)
    assert got == []


def test_select_source_files_drops_deleted_files():
    changed = ["chatter/payments/pricing.py"]
    got = mg.select_source_files(changed, exists=lambda rel: False)
    assert got == []


def test_select_source_files_normalizes_backslashes():
    changed = ["chatter\\payments\\pricing.py"]
    got = mg.select_source_files(changed, exists=lambda rel: True)
    assert got == ["chatter/payments/pricing.py"]


# ── generate_mutants: pure AST mutation ──────────────────────────────────────

def test_comparison_operator_is_flipped():
    mutants = mg.generate_mutants("def f(a, b):\n    return a == b\n")
    assert len(mutants) == 1
    assert "Eq -> NotEq" in mutants[0].description
    assert "a != b" in mutants[0].source


def test_boolean_literal_is_flipped():
    mutants = mg.generate_mutants("FLAG = True\n")
    assert len(mutants) == 1
    assert "FLAG = False" in mutants[0].source


def test_boolop_and_or_is_flipped():
    mutants = mg.generate_mutants("def f(a, b):\n    return a and b\n")
    assert len(mutants) == 1
    assert "a or b" in mutants[0].source


def test_multiple_sites_yield_independent_single_mutants():
    src = "def f(a, b, c):\n    return a == b and b == c\n"
    mutants = mg.generate_mutants(src)
    # 2 comparisons + 1 boolop = 3 independent single-site mutants
    assert len(mutants) == 3
    # each mutant differs from the original in exactly one place
    originals_untouched = sum(1 for m in mutants if "a == b" in m.source) + \
        sum(1 for m in mutants if "b == c" in m.source)
    assert originals_untouched >= 2  # each mutant keeps the OTHER site intact


def test_source_with_nothing_mutable_yields_no_mutants():
    assert mg.generate_mutants("VALUE = 1\nNAME = 'x'\n") == []


def test_unparseable_source_yields_no_mutants_not_an_error():
    assert mg.generate_mutants("def f(:\n") == []


def test_mutant_line_number_points_at_the_mutated_site():
    src = "X = 1\nY = 1\ndef f(a, b):\n    return a == b\n"
    mutants = mg.generate_mutants(src)
    assert mutants[0].lineno == 4


# ── run_mutation_gate: orchestration on fakes ────────────────────────────────

def _fake_reader(mapping):
    return lambda rel: mapping[rel]


def _always_caught(*_a, **_k):
    return True


def _never_caught(*_a, **_k):
    return False


def _collect_ok(*_a, **_k):
    return True


def test_empty_source_files_skips_without_running_anything():
    calls = []
    result = mg.run_mutation_gate(
        [], ["tests/test_x.py"],
        read_text=lambda rel: calls.append(rel) or "",
        write_mutant_at=lambda *a: calls.append(a),
        revert=lambda *a: calls.append(a),
        collect_ok=lambda *a: calls.append(a) or True,
        run_guard=lambda *a: calls.append(a) or True,
    )
    assert result.status == "skipped"
    assert result.reason == "empty_diff"
    assert calls == []
    assert not result.blocks_merge


def test_empty_guard_tests_also_skips():
    result = mg.run_mutation_gate(
        ["a.py"], [],
        read_text=lambda rel: "X = 1\n",
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=lambda *a: True,
        run_guard=lambda *a: True,
    )
    assert result.status == "skipped"
    assert result.reason == "empty_diff"


def test_dangling_guard_test_is_red_not_a_survivor():
    calls = []
    result = mg.run_mutation_gate(
        ["a.py"], ["tests/test_missing.py::test_gone"],
        read_text=lambda rel: "def f(a, b):\n    return a == b\n",
        write_mutant_at=lambda *a: calls.append(("write", a)),
        revert=lambda *a: calls.append(("revert", a)),
        collect_ok=lambda tests: False,
        run_guard=lambda *a: calls.append(("run", a)) or True,
    )
    assert result.status == "red"
    assert result.reason == "dangling_guard_test"
    assert result.survivors == []
    # never even attempted a mutation once the guard itself can't collect
    assert calls == []


def test_no_mutable_sites_is_skipped_not_green():
    result = mg.run_mutation_gate(
        ["a.py"], ["tests/test_a.py"],
        read_text=lambda rel: "VALUE = 1\n",
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_always_caught,
    )
    assert result.status == "skipped"
    assert result.reason == "no_mutable_sites"


def test_all_mutants_caught_is_green():
    result = mg.run_mutation_gate(
        ["a.py"], ["tests/test_a.py"],
        read_text=_fake_reader({"a.py": "def f(a, b):\n    return a == b\n"}),
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_always_caught,
    )
    assert result.status == "green"
    assert result.tested == 1
    assert result.survivors == []
    assert not result.blocks_merge


def test_a_surviving_mutant_reddens_the_report():
    result = mg.run_mutation_gate(
        ["chatter/x.py"], ["tests/test_x.py"],
        read_text=_fake_reader({"chatter/x.py": "def f(a, b):\n    return a == b\n"}),
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_never_caught,
    )
    assert result.status == "red"
    assert result.reason == "survivors"
    assert result.blocks_merge
    assert len(result.survivors) == 1
    survivor = result.survivors[0]
    assert survivor.file == "chatter/x.py"
    assert survivor.line == 2
    assert "Eq -> NotEq" in survivor.mutation
    assert survivor.guard_tests == ["tests/test_x.py"]
    assert "chatter/x.py:2" in result.text
    assert "tests/test_x.py" in result.text


def test_revert_runs_even_when_run_guard_raises():
    reverted = []

    def _boom(*_a, **_k):
        raise RuntimeError("pytest exploded")

    try:
        mg.run_mutation_gate(
            ["a.py"], ["tests/test_a.py"],
            read_text=_fake_reader({"a.py": "def f(a, b):\n    return a == b\n"}),
            write_mutant_at=lambda *a: None,
            revert=lambda rel: reverted.append(rel),
            collect_ok=_collect_ok,
            run_guard=_boom,
        )
    except RuntimeError:
        pass
    assert reverted == ["a.py"]


def test_budget_exceeded_marks_partial_not_silent():
    # Two mutable files; the clock reports the budget as already blown before
    # the very first mutant, so nothing is tested but the gate MUST say so.
    ticks = iter([0.0, 100.0])
    result = mg.run_mutation_gate(
        ["a.py"], ["tests/test_a.py"],
        read_text=_fake_reader({"a.py": "def f(a, b):\n    return a == b\n"}),
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_always_caught,
        clock=lambda: next(ticks),
        budget_s=1.0,
    )
    assert result.status == "partial"
    assert result.reason == "budget_exceeded"
    assert result.tested == 0
    assert "бюджет" in result.text
    assert not result.blocks_merge


def test_budget_exceeded_after_some_mutants_still_reports_what_ran():
    src = "def f(a, b, c):\n    return a == b and b == c\n"  # 3 mutants
    ticks = iter([0.0, 0.0, 1.0, 50.0, 50.0])
    result = mg.run_mutation_gate(
        ["a.py"], ["tests/test_a.py"],
        read_text=_fake_reader({"a.py": src}),
        write_mutant_at=lambda *a: None,
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_always_caught,
        clock=lambda: next(ticks),
        budget_s=10.0,
    )
    assert result.status == "partial"
    assert result.tested < result.total
    assert result.tested >= 1


def test_each_mutant_write_gets_a_distinct_mtime_stamp():
    src = "def f(a, b, c):\n    return a == b and b == c\n"  # 3 mutants
    stamps = []
    mg.run_mutation_gate(
        ["a.py"], ["tests/test_a.py"],
        read_text=_fake_reader({"a.py": src}),
        write_mutant_at=lambda rel, source, stamp: stamps.append(stamp),
        revert=lambda *a: None,
        collect_ok=_collect_ok,
        run_guard=_always_caught,
    )
    assert len(set(stamps)) == len(stamps) == 3
    assert all(s >= mg.MTIME_BASE for s in stamps)


# ── write_mutant: thin wrapper, exercised directly ──────────────────────────

def test_write_mutant_applies_text_then_the_given_stamp():
    calls = []
    mg.write_mutant(
        write_text=lambda path, text: calls.append(("write", path, text)),
        set_mtime=lambda path, stamp: calls.append(("stamp", path, stamp)),
        path="a.py", source="X = 1\n", stamp=123.0,
    )
    assert calls == [("write", "a.py", "X = 1\n"), ("stamp", "a.py", 123.0)]
