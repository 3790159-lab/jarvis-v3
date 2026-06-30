# Vizir × Hermes — powerful agent as a StepHandler (spec)

**Goal:** clip the Hermes Agent (Nous Research) onto the Vizir spine as the first
**Hermes-class "колено"** — a *controlled executor* under the money-gate, never a
parallel brain (a parallel brain makes its own money/coordination decisions →
desync). See the wrapping contract in `vizir-arc.md` §"Attaching powerful agents".

## Design principle (Daniil)

**Do NOT limit Hermes to one step — multi-step is its strength.** The split:

- **Hermes** runs FREELY multi-step *inside* one Vizir step (its own tool-calling
  loop, up to `max_iterations`), using its tools.
- **Vizir** holds the FRAME *above*: money (cost-cap), boundaries (timeout),
  and **acceptance** (did it do it right?). Vizir stays the sole conductor.

Hermes free inside, Vizir frames it from above.

## Recon facts (confirmed, $0, nothing installed)

- **Invocation:** all Hermes entry points converge on `AIAgent.run_conversation()`
  (`run_agent.py`). Programmatic one-shot: `agent = AIAgent(...); result =
  agent.run_conversation(user_message=..., task_id=...)` → dict with
  `final_response`, `messages` (+ metadata/usage). Python→Python in-process; no
  CLI/HTTP/gateway needed.
- **WSL2 not required:** Hermes runs natively on Windows. (This machine: WSL +
  VirtualMachinePlatform both Disabled, no Docker — native path is the only one
  we need.)
- **Engine = our Grok:** native `provider:"xai"` / `XAI_API_KEY` (also native
  Anthropic). No new accounts.
- **Leaf-executor lockdown:** `delegation.orchestrator_enabled:false` (no Docker
  subagents — for knee #1), `disabled_toolsets` excludes `terminal`,
  `code_execution`, `delegation`. Multi-step preserved via `max_iterations`.
- **Cost/observability counters on the agent:** `session_estimated_cost_usd`,
  `session_total_tokens`, … read live in a per-iteration callback.
- **Mid-flight stop:** `step_callback` per iteration; `pre_tool_call` hook can
  return `{"action":"block"}` to veto the next tool call. No built-in budget hook
  → Vizir enforces the cap.

## Components (this arc)

- `app/services/vizir/handlers_hermes.py` — `make_hermes_handler(run_fn=None,
  model, enabled_toolsets, disabled_toolsets, max_iterations)` → async
  `hermes(step, ctx) -> HandlerResult`. Mirrors `handlers_grok` (injectable
  `run_fn` for $0 tests; lazy default binds real `AIAgent`).
  - Reads `step.params` (`prompt` required; optional `model`/`enabled_toolsets`/
    `disabled_toolsets`/`max_iterations`).
  - **Bridge:** Hermes calls `on_step(cumulative_cost, note)` each iteration; the
    handler emits `ctx["report_progress"](note)` and `ctx["report_cost"](delta)`
    (reserve-before-next-iteration; RAISES `StepBudgetExceeded` when the cap would
    be crossed → propagates → Hermes stops before the next iteration).
  - Normalizes → `HandlerResult(ok, cost_usd, result={final_response,
    artifact_path, iterations, stopped_reason, tokens})`. Empty/no-output →
    `ok=False` (not charged, 'refusal не списан').
- `app/services/vizir/hermes_acceptance.py` — Vizir's приёмка (separate from the
  handler). `check_chat_acceptance(html)` + `accept_hermes_chat(value)` →
  `AcceptanceResult(accepted, reasons)`. Deterministic, **no paid LLM judge**.

## Mid-flight wiring (our contract ← Hermes counters)

| Vizir safeguard | Hermes source | Mechanism |
|---|---|---|
| cost-cap | `session_estimated_cost_usd` | per-iteration delta → `report_cost` (reserve-before-spend); breach → `StepBudgetExceeded` → `stopped_cost_cap`, charge `step_spent` (partial) |
| timeout | `max_iterations` (+ `child_timeout_seconds` later) | `Step.timeout_s` wraps the handler in `asyncio.wait_for` (Coordinator) |
| progress / report_cost | `session_*` counters | `report_progress` notes + `cost_progress` events each iteration |

**Charge truthfulness:** clean success → `charge-after = result.cost_usd` (actual
total). Cost-cap breach → charge the approved cumulative (`step_spent`); the single
tipping iteration's marginal cost (≈ one Grok iteration, cents) is real spend not
yet in the ledger — reconciled in the live phase from final
`session_estimated_cost_usd`. The safety guarantee (Hermes STOPS, no runaway)
holds exactly.

## Acceptance (Vizir checks "verno li vypolneno")

Two levels, Vizir-side:
1. **Completion:** `stopped_reason == "completed"`; a `max_iterations` / `cost_cap`
   truncated run is **rejected** up front.
2. **Deterministic contract check** of the artifact (knee #1 = Jarvis web chat):
   HTML doc · chat structure (input + send + messages) · online/offline status ·
   dark futuristic theme (dark bg + neon cyan/blue accent) · JS POSTs to backend
   `localhost:8010` via `const API_URL`. Failing reasons are reported so Vizir can
   retry / escalate to Daniil. (LLM-judge acceptance is a later upgrade.)

## First task for Hermes (knee #1, Path A — generation, not execution)

Generate a working Jarvis-styled web chat as ONE self-contained HTML file
(HTML+CSS+JS inline, opens in a browser, no build): dark futuristic theme, neon
cyan/blue accents; message history (own right / replies left, autoscroll); input +
send (Enter sends); online/offline status; JARVIS title; JS POSTs to
`const API_URL = "http://localhost:8010"`, handles offline gracefully; desktop
layout. `enabled_toolsets = ["file","web","search"]`;
`disabled = ["terminal","code_execution","delegation","cronjob","messaging"]`.

## Money order (mocks → live)

- **Phase M — mocks, $0, TDD (DONE):** handler normalizes result; empty→not
  charged; per-iteration report_cost/progress bridge; cost-cap breach → BLOCKED +
  partial charge + `stopped_cost_cap` (real Coordinator); toolset-lockdown +
  Grok engine; param overrides; acceptance accept/reject + truncated→not accepted;
  **spy-teeth** (broken bridge overspends undetected). Built in worktree
  `vizir-hermes` (live bot/branch untouched).
- **Phase L — first LIVE run:** under a small `cost_cap_usd`, **with Daniil's
  confirmation**, **NOT an autonomous loop**. Confirm the real `AIAgent` adapter
  against `run_agent.py` source (callback wiring, result keys), one real Grok run,
  watch `session_estimated_cost_usd` live, verify acceptance + report_cost,
  isolated prod ledger.
- **Loop — only AFTER** the live connection is proven (now: mocks + one manual
  live run).

## Open items for Phase L

- Confirm exact `run_conversation()` signature & result keys, and that
  `step_callback` exceptions propagate (else use `pre_tool_call` block to stop).
- Finalize `enabled_toolsets` against the real Hermes toolset names.
- Ledger reconciliation of the one-iteration tipping cost on a cap breach.
