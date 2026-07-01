# Vizir autonomy LOOP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `LoopController` — a thin, приёмко-гейтед retry cycle over the existing Vizir `Coordinator`, so an autonomous run keeps trying (with acceptance feedback) until it succeeds, exhausts a hard cap, or hits a money/behavior tooth — replacing today's manual "повторить?" with automatic teeth.

**Architecture:** A NEW outer loop (`app/services/vizir/loop.py`) wraps `Coordinator.run` (unchanged). Inner Hermes `max_iterations` = one attempt; outer `max_attempts` = acceptance-gated retry. The controller is a *controlled executor, not a parallel brain*: it re-runs the SAME single-step plan with appended feedback, makes only deterministic budget/stall decisions, and never re-plans. Money teeth span the whole cycle via one shared `Task.budget_usd` + reserve-before-attempt; six deterministic stops bound behavior and spend.

**Tech Stack:** Python 3.11, asyncio, pytest (plain-assert style, `asyncio.run` wrapper — mirrors existing `tests/test_vizir_*.py`). $0 throughout: all tests use the REAL `Coordinator` + mock handlers; NO real Hermes, NO real money. Live break-in is a separate, human-gated step (§ spec `docs/specs/vizir-loop.md`), NOT in this plan.

**Spec:** `docs/specs/vizir-loop.md` (approved). knee#1/#2 and the live bot are NOT touched — additive only, in worktree `vizir-loop`.

---

## File structure

- **Create** `app/services/vizir/loop.py` — `LoopConfig`, `LoopReport`, `LoopController`, `make_loop`. Single responsibility: the outer acceptance-gated retry cycle. Depends on `models`, `handlers`, `coordinator` (via injected instance), `hermes_acceptance` (only in `make_loop`).
- **Create** `tests/test_vizir_loop.py` — all loop tests (exits, teeth-spies, integration).
- **Do NOT modify** `coordinator.py`, `handlers.py`, `handlers_hermes.py`, `hermes_acceptance.py`, `models.py`.

**Seam methods (spy-able, mirror `Coordinator._money_gate_allows`/`_chunk_allowed`):** `_reserve_allows`, `_is_hard_stop`, `_is_stalled`, `_deadline_exceeded`, `_compose_prompt`. Sabotaging any of these in a test proves that tooth has teeth (Task 9).

**Stop reasons (the six teeth):** `completed` (success), `stopped_max_attempts`, `stopped_budget`, `stopped_cost_cap`, `stopped_stalled`, `stopped_timeout`. Every non-`completed` stop sets `needs_escalation=True`.

---

### Task 1: Data models (`LoopConfig`, `LoopReport`)

**Files:**
- Create: `app/services/vizir/loop.py`
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
# -*- coding: utf-8 -*-
"""Vizir autonomy LOOP — приёмко-гейтед retry over the Coordinator. $0 (mocks)."""
import asyncio

from app.services.vizir.loop import LoopConfig, LoopReport


def _run(coro):
    return asyncio.run(coro)


def test_loopconfig_defaults():
    cfg = LoopConfig()
    assert cfg.max_attempts == 3
    assert cfg.min_attempt_usd == 0.0
    assert cfg.loop_deadline_s == 0.0


def test_loopreport_fields():
    r = LoopReport(
        task_id="t", goal="g", actor="admin", accepted=True,
        stopped_reason="completed", attempts=1, loop_spent_usd=0.0,
        last_result={"x": 1}, reasons=[], reasons_history=[], needs_escalation=False,
    )
    assert r.accepted is True and r.stopped_reason == "completed"
    assert r.needs_escalation is False and r.attempts == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.vizir.loop'`

- [ ] **Step 3: Write minimal implementation**

