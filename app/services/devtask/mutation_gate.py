# -*- coding: utf-8 -*-
"""DEV-97: generic mutation gate for the dev_task merge report.

DEV-26 caught three sentries that existed, asserted the right thing, stayed
green, and proved nothing — because nobody ever broke the fix back and
watched them stay green on broken code. The hand-curated ``scripts/mutate_*.py``
harnesses fix that PER ARC, with a human-authored ``(name, file, old, new,
guard_test)`` list. A dev_task's diff touches files nobody wrote a list for.

This module is the generic counterpart: given the ``.py`` source files a
dev_task actually changed and the test files the merge gate already mapped to
them (:mod:`app.services.devtask.target_tests`), it AST-mutates each source
file at every recognised decision point (comparison operators, boolean
literals, ``and``/``or``), reruns the guard tests against each single mutant,
and reports which ones came back green when they should have turned red.

Two things are inherited on purpose, not reinvented (both paid for in blood
2026-08-11/12 — see ``tests/test_mutation_harness.py``):
- every mutant write gets its own byte-cache-busting mtime (:func:`write_mutant`
  callers must use this, never a bare ``Path.write_text``);
- a dirty tree is never mutated (revert here is ``git checkout --``, which
  would eat uncommitted work).

Orchestration (:func:`run_mutation_gate`) is pure control flow over injected
callables — no subprocess, no filesystem I/O, no git — so every branch (empty
diff, a survivor, a budget cut-off, a guard test that doesn't even collect) is
unit-tested without spawning a single real pytest.
"""
from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

#: Wall-clock ceiling for one gate run (mutants tested serially; each spawns a
#: real pytest). A constant, not a per-call knob — DEV-97 requires the budget
#: to be a fixed number the report can point at, not something that quietly
#: drifts per invocation.
DEFAULT_BUDGET_S = 300

_CMP_FLIP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
_BOOLOP_FLIP = {ast.And: ast.Or, ast.Or: ast.And}

#: A mutant write must never collide mtimes with the previous one in the same
#: wall-clock second (DEV-26, 2026-08-11: two same-size mutants in one second
#: are indistinguishable to Python's .pyc cache — the second silently runs the
#: first's bytecode). Base is deliberately in the future so it never collides
#: with a real filesystem mtime.
MTIME_BASE = 2_000_000_000


@dataclass(frozen=True)
class Mutant:
    description: str
    lineno: int
    source: str


@dataclass(frozen=True)
class Survivor:
    file: str
    line: int
    mutation: str
    guard_tests: List[str]


@dataclass(frozen=True)
class MutationGateResult:
    status: str  # "skipped" | "green" | "red" | "partial"
    reason: Optional[str]
    survivors: List[Survivor] = field(default_factory=list)
    tested: int = 0
    total: int = 0
    text: str = ""

    @property
    def blocks_merge(self) -> bool:
        """Only an actual survivor (or an unverifiable guard) reddens the
        report — a budget cut-off or nothing-to-mutate is informational."""
        return self.status == "red"


def select_source_files(changed_paths: Sequence[str], exists: Callable[[str], bool]) -> List[str]:
    """The subset of a diff's changed paths this gate can mutate: an existing
    (not deleted) ``.py`` file that is not itself a test.

    Test files are excluded on both counts a real diff can hit — a path under
    a ``tests/`` directory at any depth, and a bare ``test_*.py`` filename
    outside one (mirrors ``target_tests``'s own recursive test discovery, so
    the two modules agree on what "is a test" means).
    """
    out = []
    for raw in changed_paths:
        rel = (raw or "").replace("\\", "/").strip()
        if not rel.endswith(".py"):
            continue
        parts = rel.split("/")
        if "tests" in parts[:-1]:
            continue
        if parts[-1].startswith("test_"):
            continue
        if not exists(rel):
            continue
        out.append(rel)
    return sorted(out)


def _candidates(tree: ast.AST):
    """Every recognised decision point, in a fixed deterministic order.

    Walking the SAME tree structure (either the original, when counting, or a
    fresh re-parse of the identical source, when mutating) yields identical
    order — that is what lets :func:`generate_mutants` address the Nth
    candidate by a plain integer index instead of carrying node identity
    across a copy.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_FLIP:
            yield node, "cmp"
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield node, "bool_const"
        elif isinstance(node, ast.BoolOp) and type(node.op) in _BOOLOP_FLIP:
            yield node, "boolop"


def _mutate(node: ast.AST, kind: str) -> str:
    if kind == "cmp":
        old = type(node.ops[0])
        new = _CMP_FLIP[old]
        node.ops[0] = new()
        return "%s -> %s" % (old.__name__, new.__name__)
    if kind == "bool_const":
        before = node.value
        node.value = not node.value
        return "bool literal %r -> %r" % (before, node.value)
    if kind == "boolop":
        old = type(node.op)
        new = _BOOLOP_FLIP[old]
        node.op = new()
        return "%s -> %s" % (old.__name__, new.__name__)
    raise ValueError(kind)  # pragma: no cover - _candidates never yields another kind


def generate_mutants(source: str) -> List[Mutant]:
    """Every single-operator mutant of ``source`` — one flipped decision each,
    never combined, so a surviving mutant points at exactly one line and one
    change. Source that doesn't parse yields no mutants: a syntax-broken diff
    is the targeted-test gate's problem, not this one's — it never reaches
    here because that gate already failed and blocked before we run.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    total = sum(1 for _ in _candidates(tree))
    mutants: List[Mutant] = []
    for target_idx in range(total):
        mutant_tree = ast.parse(source)
        for i, (node, kind) in enumerate(_candidates(mutant_tree)):
            if i != target_idx:
                continue
            description = _mutate(node, kind)
            ast.fix_missing_locations(mutant_tree)
            mutants.append(Mutant(description=description, lineno=node.lineno,
                                   source=ast.unparse(mutant_tree)))
            break
    return mutants


