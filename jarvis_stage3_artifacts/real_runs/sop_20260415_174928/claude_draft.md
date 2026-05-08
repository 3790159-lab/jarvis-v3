Please approve the file write to save the SOP to your project directory. In the meantime, here is the full document:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Audience:** Jarvis operators
**Scope:** Handling live task and agent invocation requests through the Telegram bot interface

---

## 1. Purpose

Define the standard procedure for operators to receive, evaluate, approve or reject, and audit Jarvis task requests delivered via the Telegram bot. This SOP ensures that no high-risk or ambiguous action is executed without deliberate human review.

---

## 2. Preconditions

- Telegram bot is running and reachable (`/health` returns `ok`)
- Operator's Telegram `chat_id` is registered and authenticated with the backend
- Jarvis supervisor backend is running and the SQLite approval store is accessible
- Operator has reviewed the risk policy table:

| Risk Level | Requires Approval | Notes |
|---|---|---|
| `low` | No | Auto-executed |
| `guarded` | Yes | HTTP, external AI, non-local URLs |
| `high_risk` | Yes | Shell commands, external CLI agents |
| `forbidden` | Blocked (HTTP 403) | Never executed |

- Operator understands that **approval is irreversible** â approved tasks execute immediately

---

## 3. Pre-run Checks

Before engaging with any incoming request:

1. Run `/health` in the Telegram chat and confirm the backend responds with a healthy status
2. Confirm the active execution mode (`safe` / `restricted` / `full`) matches the expected environment
   - `safe` blocks: shell, python, http tools
   - `restricted` blocks: shell only
3. Verify no orphaned pending approvals exist from a prior session
4. Confirm the mission or task context is known â do not approve requests with missing or unrecognized `mission_id`

---

## 4. Approval and Risk Control

**Receiving a request**

1. A request arrives via Telegram text â the bot forwards it to `/api/respond` on the backend
2. If the adapter or payload triggers `requires_approval = True`, a pending approval record is created in the SQLite `approvals` table
3. Operator is notified that a decision is required

**Evaluating the request**

4. Review these fields before deciding:
   - `risk_level` â guarded or high_risk
   - `summary` â human-readable description of the intended action
   - `payload_json` â the full action payload
   - `mission_id` / `step_id` â confirm this belongs to a known active mission

5. Decision rules:

| Condition | Action |
|---|---|
| Request is `forbidden` | Already blocked â no operator action needed |
| Payload contains unrecognized shell commands | **Reject** |
| URL target is external and unexpected | **Reject** |
| Mission ID is unknown or blank | **Reject** and investigate |
| Request matches expected mission plan | **Approve** with a note |
| Intent cannot be verified | **Reject** and ask for clarification |

6. Always add an `operator_note` â required for high-risk approvals and stored in the audit record

**Approving or rejecting**

7. Approve: `POST /api/approvals/{approval_id}/approve` with `operator_note`
8. Reject: `POST /api/approvals/{approval_id}/reject` with `operator_note`

---

## 5. Execution Procedure

1. After approval, confirm the task transitions from `pending` â `leased`
2. Monitor tool execution logs at `jarvis_stage3_artifacts/tool_runtime/logs/{tool}.log`
3. For multi-step missions, repeat the approval flow for each step that triggers `requires_approval`
4. Do not send additional Telegram messages while a step is executing â wait for a response first
5. For `external_cli_agent` or shell adapters, watch the log actively until completion

---

## 6. Post-run Validation

1. Confirm task status is `completed` (not `failed` or stuck in `leased`)
2. Check `mission_result.json` at `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`
3. Verify `artifact_manifest.json` lists all expected output files with correct sizes
4. Review tool logs for warnings or retried operations
5. Confirm no pending approvals remain open in the approval store
6. If the mission touched external systems (email, calendar, HTTP), verify the external state matches the expected outcome

---

## 7. Failure Handling

**Task fails during execution**

1. Check `error_text` in the task record via `GET /api/agents/tasks/{task_id}`
2. Check tool logs for the specific failure point
3. Determine if the error is retryable (connection/timeout/429/50x) or terminal (blocked pattern, forbidden tool)
4. For retryable errors: the system retries with exponential backoff (2^attempt seconds, 1 retry default) â wait before intervening
5. For terminal errors: reject any re-attempt, investigate root cause, escalate if needed

**Approval hangs**

6. If a step is blocked awaiting approval with no operator response, the mission will not progress â this is by design
7. Reject with a note explaining the timeout; re-initiate with a corrected payload if appropriate

**Bot becomes unresponsive**

8. Run `/health`; if unhealthy, restart the supervisor backend
9. Verify the bot polling loop is active
10. Do not retry previously approved requests until you confirm their execution status â duplicate execution is a real risk

**Unexpected `forbidden` block**

11. A `403` means the operation is in the `forbidden` tier â do not attempt to work around it
12. Escalate to the system owner to update the risk policy if the operation is legitimately needed

---

## 8. Evidence and Artifacts

All of the following must be present and retained after a completed mission:

| Artifact | Location | Contents |
|---|---|---|
| Approval records | SQLite `approvals` table | approval_id, risk_level, payload, operator_note, timestamps |
| Mission result | `missions/{mission_id}/mission_result.json` | Final output, status, step results |
| Artifact manifest | `missions/{mission_id}/artifact_manifest.json` | File list, sizes, paths |
| Tool execution logs | `tool_runtime/logs/{tool}.log` | Per-tool timestamped entries |
| Task records | Task marketplace (SQLite) | status, result_json, error_text, policy_reason |

**Retention rule:** Do not delete artifact directories until the mission is confirmed complete and `operator_note` has been written for each approval. These records are the primary audit trail for any post-incident review.

---

Once you approve the file write, I'll save this to `SOP_telegram_operator_workflow.md` in your project root. The SOP is grounded in the actual system â risk tiers from `risk_policies.py`, approval flow from `agent_control_plane.py` + `approval_store.py`, artifact paths from `artifact_registry.py`, and retry/failure logic from `tool_retry_executor.py`.