```python
# -*- coding: utf-8 -*-
"""Vizir autonomy LOOP — a thin, acceptance-gated retry cycle OVER the Coordinator.

The Coordinator runs ONE plan under the money-gate. The LoopController runs the
Coordinator repeatedly on the SAME single-step plan (Hermes), re-checking Vizir
acceptance each attempt and injecting the failing reasons into the next attempt's
prompt, until acceptance passes or a deterministic tooth stops it. It is a
CONTROLLED EXECUTOR, never a parallel brain: it re-plans nothing and makes only
deterministic budget/stall decisions. Money teeth span the WHOLE cycle via one
shared Task.budget_usd + reserve-before-attempt. See docs/specs/vizir-loop.md.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LoopConfig:
    max_attempts: int = 3          # hard ceiling on outer attempts
    min_attempt_usd: float = 0.0   # reserve-before-attempt floor (0 => only require remaining>0)
    loop_deadline_s: float = 0.0   # wall-clock cap over the whole loop (0 => off)


@dataclass
class LoopReport:
    task_id: str
    goal: str
    actor: str
    accepted: bool
    stopped_reason: str            # completed | stopped_max_attempts | stopped_budget
                                   # | stopped_cost_cap | stopped_stalled | stopped_timeout
    attempts: int
    loop_spent_usd: float
    last_result: Any
    reasons: list = field(default_factory=list)          # last acceptance reasons
    reasons_history: list = field(default_factory=list)  # reasons per rejected attempt
    needs_escalation: bool = False # True on any non-completed stop -> show Daniil
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): LoopConfig + LoopReport data models (TDD)"
```

---

### Task 2: `LoopController` skeleton — `completed` + `max_attempts` exits

Writes the full `run()` loop calling five seam methods, with the seams as PERMISSIVE STUBS. Later tasks make each seam real, each red-first (the stub is intentionally too permissive → the new exit test fails until the seam is implemented).

**Files:**
- Modify: `app/services/vizir/loop.py`
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator
from app.services.vizir.loop import LoopController, LoopConfig
from app.services.vizir.hermes_acceptance import AcceptanceResult


def _coord_with(handler, kind="gen", **coord_kw):
    reg = HandlerRegistry()
    reg.register(kind, handler)
    return Coordinator(reg, **coord_kw)


def _gen_handler(result_dict, cost=0.0, kind="gen"):
    """A mock generation handler returning a fixed result dict, charging `cost`."""
    async def handler(step, ctx):
        return HandlerResult(ok=True, result=result_dict, cost_usd=cost)
    return handler


def _build_plan_gen(prompt):
    # single-step plan, kind "gen"; estimated_usd=0 so pre-check treats it free
    return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])


def _loop(coord, accept_fn, cfg=None, on_event=None, now_fn=None, build_plan=_build_plan_gen):
    return LoopController(coord, build_plan=build_plan, accept_fn=accept_fn,
                          config=cfg or LoopConfig(), on_event=on_event, now_fn=now_fn)


