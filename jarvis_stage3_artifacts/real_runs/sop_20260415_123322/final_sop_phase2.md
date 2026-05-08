# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Scope:** Operators managing live Jarvis missions via Telegram
**Applies to:** Anyone with operator-level access to the Jarvis supervisor instance

## 1. Purpose

This SOP defines how operators use the Telegram interface to review, approve, monitor, and recover real Jarvis missions. It ensures that human oversight is applied correctly before high-risk tasks execute and that failures are handled consistently with an audit trail.

## 2. Preconditions

Before handling any operator request, confirm:

- Your Telegram account chat ID is registered in `ALLOWED_CHAT_IDS` (checked in `telegram_bot.py`)
- The backend API is reachable on port `8010` (run `/health` to verify)
- You have read access to `approvals.db`, `artifacts/logs/`, and `operator_escalations.jsonl`
- You understand the current `SUPERVISOR_POLICY_PROFILE` (default: `safe`) and `EXECUTION_MODE` (default: `safe`)
- Shell execution status via `POLICY_SHELL_ENABLED` is known; it is **disabled by default**

## 3. Pre-Run Checks

Before approving or initiating any mission action, verify all of the following:

1. **Confirm the request is legitimate**
   - Cross-check the mission objective against the originating user request in Telegram chat history.

2. **Check mission state**
   - Send `/mission <id>` to view current status, queue depth, and task breakdown.

3. **Review risk level**
   - Confirm whether the pending step is `guarded` or `high_risk` in the approval record.

4. **Check guard metrics**
   - Run `/diag` and verify none of these thresholds are already at limit:
     - `max_runs_per_hour: 8`
     - `max_replans_per_hour: 4`
     - `max_consecutive_failures: 3`
     - `max_pending_jobs_per_mission: 3`

5. **Inspect the payload**
   - Read `payload_json` in the approval record before approving any shell, external HTTP, or AI bridge step.

6. **Check shell commands**
   - If the step involves shell execution, verify the command uses only the allowed prefix list, such as:
     - `echo`
     - `dir`
     - `python --version`
   - Commands such as the following are blocked and must **never** be approved:
     - `del`
     - `rm`
     - `shutdown`
     - `rmdir /s`

## 4. Approval and Risk Control

### 4.1 Risk Levels

| Level | Trigger | Approval Required |
|---|---|---|
| `low` | Local, read-only, no external calls | No |
| `guarded` | External HTTP, OpenAI/Claude bridge, non-local URLs | Yes |
| `high_risk` | Shell/CLI commands, shell capability required | Yes |
| `forbidden` | Destructive patterns matched by `shell_guard.py` | Never approve |

### 4.2 Approval Procedure

1. Receive the escalation alert.
   - Check `operator_escalations.jsonl` or wait for the Telegram notification.

2. Identify the blocked step.
   - Run `/mission <id>` and note the `approval_id`.

3. Review the approval record in `approvals.db`.
   - Check:
     - `summary`
     - `risk_level`
     - `payload_json`

4. Make the decision.
   - **Approve** only if:
     - the payload matches the declared intent
     - the risk is justified
     - no forbidden patterns are present
   - **Reject** if:
     - the payload is ambiguous
     - the risk level is disproportionate
     - there is no business justification

5. Record the rationale.
   - Add an `operator_note` explaining the decision. This is required for audit.

6. Set the approval status using the approval store API or CLI:
   - Approve:
     - `set_approval_status(<approval_id>, "approved", "<note>")`
   - Reject:
     - `set_approval_status(<approval_id>, "rejected", "<note>")`

> **Rule:** Never approve a `high_risk` step without reading the full `payload_json`. Never approve `forbidden`-classified steps under any circumstance.

## 5. Execution Procedure

### 5.1 Monitoring a Running Mission

1. Send `/missions` to list the last 10 missions and their statuses.
2. Use `/mission <id>` for detailed step-by-step status and queue state.
3. Use `/logs <id>` to stream the last 20 task results in real time.
4. Use `/memory <id>` to inspect memory events and summaries if the mission involves multi-step context.

