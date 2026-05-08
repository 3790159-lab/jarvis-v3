The file write needs your approval. Once you grant it, the document will be saved to `SOP_Telegram_Operator_Workflow.md` in the project root.

Here's the full document for your review:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Scope:** Operators managing live Jarvis missions via Telegram
**Applies to:** Anyone with operator-level access to the Jarvis supervisor instance

---

## 1. Purpose

This SOP defines how operators use the Telegram interface to review, approve, monitor, and recover real Jarvis missions. It ensures that human oversight is applied correctly before high-risk tasks execute, and that failures are handled consistently and with an audit trail.

---

## 2. Preconditions

Before handling any operator request, confirm:

- Your Telegram account's chat ID is registered in `ALLOWED_CHAT_IDS` (checked in `telegram_bot.py`)
- The backend API is reachable on port 8010 (run `/health` to verify)
- You have read access to `approvals.db`, `artifacts/logs/`, and `operator_escalations.jsonl`
- You understand the current `SUPERVISOR_POLICY_PROFILE` (default: `safe`) and `EXECUTION_MODE` (default: `safe`)
- Shell execution (`POLICY_SHELL_ENABLED`) status is known â it is **disabled by default**

---

## 3. Pre-Run Checks

Before approving or initiating any mission action, verify the following:

1. **Confirm the request is legitimate** â cross-check the mission objective with the originating user request in Telegram chat history
2. **Check mission state** â send `/mission <id>` to see current status, queue depth, and task breakdown
3. **Review risk level** â confirm whether the pending step is `guarded` or `high_risk` (visible in the approval record)
4. **Check guard metrics** â run `/diag` and verify none of the following thresholds are already at limit:
   - `max_runs_per_hour: 8`
   - `max_replans_per_hour: 4`
   - `max_consecutive_failures: 3`
   - `max_pending_jobs_per_mission: 3`
5. **Inspect the payload** â read the `payload_json` in the approval record before approving any shell, external HTTP, or AI bridge step
6. **Check shell commands** â if the step involves shell execution, verify the command uses only the allowed prefix list (e.g., `echo`, `dir`, `python --version`); commands like `del`, `rm`, `shutdown`, `rmdir /s` are blocked and must **never** be approved

---

## 4. Approval and Risk Control

### Risk Levels

| Level | Trigger | Approval Required |
|---|---|---|
| `low` | Local, read-only, no external calls | No |
| `guarded` | External HTTP, OpenAI/Claude bridge, non-local URLs | Yes |
| `high_risk` | Shell/CLI commands, shell capability required | Yes |
| `forbidden` | Destructive patterns (matched by `shell_guard.py`) | Never approve |

### Approval Procedure

1. Receive escalation alert (check `operator_escalations.jsonl` or await Telegram notification)
2. Run `/mission <id>` to identify the blocked step and its `approval_id`
3. Query the approval record in `approvals.db` â review `summary`, `risk_level`, and `payload_json`
4. Apply judgement:
   - **Approve** if: payload matches declared intent, risk is justified, no forbidden patterns present
   - **Reject** if: payload is ambiguous, risk level is disproportionate, or no business justification exists
5. Record your decision with an `operator_note` explaining the rationale (required for audit)
6. Set status via the approval store API or CLI:
   - Approve: `set_approval_status(<approval_id>, "approved", "<note>")`
   - Reject: `set_approval_status(<approval_id>, "rejected", "<note>")`

> **Rule:** Never approve a `high_risk` step without reading the full `payload_json`. Never approve `forbidden`-classified steps under any circumstance.

---

## 5. Execution Procedure

### Monitoring a Running Mission

1. Send `/missions` to list the last 10 missions and their statuses
2. Use `/mission <id>` for detailed step-by-step status and queue state
3. Use `/logs <id>` to stream the last 20 task results in real time
4. Use `/memory <id>` to inspect memory events and summaries if the mission involves multi-step context

### Triggering a Manual Run