def test_completed_on_first_attempt_stops_and_not_escalated():
    coord = _coord_with(_gen_handler({"final_response": "<html></html>",
                                      "stopped_reason": "completed"}))
    accept_fn = lambda d: AcceptanceResult(accepted=True, reasons=[])
    loop = _loop(coord, accept_fn)
    task = Task("t", "make chat", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base goal"))

    assert rep.accepted is True
    assert rep.stopped_reason == "completed"
    assert rep.attempts == 1
    assert rep.needs_escalation is False


def test_never_accepted_stops_at_max_attempts_and_escalates():
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # different reasons each attempt (no stall), never accepted
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["reason-%d" % d.get("n", 0)])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 3
    assert rep.stopped_reason == "stopped_max_attempts"
    assert rep.accepted is False
    assert rep.needs_escalation is True
    assert len(rep.reasons_history) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_vizir_loop.py -k "completed_on_first or max_attempts" -v`
Expected: FAIL — `AttributeError: ... 'LoopController'` / cannot import `LoopController`

- [ ] **Step 3: Write the controller (full run() + seam STUBS)**

Append to `app/services/vizir/loop.py`:

```python
class LoopController:
    """Outer acceptance-gated retry loop over a Coordinator. Thin: it sequences
    attempts, injects acceptance feedback, and applies deterministic teeth. All
    money is metered by the injected Coordinator; the loop adds only the
    reserve-before-attempt tooth on the shared budget."""

    def __init__(
        self,
        coordinator,
        build_plan: Callable[[str], Any],   # (prompt) -> Plan (single Hermes step)
        accept_fn: Callable[[dict], Any],    # (result_dict) -> AcceptanceResult
        config: LoopConfig | None = None,
        on_event: Callable[[dict], None] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._build_plan = build_plan
        self._accept_fn = accept_fn
        self._config = config or LoopConfig()
        self._on_event = on_event or (lambda e: None)
        self._now = now_fn or time.monotonic

    def _emit(self, type_: str, **fields) -> None:
        self._on_event({"type": type_, **fields})

    # ---- spy-able seams (permissive stubs; made real in later tasks) ----
    def _reserve_allows(self, remaining: float, cfg: LoopConfig) -> bool:
        return remaining > 0

    def _is_hard_stop(self, status: str) -> bool:
        return False

    def _is_stalled(self, reasons: list, prev_reasons) -> bool:
        return False

    def _deadline_exceeded(self, start: float, cfg: LoopConfig) -> bool:
        return False

    def _compose_prompt(self, base_prompt: str, reasons: list) -> str:
        return base_prompt

    # ---- helpers ----
    def _stopped(self, task, reason, attempt, loop_spent, last_result,
                 reasons, reasons_history) -> LoopReport:
        self._emit("loop_stopped", reason=reason, spent=loop_spent, attempts=attempt)
        return LoopReport(
            task_id=task.task_id, goal=task.goal, actor=task.actor,
            accepted=False, stopped_reason=reason, attempts=attempt,
            loop_spent_usd=loop_spent, last_result=last_result,
            reasons=list(reasons), reasons_history=reasons_history,
            needs_escalation=True,
        )

    async def run(self, task, base_prompt: str) -> LoopReport:
        from .models import Task  # local import: avoid a hard module-load cycle
        cfg = self._config
        loop_spent = 0.0
        reasons_history: list = []
        prev_reasons = None
        last_result = None
        reasons: list = []
        start = self._now()
        attempt = 0

        while True:
            # STOP 1: hard ceiling on attempts
            if attempt >= cfg.max_attempts:
                return self._stopped(task, "stopped_max_attempts", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            # STOP 6: wall-clock loop deadline (backstop)
            if self._deadline_exceeded(start, cfg):
                return self._stopped(task, "stopped_timeout", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            # STOP 3: reserve-before-attempt on the SHARED budget (never start
            # an attempt you cannot afford within the whole-loop budget)
            remaining = task.budget_usd - loop_spent
            if not self._reserve_allows(remaining, cfg):
                return self._stopped(task, "stopped_budget", attempt,
                                     loop_spent, last_result, reasons, reasons_history)

            attempt += 1
            prompt = self._compose_prompt(base_prompt, reasons)
            self._emit("loop_attempt_started", attempt=attempt)
            plan = self._build_plan(prompt)
            # per-attempt Task carries the REMAINING budget down => the
            # Coordinator's own pre-check is a double-insurance backstop.
            task_k = Task(
                task_id="%s-a%d" % (task.task_id, attempt), goal=task.goal,
                budget_usd=remaining, actor=task.actor,
                constraints=dict(task.constraints),
            )
            report = await self._coordinator.run(task_k, plan)
            loop_spent += report.total_cost_usd

            # STOP 2/4: the Coordinator did not complete the plan (money/approval
            # event: stopped_budget / stopped_cost_cap / stopped_for_approval)
            # => conservative stop of the whole loop, no retry.
            if self._is_hard_stop(report.status):
                return self._stopped(task, report.status, attempt,
                                     loop_spent, last_result, reasons, reasons_history)

            last_step = report.steps[-1] if report.steps else None
            result_dict = (last_step.result
                           if (last_step is not None and isinstance(last_step.result, dict))
                           else {})
            last_result = result_dict

            acc = self._accept_fn(result_dict)
            if acc.accepted:
                self._emit("loop_stopped", reason="completed",
                           spent=loop_spent, attempts=attempt)
                return LoopReport(
                    task_id=task.task_id, goal=task.goal, actor=task.actor,
                    accepted=True, stopped_reason="completed", attempts=attempt,
                    loop_spent_usd=loop_spent, last_result=last_result,
                    reasons=[], reasons_history=reasons_history,
                    needs_escalation=False,
                )

            reasons = list(acc.reasons)
            reasons_history.append(list(reasons))
            self._emit("loop_attempt_rejected", attempt=attempt, reasons=reasons)

            # STOP 5: not converging (identical failure signature two attempts running)
            if self._is_stalled(reasons, prev_reasons):
                return self._stopped(task, "stopped_stalled", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            prev_reasons = reasons
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_vizir_loop.py -k "completed_on_first or max_attempts" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): LoopController run() + completed/max_attempts exits (TDD)"
```

---

### Task 3: Feedback injection (`_compose_prompt`) + reasons history

**Files:**
- Modify: `app/services/vizir/loop.py` (`_compose_prompt`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_feedback_reasons_injected_into_next_prompt_base_preserved():
    seen_prompts = []
    def build_plan(prompt):
        seen_prompts.append(prompt)
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])

    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # attempt 1 rejected with a specific reason; attempt 2 accepted
    def accept_fn(d):
        if d.get("n", 0) >= 2:
            return AcceptanceResult(accepted=True, reasons=[])
        return AcceptanceResult(accepted=False, reasons=["no neon cyan/blue accent"])
    loop = _loop(coord, accept_fn, build_plan=build_plan)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "BASE_GOAL"))

    assert rep.accepted is True and rep.attempts == 2
    # attempt 1 prompt = base only; attempt 2 prompt = base + injected reason
    assert seen_prompts[0] == "BASE_GOAL"
    assert "BASE_GOAL" in seen_prompts[1]                     # base preserved (immutable)
    assert "no neon cyan/blue accent" in seen_prompts[1]      # reason injected
    assert rep.reasons_history == [["no neon cyan/blue accent"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k feedback_reasons_injected -v`
Expected: FAIL — `assert "no neon cyan/blue accent" in seen_prompts[1]` (stub returns base only)

- [ ] **Step 3: Make `_compose_prompt` real**

Replace the stub in `app/services/vizir/loop.py`:

```python
    def _compose_prompt(self, base_prompt: str, reasons: list) -> str:
        # IMMUTABLE base + APPENDED feedback (never overwrite the goal): bounds
        # goal-drift and gives directed convergence across attempts.
        if not reasons:
            return base_prompt
        joined = "; ".join(reasons)
        return (base_prompt
                + "\n\n[FEEDBACK] Предыдущая попытка провалила проверки: "
                + joined + ". Исправь их, остальное сохрани.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k feedback_reasons_injected -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): acceptance-reasons feedback injection, immutable base prompt (TDD)"
```

---

### Task 4: Reserve-before-attempt (`_reserve_allows`) — `stopped_budget`

The loop-level money tooth: never START an attempt you cannot afford within the shared budget. Red-first proof: assert the handler is NOT invoked for the unaffordable attempt.

**Files:**
- Modify: `app/services/vizir/loop.py` (`_reserve_allows`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reserve_before_attempt_stops_budget_without_running_handler():
    calls = {"n": 0}
    async def handler(step, ctx):
        calls["n"] += 1
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.01)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["still bad"])
    cfg = LoopConfig(max_attempts=5, min_attempt_usd=0.015)  # need >= $0.015 to start
    loop = _loop(coord, accept_fn, cfg=cfg)
    task = Task("t", "g", budget_usd=0.02, actor="admin")
    rep = _run(loop.run(task, "base"))

    # attempt1: remaining 0.02 >= 0.015 -> runs, spends 0.01. attempt2: remaining
    # 0.01 < 0.015 -> reserve REFUSES to start (handler not called again).
    assert calls["n"] == 1
    assert rep.stopped_reason == "stopped_budget"
    assert rep.attempts == 1
    assert rep.loop_spent_usd <= task.budget_usd
    assert rep.needs_escalation is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k reserve_before_attempt -v`
Expected: FAIL — stub `remaining > 0` allows attempt 2 (`calls["n"] == 2`), so `assert calls["n"] == 1` fails.

- [ ] **Step 3: Make `_reserve_allows` real**

Replace the stub:

```python
    def _reserve_allows(self, remaining: float, cfg: LoopConfig) -> bool:
        # never start an attempt you cannot pay for within the shared budget
        if remaining <= 0:
            return False
        if cfg.min_attempt_usd and remaining < cfg.min_attempt_usd:
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k reserve_before_attempt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): reserve-before-attempt on shared budget -> stopped_budget (TDD)"
```

---

### Task 5: Hard-stop on non-completed coordinator run (`_is_hard_stop`) — `stopped_cost_cap`

A cost-cap breach inside an attempt makes the Coordinator return `status="stopped_cost_cap"`. The loop must STOP the whole cycle (money event, no retry), not fall through to acceptance/retry.

**Files:**
- Modify: `app/services/vizir/loop.py` (`_is_hard_stop`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cost_cap_breach_in_attempt_stops_loop_immediately_no_retry():
    # handler overspends via report_cost beyond the step cap -> Coordinator
    # returns stopped_cost_cap (proven mechanism, see test_vizir_cost_cap.py).
    async def overspender(step, ctx):
        for c in [0.01, 0.01, 0.01, 0.01]:   # 4 x 0.01 vs max_usd 0.03
            ctx["report_cost"](c)             # 4th raises StepBudgetExceeded
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.04)
    coord = _coord_with(overspender)

    def build_plan(prompt):
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt},
                                estimated_usd=0.03, max_usd=0.03)])

    accept_fn = lambda d: AcceptanceResult(accepted=True, reasons=[])  # would accept if reached
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3), build_plan=build_plan)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 1                       # did NOT retry a breached attempt
    assert rep.stopped_reason == "stopped_cost_cap"
    assert rep.accepted is False
    assert rep.needs_escalation is True
    assert abs(rep.loop_spent_usd - 0.03) < 1e-9   # partial charge (step_spent), not full
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k cost_cap_breach_in_attempt -v`
Expected: FAIL — stub `_is_hard_stop` returns False, so the loop proceeds to acceptance (result of a BLOCKED step is `None` → `{}` → `accept_fn({})` accepts) and returns `stopped_reason="completed"`; assertion `== "stopped_cost_cap"` fails.

- [ ] **Step 3: Make `_is_hard_stop` real**

Replace the stub:

```python
    def _is_hard_stop(self, status: str) -> bool:
        # Any non-"completed" Coordinator outcome is a money/approval event
        # (stopped_budget / stopped_cost_cap / stopped_for_approval) => stop the
        # whole loop conservatively; do NOT retry a run that burned toward a cap.
        return status != "completed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k cost_cap_breach_in_attempt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): cost-cap breach stops loop (no retry), partial charge (TDD)"
```

---

### Task 6: No-progress detector (`_is_stalled`) — `stopped_stalled`

**Files:**
- Modify: `app/services/vizir/loop.py` (`_is_stalled`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_identical_reasons_two_attempts_stops_stalled_before_max():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # SAME reasons every attempt -> not converging
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["no dark theme background"])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=5))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 2                     # stalled at 2, NOT run to max_attempts=5
    assert rep.stopped_reason == "stopped_stalled"
    assert rep.needs_escalation is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k identical_reasons_two_attempts -v`
Expected: FAIL — stub `_is_stalled` returns False, so the loop runs to `max_attempts=5`; `assert rep.attempts == 2` fails (gets 5, `stopped_max_attempts`).

- [ ] **Step 3: Make `_is_stalled` real**

Replace the stub:

```python
    def _is_stalled(self, reasons: list, prev_reasons) -> bool:
        # identical failure signature two attempts running => not converging.
        return prev_reasons is not None and reasons == prev_reasons
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k identical_reasons_two_attempts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): no-progress detector (identical reasons) -> stopped_stalled (TDD)"
```

---

### Task 7: Loop wall-clock deadline (`_deadline_exceeded`) — `stopped_timeout`

Backstop over the whole loop, using an injectable clock for deterministic testing.

**Files:**
- Modify: `app/services/vizir/loop.py` (`_deadline_exceeded`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_loop_deadline_stops_timeout_between_attempts():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["reason-%s" % id(d)])

    # fake monotonic clock: 0 at start, then jumps past the 10s deadline
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    now_fn = lambda: next(ticks)
    cfg = LoopConfig(max_attempts=10, loop_deadline_s=10.0)
    loop = _loop(coord, accept_fn, cfg=cfg, now_fn=now_fn)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.stopped_reason == "stopped_timeout"
    assert rep.attempts == 1                     # 1 attempt ran, deadline hit before attempt 2
    assert rep.needs_escalation is True
```

