# Vizir /task — money ledger wiring (record_cost + check_limit)

**Date:** 2026-07-02  **Base:** `20e696e` (`phase-4.0-unified-jarvis`)
**Status:** SPEC — awaiting OK before TDD.
**Scope:** money-critical. Two-stage review + mutation-proven spy-teeth mandatory.

## Problem (verified by code)

`/task` runs the autonomous LOOP but its handler is constructed **without money hooks**:

```python
# tools/jarvis_smart_telegram_control.py:1215
_TASK_HANDLER = VizirTaskHandler(artifact_dir=_Path("state/vizir_tasks"))
```

`check_limit=` and `record_cost=` exist in `VizirTaskHandler.__init__`
(`app/handlers/vizir_task_handler.py:99`) but are not passed → both `None`.

Consequences:
- **`record_cost=None`** → `charge_logger` not built (`vizir_task_handler.py:128-131`)
  → nothing written to the `/my_stats` ledger. The `$0.1582` shown in Telegram is
  `rep.loop_spent_usd` (`handler:176`), a separate display, **not** a ledger entry.
- **`check_limit=None`** → `check_limit_fn` stays `None` (`handler:124-127`) → the
  per-user daily/monthly gate (access_control) is **not consulted before spend**.
  (The loop's own `budget_usd` + reserve-before-attempt math still caps THIS task.)

## Decision (approved by Daniil, 2026-07-02)

**Variant B — charge-after-loop-acceptance** (not per-step) **+ wire check_limit.**

Rationale: the ledger is visibility-only (`cost_tracker.py:106`); real money-safety is
held by `check_limit` (gate) + the loop-budget mathematical guarantee, not the ledger.
B records **one** entry equal to the number shown in Telegram, only on success —
matching "pay only for accepted" and videoref/swapbatch "quoted == charged". Accepted
tradeoff: a loop that spends then fails writes nothing to `/my_stats` (the real spend
left the Anthropic balance and is visible only in logs).

## Money semantics (exact)

1. **Gate BEFORE spend (check_limit):** wire `check_limit=_check_limit` into the
   handler. It flows to the Coordinator as `check_limit_fn` (`handler:124-127`), which
   calls it per paid step inside `_money_gate_allows` (`coordinator.py:65-68`). On
   denial the step is `BLOCKED`, `status="stopped_budget"`, **step never executes →
   $0 spent** (`coordinator.py:171-179`). Consistent with videoref/swapbatch's single
   `check_limit` gate on the full est.

2. **Record AFTER loop acceptance (record_cost), ONCE:** the Coordinator's per-step
   `charge_logger` is **NOT** wired to the ledger under B (it would record each attempt
   as it completes = Variant A). Instead, in `run_task_phase`, after `loop.run(...)`:
   - if `rep.accepted` → `record_cost(actor_int, username, rep.loop_spent_usd)` exactly
     **once** (the total across all attempts = the Telegram number).
   - if not accepted (stalled / escalated / budget-blocked) → **no** record call.
   Signature matches existing callers: `_cost.record_cost(chat_id_int, _uname, AMOUNT)`
   (`control:1584/1823`). `run_task_phase` already receives the real
   `user_id=chat_id_int, username` (`control:1262`).

   Refusal/timeout steps are already not charged at the step level
   (`coordinator.py:242-247`); B additionally guarantees a *rejected loop* records
   nothing regardless of any partial step spend.

## Changes (additive; core `app/services/vizir/*` untouched)

**A. `tools/jarvis_smart_telegram_control.py:1215`** — wire both hooks:
```python
_TASK_HANDLER = VizirTaskHandler(
    artifact_dir=_Path("state/vizir_tasks"),
    check_limit=_check_limit,          # already imported control:1371
    record_cost=_cost.record_cost,     # already imported control:37
)
```

**B. `app/handlers/vizir_task_handler.py`** — switch record from per-step to on-accept:
- `_build_loop`: keep `check_limit_fn` wiring; pass `charge_logger=None` to the
  Coordinator (stop building it from `self._record_cost`). Per-step "charged" progress
  events are unaffected (Coordinator emits them regardless).
- `run_task_phase`: after `rep = await loop.run(...)`, if `rep.accepted` and
  `self._record_cost is not None` → call it once with
  `(int(user_id or chat_id), username, rep.loop_spent_usd)`.

No change to existing bot commands. `_send_local_document` and all videoref/swapbatch
`record_cost` calls are untouched.

## Spy-teeth (money-critical, mutation-proven both directions)

1. **Accepted → record_cost called EXACTLY once** with `(chat_id_int, username,
   loop_spent_usd)`; amount in ledger. Mutation: remove the call → red.
2. **Reject / escalation → record_cost NOT called.** Mutation: call unconditionally → red.
3. **check_limit called BEFORE spend; denial → loop does not spend (record_cost == [],
   escalated, $0).** Mutation: drop the gate → red.
4. **Multi-attempt accepted (the B-vs-A discriminator):** loop accepts on attempt 2 after
   a paid+rejected attempt 1 → record_cost called **once** with `loop_spent_usd` (the
   sum), **not once per attempt**. Mutation: revert to per-step charge_logger → red
   (would be called twice).
5. **REGRESS:** existing videoref/swapbatch ledger writes unbroken (additive) — a spy on
   `_cost.record_cost` distinguishes the /task path from the videoref path.

## Existing tests (impact)

`tests/test_vizir_task_handler.py` money tests (`:74`, `:88`, `:97`) are single-attempt /
refusal / denial and stay green under B (single-attempt `loop_spent_usd` == the one
step's cost). New multi-attempt test (spy-tooth 4) locks B and would fail under A.

## Out of scope (later tails)

- per-attempt `timeout_s=180` too small for heavy visual tasks — separate arc.
- friend-access limits — /task is admin-only today; check_limit wiring makes it
  multi-user-ready when friends are enabled.
```
