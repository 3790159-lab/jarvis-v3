# Vizir Bot Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only `/task <описание>` Telegram command that runs the proven autonomous Vizir LOOP (knee#1 generation), streams attempts + feedback injection to chat, enforces money via the real limit/ledger, and delivers the artifact — or an honest escalation.

**Architecture:** One transport-agnostic handler (`VizirTaskHandler`) builds a real `LoopController` over a real `Coordinator` with the money hooks wired to `access_control.check_limit` (before) + `cost_tracker.record_cost` (after success). Additive bot wiring in `jarvis_smart_telegram_control.py` (a `/task` dispatch, a `task:` confirm-callback, a daemon-thread run phase mirroring `_swapbatch_run_phase`). Core (`app/services/vizir/*`) and existing commands are NOT modified.

**Tech Stack:** Python 3.11 (jarvis venv `C:\jarvis\.venv`), pytest, asyncio, python-telegram-bot-style dispatch, existing Vizir core (`app/services/vizir/`).

**Reference (design):** `docs/specs/2026-07-02-vizir-bot-integration-design.md`

**Discipline:** TDD (spy-teeth first, proven by mutation where noted). $0 on mocks (real Coordinator + mock Hermes handler — the proven pattern from `tests/test_vizir_loop.py`). knee#1/#2/loop core untouched. Existing bot commands untouched (regression spy-tooth). Run all pytest from `C:\jarvis` with `C:\jarvis\.venv\Scripts\python.exe -m pytest ... -q` and `$env:PYTHONUTF8=1`.

---

## File Structure

- **Create** `app/handlers/vizir_task_handler.py` — `VizirTaskReply` dataclass, `accept_generic` acceptance, `VizirTaskHandler` (builds loop, wires money, runs, maps events, writes artifact). No Telegram imports.
- **Create** `tests/test_vizir_task_handler.py` — all $0 handler spy-teeth.
- **Modify** `tools/jarvis_smart_telegram_control.py` — additive: `/task` in `handle_command`, `task:run`/`task:cancel` in `handle_callback_query`, `_task_dispatch` / `_task_run_phase` / `_task_apply_reply` / pure helpers `_task_confirm_keyboard` / `_task_progress_text`.
- **Create** `tests/test_vizir_task_bot_wiring.py` — pure-helper tests + admin-only + isolation-regression spy-teeth.
- **No changes** to `app/services/vizir/*`, `app/services/auth/*`, `app/services/audit/*`, or existing command handlers.

---

## Task 1: `VizirTaskReply` + generic acceptance

**Files:**
- Create: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vizir_task_handler.py
# -*- coding: utf-8 -*-
"""Vizir /task handler — autonomous loop over the bot. $0 (real Coordinator + mock Hermes)."""
import asyncio
from pathlib import Path

from app.handlers.vizir_task_handler import VizirTaskReply, accept_generic


def _run(coro):
    return asyncio.run(coro)


def test_generic_acceptance_completed_nonempty_accepted():
    acc = accept_generic({"final_response": "anything non-empty", "stopped_reason": "completed"})
    assert acc.accepted is True
    assert acc.reasons == []


def test_generic_acceptance_not_completed_rejected_with_reason():
    acc = accept_generic({"final_response": "x", "stopped_reason": "max_iterations"})
    assert acc.accepted is False
    assert any("did not complete" in r for r in acc.reasons)


def test_generic_acceptance_empty_output_rejected():
    acc = accept_generic({"final_response": "", "stopped_reason": "completed"})
    assert acc.accepted is False
    assert any("empty output" in r for r in acc.reasons)


