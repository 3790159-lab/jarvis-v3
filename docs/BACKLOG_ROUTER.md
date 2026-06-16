# Router / Bot cleanup backlog (deferred — do NOT do during the hybrid-tools work)

These items were found during the read-only capability audit (2026-06-16). They are
intentionally OUT OF SCOPE for the router-hybrid-tools plan
(`docs/superpowers/plans/2026-06-16-router-hybrid-tools.md`). Each needs its own task.

## Dead-code duplicate command handlers (tools/jarvis_smart_telegram_control.py)
The dispatcher is an `if cmd == ...: return` chain, so an earlier branch shadows any
later one with the same command name:
- `/cancel` — branch at line ~4370 wins; `cmd_cancel` at ~4570 is unreachable.
- `/history` — branch at line ~4326 wins; persona `handle_history` at ~4882 is unreachable.
- `/lora_status` — persona branch ~4633 wins; Photo Studio `_ps_cmd_map` version ~4958 is unreachable.

Action (later): remove the shadowed branches; add a regression test asserting each
command resolves to exactly one handler. Do NOT touch during hybrid-tools work.

## Old Ollama router (app/services/llm_router.py)
- Single-file `route_message` on `llama3.2` (NOT the unified `app/services/unified/llm_router/` package).
- NOT used by the Telegram bot. Importers: `app/services/supervisor_core.py`, `app/services/dashboard_service.py`.

Action (later): confirm those two callers, then either delete or move under a clearly
named legacy module. Out of scope for router-hybrid work.

## Priority-2 router tools (separate plan, after enable)
- `brain_plan` (/brain) — ⚠️ templated plan + embedded web research; not a real autonomous analysis.
- `engineer_review` (/engineer) — ⚠️ templated plan/recommendation; `safe_to_auto_apply:false` always.
- `create_reminder` (/remind) — NL reminder scheduling.

Until added as tools, they remain command-only and are described honestly in the
router system prompt (see plan Stage 5).
