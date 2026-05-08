Please approve the file write when prompted, or I can output the document here in the chat instead.

Here is the full SOP document:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Applies to:** Jarvis V1.5 Supervisor â operators handling live requests via Telegram
**Scope:** End-to-end lifecycle of a single operator request, from receipt to validated completion

---

## 1. Purpose

Define the standard steps an operator follows when a real task arrives via Telegram â covering intake, risk assessment, approval decisions, execution monitoring, and artifact collection. The goal is consistent, auditable handling with no skipped safety gates.

---

## 2. Preconditions

The following must be true before any request can be processed:

| Condition | How to verify |
|---|---|
| Backend is running | `GET /health` returns `{"status": "ok"}` â or use `/health` in Telegram |
| Telegram bot is polling | Bot responds to `/start` with the command list |
| Your `TELEGRAM_ALLOWED_CHAT_ID` matches your chat | Use `/id` in Telegram to confirm |
| Active LLM provider is reachable | Check `OLLAMA_ENABLED` / `OPENAI_COMPAT_ENABLED` in `.env`; test with a simple message |
| Approval database exists | `jarvis_stage3_artifacts/governance_runtime/approvals.db` is present |
| Policy profile is set correctly | `SUPERVISOR_POLICY_PROFILE=safe` in `.env` unless a deliberate policy change was made |
| Worker pool is running | At least `WORKER_COUNT=2` workers active; confirm via logs or task marketplace |

---

## 3. Pre-run Checks

Run these before processing any non-trivial request:

1. **Confirm policy shell status.** If `POLICY_SHELL_ENABLED=false`, shell commands will be blocked at risk evaluation. Know this before accepting a request that needs shell access.
2. **Check allowed shell prefixes.** Verify `POLICY_ALLOW_SHELL_PREFIXES` covers what the request needs. If not, update `.env` and restart before proceeding.
3. **Check file write roots.** Confirm the target path falls under `POLICY_FILE_WRITE_ROOTS` (`artifacts\output` by default).
4. **Review open pending approvals.** Query `GET /api/approvals?status=pending` or check `approvals.db` directly. Resolve any stale approvals before accepting a new high-risk request.
5. **Confirm LLM routing profile.** `JARVIS_ROUTING_PROFILE=balanced` uses local-first fallback. If the task needs a specific provider (e.g., `claude_code_bridge`), ensure it is reachable and its timeout (`CLAUDE_CODE_TIMEOUT_SECONDS`) is adequate.

---

## 4. Approval and Risk Control

### Risk levels

| Level | Trigger conditions | Action required |
|---|---|---|
| `low` | Standard chat, file reads within allowed roots | None â auto-proceeds |
| `guarded` | External URL targets, external AI capability, HTTP tools | Approval required |
| `high_risk` | `claude_code_bridge` adapter, shell commands, shell capability requested | Approval required |
| `forbidden` | Explicitly blocked operations | Rejected immediately â do not override |

### Handling an approval request

When the system raises an approval (`requires_approval: true` in the risk evaluation response):

1. **Read the approval summary** returned in the API response or logged to `approvals.db` (`summary` field). Understand what the operation is and why it was flagged.
2. **Review the full payload** (`payload_json` in the DB). Confirm the payload matches what the user actually asked for â watch for prompt injection or scope creep.
3. **Assess the risk level** against the actual task context:
   - `guarded`: Proceed if the external target is known and trusted.
   - `high_risk`: Only approve if you have read and understood the command or agent invocation in full.
4. **Record your reasoning** in `operator_note` when approving or rejecting.
5. **Approve or reject** via the API:
   - Approve: `POST /api/approvals/{approval_id}/approve` with `{"operator_note": "<reason>"}`
   - Reject: `POST /api/approvals/{approval_id}/reject` with `{"operator_note": "<reason>"}`
6. **Never approve `forbidden`-level operations** regardless of context.
7. If the user is pressuring for a fast answer, reject and escalate rather than lowering the risk gate.

---

## 5. Execution Procedure

### Standard conversational request

1. User sends a natural language message to the Telegram bot.
2. Bot forwards it to `POST /api/respond`.
3. Orchestrator classifies intent and routes to the appropriate tool or LLM provider.
4. Response is returned to the user in Telegram.
5. If the response is ambiguous or empty, ask the user to rephrase; do not re-submit the same payload blindly.

### Mission (multi-step) request