> Clock trace: `start=now()`→0.0. Top of loop attempt1: `_deadline_exceeded` reads now()→0.0, 0-0<10 → ok, runs attempt1. Top of loop attempt2: now()→100.0, 100-0≥10 → stop_timeout.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k loop_deadline_stops_timeout -v`
Expected: FAIL — stub returns False; loop runs a 2nd attempt (and possibly exhausts the `ticks` iterator raising `StopIteration`, or stops for another reason). Assertion `== "stopped_timeout"` fails.

- [ ] **Step 3: Make `_deadline_exceeded` real**

Replace the stub:

```python
    def _deadline_exceeded(self, start: float, cfg: LoopConfig) -> bool:
        return bool(cfg.loop_deadline_s) and (self._now() - start) >= cfg.loop_deadline_s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k loop_deadline_stops_timeout -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): whole-loop wall-clock deadline -> stopped_timeout (TDD)"
```

---

### Task 8: Events (`loop_attempt_started` / `loop_attempt_rejected` / `loop_stopped`)

The loop is transport-agnostic: it emits structured events a driver (CC now, Jarvis later) renders. The `run()` code already emits them; this task LOCKS the contract with a test.

**Files:**
- Test: `tests/test_vizir_loop.py` (no `loop.py` change expected; if the test fails, fix emit calls to match)

- [ ] **Step 1: Write the failing test**

```python
def test_loop_emits_structured_events():
    events = []
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    def accept_fn(d):
        return AcceptanceResult(accepted=(d.get("n", 0) >= 2),
                                reasons=[] if d.get("n", 0) >= 2 else ["bad-%d" % d.get("n", 0)])
    loop = _loop(coord, accept_fn, on_event=events.append)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    types = [e["type"] for e in events]
    assert rep.accepted is True and rep.attempts == 2
    assert types.count("loop_attempt_started") == 2
    assert types.count("loop_attempt_rejected") == 1
    assert types[-1] == "loop_stopped"
    stopped = events[-1]
    assert stopped["reason"] == "completed" and stopped["attempts"] == 2
    rejected = [e for e in events if e["type"] == "loop_attempt_rejected"][0]
    assert rejected["reasons"] == ["bad-1"]
