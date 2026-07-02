# Plan — Vizir /task money ledger (record_cost + check_limit, Variant B)

**Spec:** `docs/specs/2026-07-02-vizir-task-money-ledger-design.md`
**Base:** `20e696e`  **Worktree:** `C:\jarvis_worktrees\vizir-task-money` (branch `vizir-task-money`)
**Discipline:** TDD, spy-teeth FIRST, mutation both directions, two-stage review, $0 (mocks only).
**Isolation:** touch only `app/handlers/vizir_task_handler.py`, `tests/test_vizir_task_handler.py`,
`tools/jarvis_smart_telegram_control.py:1215`. Core `app/services/vizir/*` and all existing
commands are NOT modified.

## Checkpoints
- **CP-1 (after spy-teeth):** all 5 teeth written and RED against current code; show mutation
  plan. STOP for OK.
- **CP-2 (after implementation + review):** full green, mutation A/B/C/D proven, two-stage
  review APPROVED, regression suite clean. STOP for OK before merge.

## Task 1 — Spy-tooth harness + teeth (RED first)
Extend the `_handler` fixture (`test_vizir_task_handler.py:54`) already recording
`check_limit`/`record_cost` calls. Add tests:
- **T1.1** accepted (single attempt) → `record_cost` called once `(chat_id_int, username,
  loop_spent_usd)`. (May already pass — keep as regression anchor.)
- **T1.2** refusal (`ok=False`) → `record_cost == []`. (Anchor.)
- **T1.3** check_limit denial → `record_cost == []`, reply escalated, spend $0. (Anchor.)
- **T1.4 (NEW, B-discriminator)** mock Hermes returns a rejected-by-acceptance paid result on
  attempt 1, an accepted paid result on attempt 2 → `record_cost` called **exactly once**
  with `rep.loop_spent_usd` (sum of both attempts), NOT twice. RED under current per-step
  wiring (would be called twice via charge_logger).
- **T1.5 (NEW, gate-before)** assert `check_limit` recorded BEFORE any `record_cost` and with
  the step est; on denial no spend.
Mutation notes documented per tooth (what sabotage makes each go red).

## Task 2 — Implement Variant B in `vizir_task_handler.py`
- `_build_loop`: pass `charge_logger=None` to `Coordinator(...)`; keep `check_limit_fn`.
- `run_task_phase`: after `rep = await loop.run(...)`, if `rep.accepted and self._record_cost
  is not None` → `self._record_cost(int(user_id if user_id is not None else chat_id),
  username, rep.loop_spent_usd)`.
Run T1.* → all green.

## Task 3 — Wire control:1215
Add `check_limit=_check_limit, record_cost=_cost.record_cost` to the `VizirTaskHandler(...)`
constructor. Add/confirm a wiring test in `tests/test_vizir_task_bot_wiring.py` that the
prod handler is constructed with both hooks non-None (spy-tooth 5 / regression). Verify
imports already present (`control:37`, `control:1371`).

## Task 4 — Regression + mutation sweep
- Full `tests/test_vizir_task_*.py` + hermes/vizir suites green (no regression).
- Reviewer runs mutation for teeth 1-4 in a scratch copy: sabotage → red, revert → green.
- Confirm existing videoref/swapbatch `record_cost` calls untouched (grep diff = only /task
  path added).

## Two-stage review (money-critical)
- Stage 1 (spec-fidelity): does the diff implement Variant B exactly (record once, on accept,
  loop_spent_usd; gate before; charge_logger not wired to ledger)?
- Stage 2 (quality/regression): additive only; core untouched; existing money paths intact;
  teeth genuinely fail on mutation.

## Done = merge
Green-all + both reviews APPROVED + CP-2 OK → clean FF `vizir-task-money` →
`phase-4.0-unified-jarvis`; restart bot via guardian; live `/task` → confirm one real
`/my_stats` ledger entry == Telegram number on a completed task. Rollback: `git reset --hard
20e696e` + restart.