### 5.2 Triggering a Manual Run

1. Confirm the mission is in `planned` or `failed` state.
   - Do **not** run active missions.

2. Send `/run <id>` to re-execute the mission from its current planned state.

3. Monitor progress with `/logs <id>` every 30-60 seconds until status reaches `completed` or `failed`.

### 5.3 Retrying a Failed Mission

1. Send `/retry <id>`.
   - This requeues failed tasks and resets status to `planned`.

2. Verify guard metrics with `/diag` before retrying to avoid triggering `max_consecutive_failures`.

3. Monitor with `/logs <id>` every 30-60 seconds until the mission completes or fails again.

### 5.4 Cancelling a Mission

1. Send `/cancel <id>` to halt all queued and running tasks.
2. Confirm cancellation with `/mission <id>`.
   - Status should show tasks as cancelled.
3. Log the reason for cancellation in your operator notes.

## 6. Post-Run Validation

After a mission completes, perform all of the following:

1. **Status check**
   - Run `/mission <id>` and confirm status is `completed`, not `blocked`, `needs_operator`, or `failed`.

2. **Output review**
   - Check `artifacts/output/` for any file-write outputs.
   - Verify the content matches expected results.

3. **Log review**
   - Run `/logs <id>` and scan for:
     - `status=error`
     - `repaired_failed`
   - Treat either as evidence of a partial or silent failure until reviewed.

4. **Memory integrity**
   - Run `/memory <id>` and confirm no unexpected memory events were written.

5. **Approval closure**
   - Confirm all `pending` approval records for the mission are now `approved` or `rejected`.
   - There must be no orphaned records in `approvals.db`.

6. **Guard metrics reset**
   - Run `/diag` and confirm counters are within healthy bounds for the next mission.

## 7. Failure Handling

| Failure Mode | Indicator | Operator Action |
|---|---|---|
| Guard block | `blocked_by_guard` in mission status | Run `/diag`, identify which guard fired, wait for the rate window or reset manually |
| Mode block | `blocked_by_mode` in mission status | Investigate mission state with `/mission <id>`; escalate to engineering if unclear |
| Awaiting approval | `needs_operator` status and pending record in `approvals.db` | Follow the approval procedure in Section 4 |
| Consecutive failures `>= 3` | Mission auto-pauses | Run `/logs <id>` to find root cause, fix the upstream issue, then use `/retry <id>` |
| Repair failed | `repaired_failed` in logs | Review the failed verification rule and decide whether to retry or cancel |
| Backend unreachable | `/health` returns error | Do not approve or execute anything; alert engineering |
| Forbidden command attempted | Step blocked by `shell_guard.py` | Do not override; reject the approval and investigate why the plan generated the command |

**Escalation threshold:** If a mission has failed more than 2 times (`max_mission_failures_before_operator: 2`), an escalation record is automatically written to `operator_escalations.jsonl`. Review it before taking any further action.

## 8. Evidence and Artifacts

| Artifact | Location | Contents |
|---|---|---|
| Mission logs | `artifacts/logs/<mission_id>.log` | Timestamped execution events per task |
| Execution records | `engine_executions.jsonl` | Full execution history with payloads and results |
| Approval history | `approvals.db` | All approval requests, decisions, operator notes, timestamps |
| Escalation log | `operator_escalations.jsonl` | Escalation records with reason, recommended action, severity |
| Output files | `artifacts/output/` | Any file-write task outputs |
| Telegram chat history | Telegram app | Source user request and operator command log |

### 8.1 Minimum Evidence Required per Operator Action

- **Approval or rejection**
  - `operator_note` field populated in `approvals.db` with rationale

- **Retry or cancel**
  - Comment in `operator_escalations.jsonl` or a Telegram message noting the reason and mission ID

- **Failure investigation**
  - `/logs <id>` output reviewed and findings noted before closing the mission

> Do not delete or rotate artifacts while a mission is in `needs_operator`, `blocked`, or `running` state.