```

- [ ] **Step 2: Run test to verify it fails/passes**

Run: `python -m pytest tests/test_vizir_loop.py -k loop_emits_structured_events -v`
Expected: PASS if the emit calls from Task 2 match the asserted shapes. If FAIL, adjust the `self._emit(...)` calls in `run()` to emit exactly these types/fields, then re-run to PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_vizir_loop.py app/services/vizir/loop.py
git commit -m "test(vizir-loop): lock structured event contract (started/rejected/stopped)"
```

---

### Task 9: Spy-teeth — prove EACH safeguard bites (mutation tests)

Mirrors `test_vizir_spy_gate.py` / the cost-cap spy: sabotage a seam, show the protective assertion of the real test FAILS — i.e. it genuinely distinguishes a working tooth from a broken one.

**Files:**
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing tests (then they pass once sabotage is wired)**

```python
def test_spy_broken_reserve_lets_loop_overspend_proving_budget_teeth():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.01)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["x-%s" % id(object())])
    cfg = LoopConfig(max_attempts=10, min_attempt_usd=0.015)
    loop = _loop(coord, accept_fn, cfg=cfg)
    # SABOTAGE reserve: always allow starting an attempt
    loop._reserve_allows = lambda remaining, c: True
    task = Task("t", "g", budget_usd=0.02, actor="admin")
    rep = _run(loop.run(task, "base"))
    # broken reserve -> the per-attempt Task budget backstop (Coordinator) still
    # bounds it, but the LOOP no longer stops with stopped_budget at attempt 1.
    # The real budget test asserts (attempts==1, stopped_budget); a broken reserve
    # violates it -> that assertion has teeth.
    real_budget_assertion_holds = (rep.attempts == 1 and rep.stopped_reason == "stopped_budget")
    assert not real_budget_assertion_holds


def test_spy_broken_stall_runs_to_max_proving_stall_teeth():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["same reason"])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=4))
    # SABOTAGE stall detector: never stalled
    loop._is_stalled = lambda reasons, prev: False
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))
    # identical reasons no longer stop at 2 -> runs to max_attempts=4
    assert rep.attempts == 4 and rep.stopped_reason == "stopped_max_attempts"
    real_stall_assertion_holds = (rep.attempts == 2 and rep.stopped_reason == "stopped_stalled")
    assert not real_stall_assertion_holds


def test_spy_broken_hardstop_retries_breach_proving_costcap_teeth():
    async def overspender(step, ctx):
        for c in [0.01, 0.01, 0.01, 0.01]:
            ctx["report_cost"](c)
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.04)
    coord = _coord_with(overspender)
    def build_plan(prompt):
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt},
                                estimated_usd=0.03, max_usd=0.03)])
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["r-%s" % id(object())])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3), build_plan=build_plan)
    # SABOTAGE hard-stop: pretend every run "completed"
    loop._is_hard_stop = lambda status: False
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))
    # broken hard-stop -> a breached attempt is treated as completed and RETRIED
    assert rep.attempts > 1
    real_costcap_assertion_holds = (rep.attempts == 1 and rep.stopped_reason == "stopped_cost_cap")
    assert not real_costcap_assertion_holds


def test_spy_broken_compose_drops_base_proving_goaldrift_teeth():
    seen = []
    def build_plan(prompt):
        seen.append(prompt)
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=(d.get("n", 0) >= 2),
                                           reasons=[] if d.get("n", 0) >= 2 else ["fix me"])
    loop = _loop(coord, accept_fn, build_plan=build_plan)
    # SABOTAGE compose: overwrite the goal with only the feedback (drift)
    loop._compose_prompt = lambda base, reasons: ("; ".join(reasons) if reasons else base)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    _run(loop.run(task, "BASE_GOAL"))
    # broken compose -> attempt 2 prompt LOST the base goal
    assert "BASE_GOAL" not in seen[1]
    real_immutable_assertion_holds = ("BASE_GOAL" in seen[1])
    assert not real_immutable_assertion_holds
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_vizir_loop.py -k spy_broken -v`
Expected: PASS (4 passed) — each proves the corresponding real assertion has teeth.

