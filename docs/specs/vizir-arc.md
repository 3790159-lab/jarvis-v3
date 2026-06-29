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
- **Phase 4 — Jarvis-driver seam (design + stub).** Prove the same core runs under a
  non-CC driver. Full bot integration is a separate later arc.

## Safeguards & rollback

- $20 autonomy session cap; sam-vs-ask boundary + light-reversible white-list.
- Real project code → git worktree `vizir-phase1` (off live `phase-4.0-unified-jarvis`),
  commit per step, phase-boundary commit; bad phase = reset to the seam. TDD per step.
- Isolated package `app/services/vizir/` → minimal blast radius.