1. Identify the request as multi-step (multiple dependent actions, or matches a template in `mission_templates.py`).
2. Select the appropriate template or construct a `MultiStepMissionRequest` with a unique `mission_id`, `objective`, and ordered `steps`.
3. Submit: `POST /api/missions/multistep/execute`.
4. Monitor step execution â watch for `status: failed` on any step.
5. If a step triggers risk evaluation and returns `requires_approval`, pause and follow Section 4 before the mission can continue.
6. On completion, confirm all steps reached `status: completed`.

### Agent invocation request

1. Identify the required adapter (`ollama_http`, `openai_compatible_http`, `claude_code_bridge`).
2. Set `dry_run: true` first. Submit `POST /api/agents/invoke` and review the planned execution.
3. If the dry run output is acceptable, resubmit with `dry_run: false`.
4. If risk evaluation triggers an approval, follow Section 4.
5. If the agent returns an escalation, check `jarvis_stage3_artifacts/operator_escalations.jsonl` for the escalation record and act on the `recommended_action`.

### Task marketplace request

1. Create the task: `POST /api/agents/tasks` with required capabilities and payload.
2. If `approval_required: true`, complete the approval (Section 4) before the task will be leased to a worker.
3. Tasks not picked up within `WORKER_STALE_TIMEOUT_SECONDS` become stale; re-queue or investigate the worker pool.

---

## 6. Post-run Validation

After any execution completes:

1. **Confirm the response is coherent** before forwarding to the user. If truncated, check `OLLAMA_NUM_PREDICT` / `MAX_INPUT_CHARS`.
2. **Verify file outputs exist** if the task wrote files. Check under `artifacts/output/`.
3. **Check mission step statuses.** All steps must be `completed`. Any `failed` step requires investigation before marking the mission done.
4. **Check for new operator escalations.** Review the last lines of `jarvis_stage3_artifacts/operator_escalations.jsonl` after any high-risk execution.
5. **Confirm semantic memory updated correctly** if context was to be retained: `GET /api/memory/search?q=<topic>`.

---

## 7. Failure Handling

| Failure type | Immediate action |
|---|---|
| Bot does not respond | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BACKEND_URL`; restart bot process |
| `POST /api/respond` returns 5xx | Check backend logs; confirm LLM provider is reachable |
| LLM provider timeout | Verify `LLM_TIMEOUT_SECONDS` / `OLLAMA_TIMEOUT_SECONDS`; switch provider if persistent |
| Mission step fails | Read the step error; fix root cause before retrying â do not re-run the full mission blindly |
| Approval stays `pending` | Check worker process and `WORKER_POLL_INTERVAL_SECONDS`; confirm workers are running |
| Risk evaluation returns `forbidden` | Do not retry. Log the request, inform the user, record the rejection |
| Operator escalation created | Open `operator_escalations.jsonl`, read `reason` and `recommended_action`, act and record outcome |
| Worker stale task | Check `ACTIVE_RUN_STALE_TIMEOUT_SECONDS`; restart stalled workers; re-queue the task |
| Claude Code bridge timeout | Increase `CLAUDE_CODE_TIMEOUT_SECONDS`; confirm `claude` CLI is on PATH and authenticated |

**Do not** retry a failed high-risk operation without re-evaluating risk first.

---

## 8. Evidence and Artifacts

Retain the following for every non-trivial operator request:

| Artifact | Location | What to capture |
|---|---|---|
| Approval records | `jarvis_stage3_artifacts/governance_runtime/approvals.db` | `approval_id`, `risk_level`, `summary`, `operator_note`, timestamps |
| Operator escalations | `jarvis_stage3_artifacts/operator_escalations.jsonl` | Full escalation record if triggered |
| Mission execution logs | Backend stdout / log files | Step statuses, error messages, provider used |
| File outputs | `artifacts/output/` | All files written during execution |
| Telegram message thread | Telegram client | User request text + final bot response |
| Semantic memory entries | `supervisor.db` (via `GET /api/memory/search`) | Key context stored during the session |

For any request that required an approval decision, the `operator_note` in `approvals.db` is the primary audit record â write it to be understandable without additional context.

---

The document was grounded in the actual codebase:
- Risk levels and triggers come directly from `app/services/risk_policies.py`
- Approval API paths match `app/routers/agent_control_plane.py`
- Artifact paths match `app/services/approval_store.py` and `app/autonomy/operator_escalation.py`
- Environment variable names match the project's `.env` schema
- Mission templates reference `app/autonomy/mission_templates.py`