- [ ] **Step 3: Commit**

```bash
git add tests/test_vizir_loop.py
git commit -m "test(vizir-loop): spy-teeth for reserve/stall/hard-stop/compose (mutation proofs)"
```

---

### Task 10: Integration — `make_loop` factory + real `accept_hermes_chat` (fake Hermes, $0)

Wires the loop to the REAL Vizir acceptance and a Hermes-shaped single-step plan, exercised by a FAKE hermes handler (no real Hermes, no money). Proves the loop converges via real acceptance and injects real reasons.

**Files:**
- Modify: `app/services/vizir/loop.py` (add `make_loop`)
- Test: `tests/test_vizir_loop.py`

- [ ] **Step 1: Write the failing test**

```python
_GOOD_HTML = (
    "<html><head><style>body{background:#0a0a0a;} .a{color:#00ffff;}</style></head>"
    "<body><div id=\"messages\" class=\"chat\"></div>"
    "<input id=\"msg\"><button onclick=\"send()\">Send</button>"
    "<span id=\"status\">online</span>"
    "<script>const API_URL=\"http://localhost:8010\";"
    "function send(){fetch(API_URL,{method:\"POST\"});}</script></body></html>"
)
# same but neon accent removed -> acceptance rejects with "no neon cyan/blue accent"
_BAD_HTML = _GOOD_HTML.replace("#00ffff", "#ffffff")


def test_make_loop_with_real_acceptance_retries_then_accepts():
    from app.services.vizir.loop import make_loop
    seen = []
    n = {"i": 0}
    async def hermes_fake(step, ctx):
        seen.append(step.params["prompt"])
        n["i"] += 1
        html = _BAD_HTML if n["i"] == 1 else _GOOD_HTML
        return HandlerResult(ok=True,
                             result={"final_response": html, "stopped_reason": "completed"},
                             cost_usd=0.0)
    reg = HandlerRegistry()
    reg.register("hermes", hermes_fake)
    coord = Coordinator(reg)
    loop = make_loop(coord, kind="hermes", estimated_usd=0.0, max_usd=0.0, max_attempts=3)
    task = Task("t", "make jarvis chat", budget_usd=0.50, actor="admin")
    rep = _run(loop.run(task, "Generate a Jarvis dark-neon web chat."))

    assert rep.accepted is True and rep.stopped_reason == "completed"
    assert rep.attempts == 2
    # attempt 2 prompt carried the real acceptance reason from attempt 1
    assert "neon" in seen[1]


def test_make_loop_truncated_run_is_rejected_up_front():
    from app.services.vizir.loop import make_loop
    async def hermes_fake(step, ctx):
        # good HTML but run did NOT complete (hit max_iterations) -> acceptance rejects
        return HandlerResult(ok=True,
                             result={"final_response": _GOOD_HTML, "stopped_reason": "max_iterations"},
                             cost_usd=0.0)
    reg = HandlerRegistry()
    reg.register("hermes", hermes_fake)
    coord = Coordinator(reg)
    loop = make_loop(coord, kind="hermes", estimated_usd=0.0, max_usd=0.0, max_attempts=2)
    task = Task("t", "g", budget_usd=0.50, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.accepted is False
    assert rep.stopped_reason in ("stopped_stalled", "stopped_max_attempts")
    assert any("did not complete" in r for r in rep.reasons)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vizir_loop.py -k make_loop -v`