def write_mutant(write_text: Callable[[str, str], None], set_mtime: Callable[[str, float], None],
                  path: str, source: str, stamp: float) -> None:
    """Write a mutant with an explicit, caller-chosen mtime stamp.

    The uniqueness of ``stamp`` across the whole run is :func:`run_mutation_gate`'s
    job (it hands out ``MTIME_BASE + n`` for the n-th mutant written) — this
    function only applies what it is given, so it stays trivially fake-able in
    tests without reimplementing the counter there too.
    """
    write_text(path, source)
    set_mtime(path, stamp)


def _format_survivors(survivors: Sequence[Survivor]) -> str:
    lines = []
    for s in survivors:
        guards = ", ".join(s.guard_tests[:3])
        more = len(s.guard_tests) - 3
        if more > 0:
            guards += ", ...ещё %d" % more
        lines.append("• %s:%d — %s (не поймано: %s)" % (s.file, s.line, s.mutation, guards))
    return "\n".join(lines)


def run_mutation_gate(
    source_files: Sequence[str],
    guard_tests: Sequence[str],
    *,
    read_text: Callable[[str], str],
    write_mutant_at: Callable[[str, str, float], None],
    revert: Callable[[str], None],
    collect_ok: Callable[[Sequence[str]], bool],
    run_guard: Callable[[Sequence[str]], bool],
    clock: Callable[[], float] = time.monotonic,
    budget_s: float = DEFAULT_BUDGET_S,
) -> MutationGateResult:
    """Run the whole gate. Never silent: every branch returns a populated
    ``.text`` and a status that is exactly one of skipped/green/red/partial.

    Injected seams (mirrors the rest of the devtask package's testing style —
    see ``target_tests.changed_paths``'s ``run=`` parameter):
    - ``read_text(path) -> str``: current source of one changed file.
    - ``write_mutant_at(path, source, stamp)``: persist a mutant with an
      explicit mtime (see :data:`MTIME_BASE`) — real callers pass something
      that calls :func:`write_mutant` under the hood.
    - ``revert(rel_path)``: undo the mutant (``git checkout --`` in prod).
      Called in a ``finally`` around every single mutant, survivor or not.
    - ``collect_ok(guard_tests) -> bool``: True iff every guard test id
      actually collects. Checked ONCE, before any mutation — a dangling test
      id is a gate failure, never a "mutant survived" (it never even ran).
    - ``run_guard(guard_tests) -> bool``: True iff the guard tests caught the
      currently-written mutant (i.e. at least one failed). Real callers wire
      this to a pytest run using the SAME rc==1-and-"failed"-in-output
      criterion as ``scripts/mutate_worktree_discipline.py`` — a collection
      error must never register as "caught".
    - ``clock()``: monotonic seconds, for the budget cut-off.
    """
    if not source_files or not guard_tests:
        return MutationGateResult(
            status="skipped", reason="empty_diff",
            text="🧬 мутации: дифф пуст — гейт не запускается")

    if not collect_ok(guard_tests):
        return MutationGateResult(
            status="red", reason="dangling_guard_test",
            text="🧬 мутации: 🔴 тест-сторож из diff-маппинга не собирается "
                 "(%s) — гейт красный, ни одна мутация не прогонялась"
                 % ", ".join(guard_tests[:5]))

    plan = []
    for rel in source_files:
        source = read_text(rel)
        for mutant in generate_mutants(source):
            plan.append((rel, mutant))

    if not plan:
        return MutationGateResult(
            status="skipped", reason="no_mutable_sites", total=0,
            text="🧬 мутации: в изменённых файлах нет узнаваемых мутируемых точек")

    start = clock()
    survivors: List[Survivor] = []
    tested = 0
    budget_hit = False
    for stamp_offset, (rel, mutant) in enumerate(plan):
        if clock() - start > budget_s:
            budget_hit = True
            break
        write_mutant_at(rel, mutant.source, float(MTIME_BASE + stamp_offset))
        try:
            caught = run_guard(guard_tests)
        finally:
            revert(rel)
        tested += 1
        if not caught:
            survivors.append(Survivor(file=rel, line=mutant.lineno,
                                       mutation=mutant.description,
                                       guard_tests=list(guard_tests)))

    if survivors:
        return MutationGateResult(
            status="red", reason="survivors", survivors=survivors, tested=tested, total=len(plan),
            text="🧬 мутации: 🔴 выжило %d из %d\n%s" % (len(survivors), tested, _format_survivors(survivors)))

    if budget_hit:
        return MutationGateResult(
            status="partial", reason="budget_exceeded", tested=tested, total=len(plan),
            text="🧬 мутации: ⚠️ НЕ завершены — бюджет %ds исчерпан (%d/%d прогнано, все пойманы)"
                 % (budget_s, tested, len(plan)))

    return MutationGateResult(
        status="green", reason=None, tested=tested, total=len(plan),
        text="🧬 мутации: ✅ все %d пойманы" % tested)
