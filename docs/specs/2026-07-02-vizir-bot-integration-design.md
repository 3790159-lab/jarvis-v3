# Vizir bot integration — autonomous `/task` loop in Telegram (design)

**Date:** 2026-07-02
**Status:** design approved (Daniil), pre-plan
**Arc:** final Vizir arc — connect the proven autonomous LOOP (in prod @`c1f48e7`)
to the Telegram bot so Daniil gives tasks THROUGH the bot, sees the autonomous
work stream, and money is enforced in Telegram.

## Goal

A new class of bot interaction alongside face-swap/animation/video: give Vizir a
task in natural language, watch it iterate autonomously (attempts + feedback
injection), enforce money in Telegram, and receive the produced artifact — or an
honest escalation when it cannot finish.

Built on what already ships in prod (`phase-4.0-unified-jarvis` @`c1f48e7`):
Vizir core + money-gate + 4 mid-flight safeguards + Hermes knee#1 (generation) +
knee#2 (Docker execution) + autonomous `LoopController` (proven live: $0.21, 2
attempts, feedback injection, cap held). This arc adds ONLY the Telegram surface.

## Scope

- **v1 = knee#1 generation loop**, `docker_exec=False` (as proven live). Execution
  in Docker (knee#2) via `/task` is a later extension.
- **Admin-only** (Daniil). Friends excluded from this arc; the money wiring is
  built so opening to friends later is a config/allowlist change, not a rewrite.
- **`/task <описание>` = always auto-loop** (the flagship). Per-knee selection
  (generation / execution / plain) is a later UX addition.
- **Generic acceptance** for v1 (see §Acceptance). Domain acceptance
  (`accept_hermes_chat`) stays pluggable for the chat-generation demo.

Non-goals (explicit): friend access, per-knee menu, rich per-type acceptance,
resume-after-interruption (loop persistence FIX 2 — deferred to unattended),
paid LLM judge.

## Architecture — additive, follows existing bot patterns

Two new modules; existing commands untouched.

```
Telegram /task <desc>            (admin-only, handle_command)
   │  confirm button [▶️ Run (up to $X)] / [Cancel]
   ▼
_task_run_phase  (daemon thread + GenerationLock, like _swapbatch_run_phase)
   │  progress_cb → send() per loop transition
   ▼
VizirTaskHandler.run_task_phase(chat_id, base_prompt, progress_cb, user_id, username)
   │  builds LoopController(real Hermes handler, Coordinator w/ money hooks)
   │  runs loop.run(task, base_prompt)  ← the proven autonomous cycle
   ▼
HandlerReply(text=summary, documents=[artifact.html])   or escalation text
   │
   ▼
_task_apply_reply → send() + _send_local_document(html)
```

### New module 1 — `app/handlers/vizir_task_handler.py` (transport-agnostic)

Mirrors `FaceSwapHandler`'s long-running shape
(`run_swap_phase(chat_id, ..., progress_cb, *, user_id, username)`).

- `run_task_phase(chat_id, base_prompt, progress_cb, *, user_id, username) -> HandlerReply`
- Builds the loop via a small factory:
  - real Hermes handler: `make_hermes_handler(docker_exec=False, enabled_toolsets=[],
    disabled_toolsets=DEFAULT_DISABLED+["file","web","search"], max_iterations=6)`
    (no tools → Hermes returns full artifact inline in `final_response`, as proven live)
  - `Coordinator(reg, on_event=<event→progress_cb bridge>, charge_logger=<record_cost>,
    check_limit_fn=<check_limit>, state_dir=<state/vizir_tasks>)`
  - `LoopController(coord, build_plan, accept_fn=<generic or domain>,
    config=LoopConfig(max_attempts, min_attempt_usd, loop_deadline_s),
    on_event=<event→progress_cb bridge>)`
- Translates loop + coordinator events into `progress_cb(stage, payload)` calls
  (bot decides the Telegram wording), returns `HandlerReply` with the artifact or
  an escalation message.
- Pure wiring — no Telegram imports. Testable with a mock loop/coordinator ($0).

### New module 2 — wiring in `tools/jarvis_smart_telegram_control.py` (additive)

- `handle_command`: `if cmd == "/task": _task_dispatch(chat_id, rest_text)` — stores
  pending task text by chat_id, replies with estimate + hard cap + inline buttons
  `[▶️ Запустить (до $X)]`(`task:run`) `[Отмена]`(`task:cancel`).
- `handle_callback_query`: `task:run` → `_task_run_phase(chat_id)`; `task:cancel` →
  clear pending + ack.
- `_task_run_phase`: acquire `GenerationLock` (один под — одна задача; bail if busy),
  spawn daemon thread that runs `asyncio.run(handler.run_task_phase(...))` with a
  `_progress` callback that `send()`s messages, then `_task_apply_reply`, release lock.
- `_task_apply_reply`: `send(text)` + `_send_local_document(html)` for the artifact.
- **Admin-only:** `/task` is NOT added to `FRIEND_ALLOWED_COMMANDS`; `task:` is NOT
  added to the friend-allowed callback prefixes. `handle()`'s existing role gate
  (`get_role` → admin/friend/None) blocks non-admins with the standard message.

## 1. Entry / command

`/task <описание>` → pending state → confirmation message with estimate and hard
cap + `[▶️ Запустить (до $0.90)]` / `[Отмена]`. On confirm, the loop runs. This is
the ONLY new command; auto-loop is implicit.

## 2. Progress display (see the autonomous work)

`progress_cb(stage, payload)` maps core events to condensed Telegram messages
(only meaningful transitions — no per-iteration flood):

| event (loop / coordinator) | Telegram message |
|---|---|
| `loop_attempt_started` | 🔄 Попытка N/{max}… |
| `progress` (hermes iter cost) | ⚙️ Hermes работает… ${cost} (throttled) |
| `loop_attempt_rejected` (reasons) | ❌ Попытка N не прошла приёмку: {reasons}. Впрыскиваю фидбек в попытку N+1. |
| `loop_stopped` completed | (folded into final reply, §5) |
| `loop_stopped` other | (folded into escalation, §4) |

The `loop_attempt_rejected` message is the core value — Daniil SEES the directed
convergence (which acceptance reasons feed the next attempt).

## 3. Money in Telegram (critical)

- `Task.actor = str(chat_id)`; `Task.budget_usd = VIZIR_TASK_BUDGET_USD` (default
  $0.90) — **hard per-task cap** (the core's mathematical guarantee `spend ≤ budget`
  carries into the bot).
- **Wire the existing (currently-None) Vizir hooks to the real money system:**
  - `check_limit_fn = lambda actor, est: access_control.check_limit(int(actor), estimated_usd=est)`
    — BEFORE each attempt. Admin → `(True,"")` (unlimited), but `budget_usd` still
    hard-caps. Same wiring caps friends by daily limit when they're opened later.
  - `charge_logger = lambda actor, op, amt: cost_tracker.record_cost(int(actor), username, amt)`
    — AFTER each successful step. Vizir spend lands in `/my_stats` + daily buckets
    (unified budget with face-swap). Refusal not charged (core already guarantees).
- Other caps (proven live, from config): `min_attempt_usd=$0.20`, per-step
  `max_usd=$0.40`, `max_attempts=3`, `loop_deadline_s=600`.
- Confirmation shows the cap; final message shows the real spend.
- **Deliberate change from the CLI test posture:** in the bot, `charge_logger` is
  wired to the REAL ledger — recording the admin's spend IS the integration and is
  desired (visible in `/my_stats`). TDD uses a mock ($0). "Isolation from prod" now
  means: don't break existing commands + money-safety via check_limit + hard cap +
  confirmation.

## 4. Escalation to the bot (FIX 1 live)

On `rep.needs_escalation` (any non-completed stop: max_attempts / budget /
cost_cap / stalled / timeout, or an honest refusal/timeout reason from FIX 1) the
final message pushes Daniil:

> ⚠️ Задача не завершена: `{stopped_reason}`. Причина: `{rep.reasons}` (honest — not
> generic HTML noise, thanks to FIX 1). Потрачено ${spent} / кап ${budget}. Попыток {N}.

Partial artifact (if any) attached.

## 5. Artifact delivery

On `accepted`: `rep.last_result["final_response"]` (the artifact, e.g. HTML) →
write to `state/vizir_tasks/<task_id>.html` → `_send_local_document(chat_id, path)`
+ `✅ Готово за {N} попыток, ${spent}. Приёмка пройдена.` (knee#1 generation
returns the artifact inline in `final_response`, as proven live).

## 6. Isolation from prod (spy-tooth)

New handler file + additive branches only (`/task`, `task:` callback prefix, 3
dispatch functions). Existing commands (face-swap / animation / video) untouched
and NOT in friend lists. knee#1/#2/loop core NOT modified — only NEW wiring on top.
A regression spy-tooth asserts existing command dispatch is unchanged.

## Acceptance (v1 = generic; domain pluggable)

`accept_hermes_chat` checks specifically for an HTML chat, which does not fit an
arbitrary `/task`. v1 default = **generic acceptance**:

```
accepted  ⇔  stopped_reason == "completed"  AND  non-empty output
            (final_response OR artifact present)
reject    ⇔  not completed (→ inject "run did not complete") OR empty output
```

- Any task runs and delivers; money-safe regardless.
- Retry-with-feedback triggers when Hermes does not complete (`stopped_reason ≠
  completed`) or refuses (FIX 1 honest reason).
- Domain acceptance (`accept_hermes_chat`) stays trivially pluggable via the
  handler's `accept_fn` — used for the chat-generation demo, where the full
  retry-with-feedback "moment of truth" reproduces.
- Rich per-type acceptance is the next step (staircase: general+safe first, then
  picky acceptance per task type).

## Testing (TDD, $0 mocks → live)

Spy-teeth (mocks, $0):
1. **money wiring** — `check_limit_fn` called with `actor` before each attempt;
   `charge_logger`/`record_cost` called AFTER a successful step; refusal NOT charged.
2. **event→message mapping** — `loop_attempt_started` / `loop_attempt_rejected`
   (with reasons) / completed produce the expected `progress_cb` stages.
3. **escalation** — `needs_escalation=True` yields the escalation reply carrying the
   honest `rep.reasons` (FIX 1).
4. **artifact** — accepted run writes the file and returns it in `HandlerReply.documents`.
5. **admin-only gate** — `/task` absent from `FRIEND_ALLOWED_COMMANDS`; `task:` absent
   from friend callback prefixes (friend blocked).
6. **isolation regression** — existing command dispatch (face-swap/video) unchanged
   (spy that we didn't break the dispatcher).
7. **generic acceptance** — completed+non-empty → accepted; not-completed / refusal
   → retry then escalation.

Then **live supervised**: restart the bot, `/task` with real cents — separate OK.

## Money-safety summary (why this is safe)

- Hard per-task budget (`budget_usd`) — core's `spend ≤ budget` guarantee (proven
  airtight + live).
- `check_limit` before + `record_cost` after — same proven pattern as face-swap.
- Refusal/timeout not charged (core).
- Explicit confirmation button before any spend.
- Admin-only for v1; per-step + whole-loop + min-attempt caps.

## Files

- NEW `app/handlers/vizir_task_handler.py`
- NEW `tests/test_vizir_task_handler.py`
- EDIT `tools/jarvis_smart_telegram_control.py` (additive: `/task`, `task:` callback,
  3 dispatch functions)
- No changes to `app/services/vizir/*` core (loop/coordinator/handlers/acceptance),
  no changes to auth/cost modules, no changes to existing command handlers.