Expected: FAIL — `ImportError: cannot import name 'make_loop'`

- [ ] **Step 3: Add `make_loop` to `loop.py`**

Append to `app/services/vizir/loop.py`:

```python
def make_loop(
    coordinator,
    *,
    kind: str = "hermes",
    estimated_usd: float = 0.0,
    max_usd: float = 0.0,
    timeout_s: float = 0.0,
    max_attempts: int = 3,
    min_attempt_usd: float = 0.0,
    loop_deadline_s: float = 0.0,
    accept_fn=None,
    on_event=None,
    now_fn=None,
) -> "LoopController":
    """Wire a LoopController for a Hermes-shaped single-step plan under Vizir's
    real deterministic acceptance. Each attempt is ONE `kind` step carrying the
    (feedback-augmented) prompt; money is metered by `coordinator`."""
    from .models import Plan, Step
    from .hermes_acceptance import accept_hermes_chat

    def build_plan(prompt: str) -> "Plan":
        return Plan(steps=[Step(
            kind=kind, params={"prompt": prompt},
            estimated_usd=estimated_usd, max_usd=max_usd, timeout_s=timeout_s,
        )])

    return LoopController(
        coordinator,
        build_plan=build_plan,
        accept_fn=accept_fn or accept_hermes_chat,
        config=LoopConfig(max_attempts=max_attempts, min_attempt_usd=min_attempt_usd,
                          loop_deadline_s=loop_deadline_s),
        on_event=on_event, now_fn=now_fn,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vizir_loop.py -k make_loop -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/vizir/loop.py tests/test_vizir_loop.py
git commit -m "feat(vizir-loop): make_loop factory + real accept_hermes_chat integration (fake Hermes, \$0)"
```