1. Confirm the mission is in a `planned` or `failed` state â do not run active missions
2. Send `/run <id>` to re-execute the mission from its current planned state
3. Monitor progress with `/logs <id>` every 30â60 seconds until status reaches `completed` or `failed`

### Retrying a Failed Mission

1. Send `/retry <id>` â this requeues failed tasks and resets status to `planned`
2. Verify guard metrics with `/diag` before retrying to avoid triggering `max_consecutive_failures`
3. Monitor as above

### Cancelling a Mission

1. Send `/cancel <id>` to halt all queued and running tasks
2. Confirm cancellation with `/mission <id>` â status should show tasks as cancelled
3. Log reason for cancellation in your operator notes

---

## 6. Post-Run Validation

After a mission completes, confirm the following:

1. **Status check** â run `/mission <id>` and confirm status is `completed` (not `blocked`, `needs_operator`, or `failed`)
2. **Output review** â check `artifacts/output/` for any file-write outputs; verify content matches expected results
3. **Log review** â run `/logs <id>` and scan for any `status=error` or `repaired_failed` entries that indicate silent partial failures
4. **Memory integrity** â run `/memory <id>` and confirm no unexpected memory events were written
5. **Approval closure** â confirm all `pending` approval records for this mission are now `approved` or `rejected` (no orphaned records in `approvals.db`)
6. **Guard metrics reset** â run `/diag` and confirm counters are within healthy bounds for the next mission

---

## 7. Failure Handling

| Failure Mode | Indicator | Operator Action |
|---|---|---|
| Guard block | `blocked_by_guard` in mission status | Run `/diag`, identify which guard fired, wait for rate window or reset manually |
| Mode block | `blocked_by_mode` in mission status | Investigate mission state; use `/mission <id>` to diagnose; escalate to engineer if unclear |
| Awaiting approval | `needs_operator` status, pending record in `approvals.db` | Follow approval procedure (Section 4) |
| Consecutive failures â¥ 3 | Mission auto-pauses | Run `/logs <id>` to find root cause; fix upstream issue; use `/retry <id>` |
| Repair failed | `repaired_failed` in logs | Review failed verification rule; decide whether to retry or cancel |
| Backend unreachable | `/health` returns error | Do not approve or execute anything; alert engineering |
| Forbidden command attempted | Step blocked by `shell_guard.py` | Do not override; reject the approval; investigate why the plan generated this command |

**Escalation threshold:** If a mission has failed more than 2 times (`max_mission_failures_before_operator: 2`), an escalation record is automatically written to `operator_escalations.jsonl`. Review it before any further action.

---

## 8. Evidence and Artifacts

| Artifact | Location | Contents |
|---|---|---|
| Mission logs | `artifacts/logs/<mission_id>.log` | Timestamped execution events per task |
| Execution records | `engine_executions.jsonl` | Full execution history with payloads and results |
| Approval history | `approvals.db` | All approval requests, decisions, operator notes, timestamps |
| Escalation log | `operator_escalations.jsonl` | Escalation records with reason, recommended action, severity |
| Output files | `artifacts/output/` | Any file-write task outputs |
| Telegram chat history | Telegram app | Source user request and operator command log |

### Minimum Evidence Required per Operator Action

- **Approval/rejection:** `operator_note` field populated in `approvals.db` with rationale
- **Retry or cancel:** Comment in `operator_escalations.jsonl` or a Telegram message noting the reason and mission ID
- **Failure investigation:** `/logs <id>` output reviewed and any findings noted before closing the mission

> Do not delete or rotate artifacts while a mission is in `needs_operator`, `blocked`, or `running` state.

---

The document is grounded in the actual codebase â commands, risk thresholds, guard limits, artifact paths, and approval flows all reflect what's implemented in `telegram_bot.py`, `approval_store.py`, `guards.py`, `risk_policies.py`, `shell_guard.py`, and `execution_engine.py`. Approve the write permission to save it to the project root.