def test_vizirtaskreply_fields():
    r = VizirTaskReply(text="ok", document_path=Path("a.html"), escalated=False)
    assert r.text == "ok" and r.escalated is False and r.document_path == Path("a.html")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -q`
Expected: FAIL with `ModuleNotFoundError: app.handlers.vizir_task_handler`

- [ ] **Step 3: Write minimal implementation**

```python
# app/handlers/vizir_task_handler.py
# -*- coding: utf-8 -*-
"""Vizir /task handler — runs the autonomous LOOP for one bot task.

Transport-agnostic (no Telegram imports): builds a real LoopController over a
real Coordinator with the money hooks wired to the bot's access_control +
cost_tracker, runs the loop, maps core events to a progress callback, and
returns a VizirTaskReply (summary + artifact, or an honest escalation). Mirrors
FaceSwapHandler's long-running shape. See docs/specs/2026-07-02-vizir-bot-integration-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.vizir.hermes_acceptance import AcceptanceResult


@dataclass
class VizirTaskReply:
    text: str
    document_path: Path | None = None
    escalated: bool = False


def accept_generic(value: dict) -> AcceptanceResult:
    """v1 acceptance for arbitrary tasks: accept iff Hermes completed AND produced
    non-empty output. Mirrors accept_hermes_chat's stopped_reason check so a
    truncated run (max_iterations) is rejected and retried with directed feedback."""
    reasons: list[str] = []
    stopped = value.get("stopped_reason")
    if stopped and stopped != "completed":
        reasons.append(f"run did not complete (stopped_reason={stopped})")
    output = value.get("final_response") or value.get("artifact_path")
    if not output:
        reasons.append("empty output (no final_response/artifact produced)")
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir-bot): VizirTaskReply + generic acceptance (TDD)"
```

---

## Task 2: `VizirTaskHandler` — build loop + money wiring (the critical money spy-tooth)

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py`

Tests use the REAL `Coordinator` + `LoopController` with a MOCK Hermes handler (the proven `tests/test_vizir_loop.py` pattern) and SPY `check_limit`/`record_cost` callables — so the actual money wiring is exercised, $0.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_handler.py
from app.handlers.vizir_task_handler import VizirTaskHandler
from app.services.vizir.handlers import HandlerResult


def _mock_hermes(final_response="<html>ok</html>", stopped_reason="completed",
                 ok=True, cost=0.10, error=None):
    async def handler(step, ctx):
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)  # reserve-before-spend, like the real adapter
        if not ok:
            return HandlerResult(ok=False, error=error or "refused", cost_usd=0.0)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": stopped_reason})
    return handler


def _handler(tmp_path, hermes, check_limit=None, record_cost=None, **cfg):
    calls = {"check_limit": [], "record_cost": []}
    def _cl(uid, *, estimated_usd):
        calls["check_limit"].append((uid, estimated_usd))
        return (check_limit or (lambda u, estimated_usd: (True, "")))(uid, estimated_usd=estimated_usd)
    def _rc(uid, username, amount):
        calls["record_cost"].append((uid, username, amount))
        if record_cost:
            record_cost(uid, username, amount)
    h = VizirTaskHandler(
        hermes_handler=hermes, check_limit=_cl, record_cost=_rc,
        artifact_dir=tmp_path, budget_usd=cfg.get("budget_usd", 0.90),
        min_attempt_usd=cfg.get("min_attempt_usd", 0.20),
        max_usd=cfg.get("max_usd", 0.40), estimated_per_attempt_usd=0.15,
        max_attempts=cfg.get("max_attempts", 3), loop_deadline_s=cfg.get("loop_deadline_s", 600.0),
        accept_fn=cfg.get("accept_fn"),
    )
    return h, calls


def test_money_wiring_check_limit_before_and_record_cost_after_success(tmp_path):
    hermes = _mock_hermes(cost=0.10)
    h, calls = _handler(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make a page",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    # check_limit called with the actor (chat_id as int) BEFORE spending
    assert calls["check_limit"], "check_limit must be called before an attempt"
    assert calls["check_limit"][0][0] == 237616472
    # record_cost called AFTER the successful step with the real cost
    assert calls["record_cost"], "record_cost must be called after a successful step"
    assert calls["record_cost"][0][0] == 237616472
    assert abs(calls["record_cost"][0][2] - 0.10) < 1e-9


def test_money_wiring_refusal_not_charged(tmp_path):
    hermes = _mock_hermes(ok=False, error="content policy", cost=0.0)
    h, calls = _handler(tmp_path, hermes, max_attempts=1)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="x",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    # a refusal (ok=False) is never charged
    assert calls["record_cost"] == []


def test_money_wiring_check_limit_denial_blocks_spend(tmp_path):
    hermes = _mock_hermes(cost=0.10)
    h, calls = _handler(tmp_path, hermes,
                        check_limit=lambda u, estimated_usd: (False, "Дневной лимит исчерпан"))
    rep = _run(h.run_task_phase(chat_id=999, base_prompt="x",
                                progress_cb=lambda s, p: None, user_id=999, username="artem"))
    # denied by the limit gate -> nothing charged, escalated
    assert calls["record_cost"] == []
    assert rep.escalated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k money -q`
Expected: FAIL with `AttributeError`/`TypeError` (VizirTaskHandler not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# append to app/handlers/vizir_task_handler.py
import itertools

from app.services.vizir.coordinator import Coordinator
from app.services.vizir.handlers import HandlerRegistry
from app.services.vizir.handlers_hermes import DEFAULT_DISABLED, make_hermes_handler
from app.services.vizir.loop import LoopConfig, LoopController
from app.services.vizir.models import Plan, Step, Task

_TASK_COUNTER = itertools.count(1)


class VizirTaskHandler:
    """Runs the autonomous LOOP for one /task. Injectable deps keep it $0-testable
    (real Coordinator + LoopController + a mock Hermes handler)."""

    def __init__(
        self, *, hermes_handler=None, check_limit=None, record_cost=None,
        artifact_dir, budget_usd=0.90, min_attempt_usd=0.20, max_usd=0.40,
        estimated_per_attempt_usd=0.15, max_attempts=3, loop_deadline_s=600.0,
        accept_fn=None,
    ) -> None:
        # default Hermes = knee#1 generation, NO tools -> full artifact inline in
        # final_response (proven live). Tests inject a mock handler.
        self._hermes = hermes_handler or make_hermes_handler(
            docker_exec=False, enabled_toolsets=[],
            disabled_toolsets=list(DEFAULT_DISABLED) + ["file", "web", "search"],
            max_iterations=6)
        self._check_limit = check_limit
        self._record_cost = record_cost
        self._artifact_dir = Path(artifact_dir)
        self._budget_usd = float(budget_usd)
        self._min_attempt_usd = float(min_attempt_usd)
        self._max_usd = float(max_usd)
        self._estimated = float(estimated_per_attempt_usd)
        self._max_attempts = int(max_attempts)
        self._loop_deadline_s = float(loop_deadline_s)
        self._accept_fn = accept_fn or accept_generic

    def _build_loop(self, actor: str, username, on_event):
        reg = HandlerRegistry()
        reg.register("hermes", self._hermes)
        check_limit_fn = None
        if self._check_limit is not None:
            check_limit_fn = (lambda act, est:
                              self._check_limit(int(act), estimated_usd=est))
        charge_logger = None
        if self._record_cost is not None:
            # Coordinator AWAITS charge_logger (coordinator.py:257) -> it must be
            # an async callable. record_cost itself is sync, so wrap it.
            async def charge_logger(act, op, amt):
                self._record_cost(int(act), username, amt)
        coord = Coordinator(
            reg, on_event=on_event, charge_logger=charge_logger,
            check_limit_fn=check_limit_fn, state_dir=self._artifact_dir / "state")
        est, cap, tmo = self._estimated, self._max_usd, self._loop_deadline_s

        def build_plan(prompt):
            return Plan(steps=[Step(kind="hermes", params={"prompt": prompt},
                                    estimated_usd=est, max_usd=cap, timeout_s=180.0)])

        return LoopController(
            coord, build_plan=build_plan, accept_fn=self._accept_fn,
            config=LoopConfig(max_attempts=self._max_attempts,
                              min_attempt_usd=self._min_attempt_usd,
                              loop_deadline_s=tmo),
            on_event=on_event)

    async def run_task_phase(self, chat_id, base_prompt, progress_cb, *,
                             user_id=None, username=None) -> VizirTaskReply:
        actor = str(chat_id)
        task_id = "task-%s-%d" % (chat_id, next(_TASK_COUNTER))
        loop = self._build_loop(actor, username, on_event=lambda e: None)
        task = Task(task_id=task_id, goal=base_prompt[:80], actor=actor,
                    budget_usd=self._budget_usd)
        rep = await loop.run(task, base_prompt)
        # minimal: escalate unless accepted (event mapping + artifact in later tasks)
        return VizirTaskReply(text="", document_path=None, escalated=not rep.accepted)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k money -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir-bot): VizirTaskHandler loop build + money wiring (check_limit before, record_cost after, refusal not charged)"
```

---

## Task 3: Event → progress_cb mapping (see attempts + feedback injection)

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_handler.py
def test_progress_maps_attempts_and_rejection_reasons(tmp_path):
    # attempt 1 truncated (retry), attempt 2 completes -> we should SEE:
    # attempt_started x2, attempt_rejected with the reason, then accepted.
    seq = [("<html>x</html>", "max_iterations"), ("<html>ok</html>", "completed")]
    it = iter(seq)
    async def hermes(step, ctx):
        rc = ctx.get("report_cost")
        if rc:
            rc(0.05)
        fr, sr = next(it)
        return HandlerResult(ok=True, cost_usd=0.05,
                             result={"final_response": fr, "stopped_reason": sr})
    stages = []
    h, _ = _handler(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make page",
                          progress_cb=lambda s, p: stages.append((s, p)),
                          user_id=237616472, username="daniil"))
    kinds = [s for s, _ in stages]
    assert kinds.count("attempt_started") == 2
    rej = [p for s, p in stages if s == "attempt_rejected"]
    assert len(rej) == 1
    assert any("did not complete" in r for r in rej[0]["reasons"])  # feedback shown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k progress_maps -q`
Expected: FAIL (no `attempt_started` stages — progress_cb never called)

- [ ] **Step 3: Write minimal implementation**

Replace `run_task_phase` in `app/handlers/vizir_task_handler.py` to bridge events:

```python
    def _bridge(self, progress_cb):
        def on_event(e):
            t = e.get("type")
            if t == "loop_attempt_started":
                progress_cb("attempt_started", {"attempt": e.get("attempt")})
            elif t == "loop_attempt_rejected":
                progress_cb("attempt_rejected",
                            {"attempt": e.get("attempt"), "reasons": e.get("reasons", [])})
            elif t == "progress":
                progress_cb("working", {"note": e.get("note")})
        return on_event

    async def run_task_phase(self, chat_id, base_prompt, progress_cb, *,
                             user_id=None, username=None) -> VizirTaskReply:
        actor = str(chat_id)
        task_id = "task-%s-%d" % (chat_id, next(_TASK_COUNTER))
        on_event = self._bridge(progress_cb)
        loop = self._build_loop(actor, username, on_event=on_event)
        task = Task(task_id=task_id, goal=base_prompt[:80], actor=actor,
                    budget_usd=self._budget_usd)
        rep = await loop.run(task, base_prompt)
        return VizirTaskReply(text="", document_path=None, escalated=not rep.accepted)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k progress_maps -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir-bot): map loop events to progress callback (attempts + rejection reasons)"
```

---

## Task 4: Accepted path — write + return the artifact

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_handler.py
def test_accepted_writes_and_returns_artifact(tmp_path):
    html = "<html><body>hello jarvis</body></html>"
    hermes = _mock_hermes(final_response=html, cost=0.08)
    h, _ = _handler(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="make page",
                                progress_cb=lambda s, p: None,
                                user_id=237616472, username="daniil"))
    assert rep.escalated is False
    assert rep.document_path is not None and rep.document_path.exists()
    assert rep.document_path.read_text(encoding="utf-8") == html
    assert "Готово" in rep.text and "$" in rep.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k accepted_writes -q`
Expected: FAIL (`document_path is None`)

- [ ] **Step 3: Write minimal implementation**

Replace the tail of `run_task_phase` (after `rep = await loop.run(...)`):

```python
        if rep.accepted:
            html = ""
            if isinstance(rep.last_result, dict):
                html = rep.last_result.get("final_response") or ""
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            out = self._artifact_dir / ("%s.html" % task_id)
            out.write_text(html, encoding="utf-8")
            text = ("✅ Готово за %d попыток. Потрачено $%.4f (кап $%.2f). Приёмка пройдена."
                    % (rep.attempts, rep.loop_spent_usd, self._budget_usd))
            return VizirTaskReply(text=text, document_path=out, escalated=False)
        return VizirTaskReply(text="", document_path=None, escalated=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k accepted_writes -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir-bot): deliver artifact (write html + return in reply) on acceptance"
```

---

## Task 5: Escalation path — honest reason (FIX 1) in the reply

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_handler.py
def test_escalation_carries_honest_refusal_reason(tmp_path):
    hermes = _mock_hermes(ok=False, error="Claude отказался: content policy", cost=0.0)
    h, _ = _handler(tmp_path, hermes, max_attempts=1)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="spicy thing",
                                progress_cb=lambda s, p: None,
                                user_id=237616472, username="daniil"))
    assert rep.escalated is True
    assert "content policy" in rep.text                 # FIX 1 honest reason surfaced
    assert "не завершена" in rep.text or "не завершен" in rep.text
    assert "$" in rep.text                               # spent + cap shown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k escalation_carries -q`
Expected: FAIL (`rep.text` is empty on escalation)

- [ ] **Step 3: Write minimal implementation**

Replace the final `return VizirTaskReply(text="", ...escalated=True)` in `run_task_phase`:

```python
        reasons = "; ".join(rep.reasons) if rep.reasons else "(без деталей)"
        text = ("⚠️ Задача не завершена: %s. Причина: %s. "
                "Потрачено $%.4f / кап $%.2f. Попыток: %d."
                % (rep.stopped_reason, reasons, rep.loop_spent_usd,
                   self._budget_usd, rep.attempts))
        return VizirTaskReply(text=text, document_path=None, escalated=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k escalation_carries -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir-bot): escalation reply carries honest FIX 1 reason + spend"
```

---

## Task 6: Generic-acceptance end-to-end (completed→accept, truncated→retry→escalate)

**Files:**
- Test: `tests/test_vizir_task_handler.py`

Behavior test over the real loop (no new production code — locks the v1 acceptance contract end-to-end).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_handler.py
def test_generic_acceptance_completed_accepts_first_try(tmp_path):
    hermes = _mock_hermes(final_response="<html>done</html>", stopped_reason="completed", cost=0.05)
    h, _ = _handler(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="anything",
                                progress_cb=lambda s, p: None, user_id=1, username="daniil"))
    assert rep.escalated is False and rep.document_path is not None


def test_generic_acceptance_always_truncated_retries_then_escalates(tmp_path):
    hermes = _mock_hermes(final_response="<html>x</html>", stopped_reason="max_iterations", cost=0.05)
    h, _ = _handler(tmp_path, hermes, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="anything",
                                progress_cb=lambda s, p: None, user_id=1, username="daniil"))
    assert rep.escalated is True
    assert rep.stopped_reason if hasattr(rep, "stopped_reason") else True  # reply text form
    assert "did not complete" in rep.text
```

- [ ] **Step 2: Run test to verify it fails, then adjust**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_handler.py -k generic_acceptance_ -q`
Expected: the truncated test may reference `rep.text` containing the reason — since escalation text includes `rep.reasons` which for a truncated run is `["run did not complete (stopped_reason=max_iterations)"]`, `"did not complete"` is present. If the first test fails because acceptance rejected a completed run, that's a real bug — fix `accept_generic`. Both should PASS with Tasks 1-5 in place; if not, fix the code, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_vizir_task_handler.py
git commit -m "test(vizir-bot): lock generic-acceptance contract end-to-end (accept/retry/escalate)"
```

---

## Task 7: Bot pure helpers — confirm keyboard + progress text

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (add two pure module-level functions near the other `_task_*` helpers — place after the swapbatch helpers, ~line 836)
- Test: `tests/test_vizir_task_bot_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vizir_task_bot_wiring.py
# -*- coding: utf-8 -*-
"""Vizir /task bot wiring — pure helpers + admin-only + isolation regression. $0."""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


def test_confirm_keyboard_shows_cap_and_run_cancel():
    kb = mod._task_confirm_keyboard(cap_usd=0.90)
    # inline_keyboard: list of rows of {"text","callback_data"}
    flat = [btn for row in kb["inline_keyboard"] for btn in row]
    datas = [b["callback_data"] for b in flat]
    texts = " ".join(b["text"] for b in flat)
    assert "task:run" in datas and "task:cancel" in datas
    assert "0.90" in texts  # cap shown


def test_progress_text_attempt_and_rejection():
    assert "1" in mod._task_progress_text("attempt_started", {"attempt": 1})
    txt = mod._task_progress_text("attempt_rejected",
                                  {"attempt": 1, "reasons": ["run did not complete"]})
    assert "did not complete" in txt and ("фидбек" in txt.lower() or "feedback" in txt.lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_bot_wiring.py -k "confirm_keyboard or progress_text" -q`
Expected: FAIL with `AttributeError: module ... has no attribute '_task_confirm_keyboard'`

- [ ] **Step 3: Write minimal implementation**

Add near the swapbatch helpers in `tools/jarvis_smart_telegram_control.py`:

```python
def _task_confirm_keyboard(cap_usd: float) -> dict:
    return {"inline_keyboard": [[
        {"text": "▶️ Запустить (до $%.2f)" % cap_usd, "callback_data": "task:run"},
        {"text": "Отмена", "callback_data": "task:cancel"},
    ]]}


def _task_progress_text(stage: str, payload: dict) -> str:
    if stage == "attempt_started":
        return "🔄 Попытка %s…" % payload.get("attempt")
    if stage == "attempt_rejected":
        reasons = "; ".join(payload.get("reasons", []))
        return ("❌ Попытка %s не прошла приёмку: %s. Впрыскиваю фидбек в следующую попытку."
                % (payload.get("attempt"), reasons))
    if stage == "working":
        return "⚙️ Hermes работает… %s" % payload.get("note", "")
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_bot_wiring.py -k "confirm_keyboard or progress_text" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_vizir_task_bot_wiring.py
git commit -m "feat(vizir-bot): pure helpers _task_confirm_keyboard + _task_progress_text (TDD)"
```

---

## Task 8: Bot wiring — /task command, confirm callbacks, run phase, apply reply

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py`
  - `handle_command` (~line 5880): add `/task` branch
  - `handle_callback_query` (~line 3625): add `task:` branch
  - add `_task_dispatch`, `_task_run_phase`, `_task_apply_reply`, and a module-level `_TASK_PENDING` dict + a `_task_get_handler()` near the other `_*_dispatch` helpers (~line 836)

This task mirrors `_swapbatch_run_phase` (line ~939) for the daemon-thread + `GenerationLock` pattern. The pure logic is already covered by Task 7; this is the thin glue, validated by import + membership (Task 9) and the live run.

- [ ] **Step 1: Add the pending store, handler getter, and dispatch (no test — glue; covered by Task 9 + live)**

```python
# near the other _*_dispatch helpers (~line 836)
_TASK_PENDING: dict[str, str] = {}          # chat_id_str -> pending task description
_TASK_HANDLER = None

def _task_get_handler():
    global _TASK_HANDLER
    if _TASK_HANDLER is None:
        from app.handlers.vizir_task_handler import VizirTaskHandler
        from pathlib import Path
        _TASK_HANDLER = VizirTaskHandler(artifact_dir=Path("state/vizir_tasks"))
    return _TASK_HANDLER

def _task_dispatch(chat_id, description: str) -> None:
    chat_id_s = str(chat_id)
    desc = (description or "").strip()
    if not desc:
        send(chat_id_s, "Использование: /task <описание задачи>")
        return
    _TASK_PENDING[chat_id_s] = desc
    cap = float(__import__("os").environ.get("VIZIR_TASK_BUDGET_USD", "0.90"))
    send_with_keyboard(chat_id_s,
                       "🧠 Задача принята:\n%s\n\nЗапустить автономно (Vizir loop)?" % desc,
                       _task_confirm_keyboard(cap))
```

- [ ] **Step 2: Add the run phase (daemon thread + lock, mirrors `_swapbatch_run_phase`)**

```python
def _task_run_phase(chat_id) -> None:
    chat_id_s = str(chat_id)
    chat_id_int = int(chat_id)
    desc = _TASK_PENDING.pop(chat_id_s, None)
    if not desc:
        send(chat_id_s, "Нет задачи. Начни с /task <описание>.")
        return
    lock = _get_video_lock()
    try:
        token = lock.acquire(chat_id_int)
    except GenerationLockBusy:
        send(chat_id_s, "⏳ Уже выполняется задача. Подожди завершения.")
        return

    def _progress(stage: str, payload: dict) -> None:
        text = _task_progress_text(stage, payload)
        if text:
            send(chat_id_s, text)

    def _run() -> None:
        try:
            handler = _task_get_handler()
            username = _USERNAME_BY_CHAT.get(chat_id_s)
            reply = asyncio.run(handler.run_task_phase(
                chat_id_int, desc, _progress, user_id=chat_id_int, username=username))
            _task_apply_reply(chat_id_s, reply)
        except Exception as exc:  # never let the thread die silently
            send(chat_id_s, "❌ Vizir упал: %s" % exc)
        finally:
            lock.release(token)

    send(chat_id_s, "🚀 Запускаю Vizir…")
    threading.Thread(target=_run, daemon=True, name="vizir_task_%d" % chat_id_int).start()


def _task_apply_reply(chat_id_s, reply) -> None:
    if reply.text:
        send(chat_id_s, reply.text)
    if reply.document_path is not None:
        _send_local_document(chat_id_s, str(reply.document_path))
```

- [ ] **Step 3: Wire the command and callbacks**

In `handle_command` (~line 5880), add:

```python
    if cmd == "/task":
        _task_dispatch(chat_id, rest)   # `rest` = text after the command (as other cmds parse it)
        return
```

In `handle_callback_query` (~line 3625), add:

```python
    if data == "task:run":
        _task_run_phase(chat_id)
        return
    if data == "task:cancel":
        _TASK_PENDING.pop(str(chat_id), None)
        send(str(chat_id), "Отменено.")
        return
```

- [ ] **Step 4: Verify the module imports and existing tests still pass**

Run: `C:\jarvis\.venv\Scripts\python.exe -c "import tools.jarvis_smart_telegram_control as m; print(hasattr(m,'_task_dispatch'), hasattr(m,'_task_run_phase'))"`
Expected: `True True`

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/ -k "vizir or hermes" -q`
Expected: PASS (all prior vizir/hermes tests still green)

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vizir-bot): /task command + confirm callbacks + daemon run phase + apply reply"
```

---

## Task 9: Admin-only + isolation regression spy-teeth

**Files:**
- Test: `tests/test_vizir_task_bot_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vizir_task_bot_wiring.py
def test_task_is_admin_only_not_in_friend_lists():
    # /task must NOT be friend-allowed, and task: callbacks must NOT be friend-allowed
    friend_cmds = getattr(mod, "FRIEND_ALLOWED_COMMANDS", None)
    assert friend_cmds is not None
    assert "/task" not in friend_cmds
    # friend-allowed callback prefixes (tuple) must not include task:
    prefixes = getattr(mod, "FRIEND_ALLOWED_CALLBACK_PREFIXES", None) \
        or getattr(mod, "_FRIEND_ALLOWED_CALLBACK_PREFIXES", None)
    assert prefixes is not None
    assert not any(str(p).startswith("task:") or "task:".startswith(str(p)) for p in prefixes)


def test_existing_commands_still_present_isolation_regression():
    # the dispatcher glue for existing paid commands must be intact (we only added)
    for name in ("_swapbatch_dispatch", "_send_local_document", "_get_video_lock",
                 "handle_command", "handle_callback_query"):
        assert hasattr(mod, name), f"existing symbol {name} missing — integration broke the bot"
    # our additive symbols exist alongside them
    for name in ("_task_dispatch", "_task_run_phase", "_task_apply_reply",
                 "_task_confirm_keyboard", "_task_progress_text"):
        assert hasattr(mod, name)
```

- [ ] **Step 2: Run test to verify it fails (or passes) for the right reason**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_bot_wiring.py -k "admin_only or isolation" -q`
Expected: If `FRIEND_ALLOWED_CALLBACK_PREFIXES` has a different name, the test errors — fix the getattr name to the real symbol (from the explore: friend-allowed callback prefixes live near line 6532). Adjust the test to the real constant name, then it should PASS (we never added `/task` to friend lists).

- [ ] **Step 3: (If needed) confirm the real friend-list symbol names**

Run: `C:\jarvis\.venv\Scripts\python.exe -c "import tools.jarvis_smart_telegram_control as m; print([n for n in dir(m) if 'FRIEND' in n.upper()])"`
Use the printed names in the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/test_vizir_task_bot_wiring.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_vizir_task_bot_wiring.py
git commit -m "test(vizir-bot): admin-only gate + isolation regression spy-teeth"
```

---

## Task 10: Full suite green + arc close

**Files:** none (verification)

- [ ] **Step 1: Run the full vizir/hermes/task suite**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/ -k "vizir or hermes or task" -q`
Expected: all green, 0 regressions (prior 117 + new handler/wiring tests).

- [ ] **Step 2: Confirm no existing command test regressed**

Run: `C:\jarvis\.venv\Scripts\python.exe -m pytest tests/ -q` (full suite; note any pre-existing techdebt failures are unrelated — compare against the known baseline of 31 pre-existing failures documented in memory)
Expected: no NEW failures attributable to this arc.

- [ ] **Step 3: Confirm isolation — only the intended files changed**

Run: `git diff --stat <arc-base>..HEAD`
Expected: only `app/handlers/vizir_task_handler.py`, `tests/test_vizir_task_handler.py`, `tests/test_vizir_task_bot_wiring.py`, `tools/jarvis_smart_telegram_control.py`, and the two docs. No `app/services/vizir/*` core changes.

- [ ] **Step 4: STOP for live gate**

Do NOT run live. Report to Daniil: suite green, isolation confirmed, bot not yet restarted. Live = restart bot + `/task` with real cents, under a SEPARATE OK (supervised).

---

## Live test (AFTER separate OK — real cents, supervised)

1. Restart the bot (guardian will relaunch, or restart the service) so the new `/task` wiring loads. Confirm bot alive (5 procs / :8010 / guardians).
2. In Telegram (as admin): `/task Сделай тёмный неоновый одностраничный веб-чат JARVIS (порт 8010, статус online/offline)` → tap `[▶️ Запустить (до $0.90)]`.
3. Observe: attempt messages, rejection+feedback message (if any), final artifact document OR escalation with honest reason.
4. Confirm: real spend recorded (`/my_stats` reflects it), cap held, existing commands still work (run one face-swap/animate to be sure).

---

## Self-Review

- **Spec coverage:** §1 entry → Task 8; §2 progress → Tasks 3,7; §3 money → Task 2 (+7 cap display); §4 escalation → Task 5; §5 artifact → Task 4; §6 isolation → Task 9; acceptance → Tasks 1,6; testing → all. No gaps.
- **Placeholders:** none — every code step shows the code; commands show expected output.
- **Type consistency:** `VizirTaskReply(text, document_path, escalated)`, `VizirTaskHandler.run_task_phase(chat_id, base_prompt, progress_cb, *, user_id, username)`, `accept_generic(value) -> AcceptanceResult`, progress stages `attempt_started`/`attempt_rejected`/`working` used consistently across handler (Task 3) and bot text (Task 7). Note: Task 6's `rep` is a `VizirTaskReply` (no `stopped_reason` attr) — the truncated-run assertion uses `rep.text` (the escalation string carries the reason); the `hasattr` guard keeps it robust.
- **Known adjustment point:** Task 9 friend-list constant name — the plan instructs confirming the real symbol name at execution (explore located it near line 6532) rather than guessing.
