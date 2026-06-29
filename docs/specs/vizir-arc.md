# Vizir — task coordinator arc (spec)

**Goal:** a reusable, transport/driver-agnostic task **coordinator**. CC drives it
now; later **Jarvis (the bot) drives the same core** to coordinate tasks for Daniil
and friends. Money-safety lives in the CORE so it transfers to Jarvis automatically.

## Architecture: one core, swappable driver

```
        ┌──────────── Vizir CORE (transport/driver-agnostic) ────────────┐
        │  Task → Plan(steps) → execute under MONEY-GATE → Report         │
        │  per-step state persist · structured events · policy boundary   │
        └─────────────────────────────────────────────────────────────────┘
               ▲                                  ▲
        CC-driver (now)                  Jarvis-driver (later)
```

The core knows nothing about CC or Telegram. Its only outward surface is
`on_event(event)` (driver renders) and the returned `Report`. Mirrors the proven
`batch_orchestrator` pattern (storage-aware, transport-agnostic, persist per
transition, never auto-resume an in-flight paid step).

## Core pieces (`app/services/vizir/`)

- `models.py` — `Task`(goal, **budget_usd**, **actor**, constraints), `Step`(kind,
  params, **policy**, estimated_usd, status, result, cost_usd, error), `Plan`,
  `Report`; `Policy{auto, requires_approval}`, `StepStatus{pending,done,blocked,
  needs_approval,failed,skipped}`.
- `handlers.py` — `HandlerRegistry` + `HandlerResult` (pluggable like `SwapEngine`;
  handlers dispatched by `Step.kind`; the Coordinator — not the handler — owns the gate).
- `coordinator.py` — the loop: policy gate → money gate (check-before) → execute →
  charge-after → persist → emit events → `Report`.

## Money-safety (baked into the core — product-level, not per-driver)

- **Per-task budget** `Task.budget_usd`: strict pre-check, **universal** (applies even
  to admin). `spent + estimated > budget` → BLOCK before spend, `stopped_budget`.
- **Actor per-user limit** (`actor_limits`): admin = unlimited; mapped actors (friend)
  capped, *additional* to the task budget. Single seam `_money_gate_allows` → Phase 3
  swaps in the proven `access_control.check_limit`.
- **Charge-after-success**: `ok=False` (refusal/failure) is **not charged** ('refusal не
  списан').
- **Policy boundary** `requires_approval`: never auto-run; surfaces `needs_approval`,
  `stopped_for_approval` — the product's "what Jarvis does alone vs needs Daniil".

Proven live first: free autonomy (lnav, run #1) + real-money gate (run #2, $0.05,
cap+gate+spy ALL_PASS). Vizir reuses those exact patterns in-orchestrator.

## Phase ladder (phase-checkpoint autonomy mode)

CC is autonomous *within* a phase, reports + STOPS at each seam (Daniil OK to proceed).

- **Phase 0 — Spec** (this doc). ✅
- **Phase 1 — Core, free, TDD.** ✅ models/registry/coordinator, budget+actor gate,
  no-charge-on-failure, approval policy, persistence, in-orchestrator spy. 19 tests
  green, $0. Built in git worktree (live bot untouched).
- **Phase 2 — CC-driver + one real FREE task e2e.** Thin driver renders events to chat;
  CC coordinates a small free task end-to-end. Proves the driver seam.
- **Phase 3 — First PAID StepHandler under the gate.** Wire the proven Grok motion-prompt
  as a handler; micro-cap + HARD_BOUND; `check_limit ДО → charge ПОСЛЕ` via the gate seam.
- **Phase 4 — Jarvis-driver seam (design + stub).** ✅ BaseDriver extracted (all
  wiring shared); CCDriver + stub JarvisDriver override only `_format`. Proven:
  both drivers yield an IDENTICAL Report + identical gate-wiring, differing only in
  rendering (JarvisDriver renders Telegram-markdown + [Approve]/[Reject] button
  stubs). 38 tests green, $0. Full live-bot integration + resume-after-approval is
  a separate later arc.

## Attaching powerful agents (Hermes, browser-use, …) as StepHandlers ("колена")

The endgame: Vizir is the spine; powerful capabilities clip on as StepHandlers,
each under the money-gate. A powerful autonomous agent is wrapped EXACTLY like
`grok_motion` (Phase 3) — as a **controlled executor**, never a parallel brain
(a parallel brain makes its own money/coordination decisions → desync).

The wrapping contract (what the handler protocol must guarantee):
1. **Bounded invocation.** One async `handler(step, ctx) -> HandlerResult`. The
   agent runs to completion within ONE step and returns control at the step
   boundary. No out-of-band side effects, no unmanaged background work.
2. **Single coordinator.** State flows only through `step.params` (in) and
   `ctx['results']` (out). The agent reads inputs and writes its output as the
   step result — no hidden shared state. Vizir alone sequences steps.
3. **Money-gate, both sides.** `estimated_usd` for the gate's check-before;
   `HandlerResult.cost_usd` for charge-after; `ok=False` on refusal/failure ->
   not charged. The per-task budget + actor `check_limit_fn` bound the spend.
4. **Policy.** Risky/powerful actions tagged `requires_approval` -> surfaced to
   Daniil (JarvisDriver buttons). Capability -> authority is set here.
5. **Reportable, never auto-resumed.** Per-step persistence already captures an
   interrupted run; a hung/crashed agent is reported, not silently resumed.

Small core additions to add (each TDD'd) BEFORE wrapping a variable-cost agent:
- **per-step cost cap** — abort the agent if its running spend exceeds the step
  cap (today the gate is pre-check only; powerful agents need mid-flight stop).
- **progress/cost callback in ctx** — let a long agent stream incremental spend
  so the gate can stop it mid-flight, and surface progress to the driver.
- **timeout + cancellation token in ctx** — so a hung agent can't stall the run.
- **quote phase** — for agents whose cost isn't known upfront, a cheap estimate
  call feeds `estimated_usd` before the real run.

With these, any agent (Hermes-class) clips onto the same spine: gated, coordinated,
approval-bounded, money-safe — power added without losing control.

## Safeguards & rollback

- $20 autonomy session cap; sam-vs-ask boundary + light-reversible white-list.
- Real project code → git worktree `vizir-phase1` (off live `phase-4.0-unified-jarvis`),
  commit per step, phase-boundary commit; bad phase = reset to the seam. TDD per step.
- Isolated package `app/services/vizir/` → minimal blast radius.