---

### Task 11: Full suite green + checkpoint (NO live run)

**Files:** none (verification + checkpoint only)

- [ ] **Step 1: Run the whole loop test file**

Run: `python -m pytest tests/test_vizir_loop.py -v`
Expected: ALL pass (≈15 tests). If any fail, fix inline and re-run.

- [ ] **Step 2: Run the full vizir suite (no regressions in the isolated package)**

Run: `python -m pytest tests/ -k vizir -v`
Expected: all previously-green vizir tests still pass; the new loop tests pass. (Pre-existing non-vizir techdebt failures — pydantic/runpod/mobile_ux — are out of scope; confirm the count is unchanged vs baseline `git stash`-clean if in doubt.)

- [ ] **Step 3: Confirm additive isolation (nothing outside loop touched)**

Run: `git diff --name-only phase-4.0-unified-jarvis...vizir-loop`
Expected: only `app/services/vizir/loop.py`, `tests/test_vizir_loop.py`, `docs/specs/vizir-loop.md`, `docs/superpowers/plans/2026-07-01-vizir-loop.md`. NO changes to `coordinator.py`/`handlers*.py`/`hermes_acceptance.py`/`models.py`.

- [ ] **Step 4: Final commit + STOP for Daniil (checkpoint)**

```bash
git add docs/superpowers/plans/2026-07-01-vizir-loop.md
git commit -m "chore(vizir-loop): mocks phase green — LoopController + teeth, \$0, ready for live break-in gate"
```

**CHECKPOINT — return to Daniil.** Report: all teeth proven on mocks ($0), six stops + spies green, isolation confirmed. Do NOT run live. The live break-in (knee#1 generation, `budget_usd ≈ $0.50`, `max_attempts=2–3`, prod ledger isolated `charge_logger=None`) is a SEPARATE step requiring Daniil's explicit OK, per spec §6.

---

## Self-review

**Spec coverage:**
- §1 loop-mechanika (external cycle over Hermes step, thin controlled executor) → Task 2 (`run()`), Task 10 (`make_loop`, single Hermes step). ✓
- §2 money teeth on whole cycle (shared budget + reserve-before-attempt + partial charge on breach) → Task 4 (reserve), Task 5 (cost-cap partial charge/stop). Shared budget threaded via `remaining` into per-attempt Task. ✓
- §3 six stops (max_attempts/completed/budget/cost_cap/stalled/deadline) → Tasks 2,4,5,6,7. ✓
- §4 behavior teeth (acceptance each attempt, reasons injection, immutable base vs goal-drift, escalation) → Task 3 (injection+immutable), Task 10 (real acceptance + truncated-rejected), `needs_escalation` in every `_stopped`. ✓
- §5 break-in TDD spy-teeth first → Task 9 (four spies), exit tests Tasks 2–7. ✓
- §6 money $0, live gated → Task 11 checkpoint (no live). ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; every run step shows exact command + expected output. ✓

**Type consistency:** `LoopReport`/`LoopConfig` field names identical across Tasks 1,2,10. Seam names (`_reserve_allows`, `_is_hard_stop`, `_is_stalled`, `_deadline_exceeded`, `_compose_prompt`) identical in definition (Task 2) and sabotage (Task 9). `accept_fn(result_dict) -> AcceptanceResult(accepted, reasons)` matches `hermes_acceptance.py`. `Coordinator.run(task, plan) -> Report` with `.status`/`.total_cost_usd`/`.steps[].result` matches `coordinator.py`. ✓
