I need your permission to write the file. Once you approve, the SOP will be saved to `SOP_Telegram_Operator_Requests.md` in your project root.

In the meantime, here is the full document:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Scope:** Operators interacting with the Jarvis Supervisor system via Telegram
**Policy profile context:** `safe` (default) | `dev` | `admin`

---

## 1. Purpose

This SOP defines the end-to-end procedure for an authorized operator to submit, monitor, and validate real task or mission requests through the Jarvis Telegram interface. It covers how to safely interact with the supervisor, assess risk before execution, confirm outcomes, and handle failures â reducing the chance of runaway tasks, unreviewed high-risk actions, or lost execution evidence.

---

## 2. Preconditions

Before sending any operational request, confirm all of the following are true:

| # | Condition | How to verify |
|---|-----------|---------------|
| 1 | Your Telegram account's `chat_id` is listed in `ALLOWED_CHAT_ID` | Send `/id` to the bot; confirm the returned ID matches config |
| 2 | The backend service is reachable | Send `/health`; expect `200 OK` with status details |
| 3 | You know the current active policy profile (`safe` / `dev` / `admin`) | Send `/diag`; read `policy` field in the response |
| 4 | Shell execution requirements are understood | `shell_enabled` is `false` in `safe` profile; use `dev`/`admin` for shell tasks |
| 5 | Any dependent external services (Gmail, Calendar, Ollama) are available | Verify with `/gmail` or `/calendar` for Google; `/diag` shows LLM status |

---

## 3. Pre-Run Checks

Before submitting a request that will create a mission or task:

1. **Check for existing missions in progress.** Send `/missions` and review statuses. Do not submit a duplicate request if a similar mission is already `planned` or `running`.
2. **Check the task queue.** If the system is under load (worker count at maximum per `/diag`), consider waiting or reducing the scope of the new request.
3. **Assess available tools.** Send `/tools` to confirm the required task type (e.g., `file_write`, `shell`, `integration`) is available under the current policy profile.
4. **For high-impact requests**, confirm the policy profile permits the intended action:
   - Shell commands â requires `dev` or `admin` profile
   - External HTTP calls â flagged as `guarded`, may require approval
   - Claude-bridge or external agent tasks â classified `high_risk`

---

## 4. Approval and Risk Control

Jarvis automatically classifies tasks by risk level. Operators must understand when a manual approval gate is triggered.

### Risk levels

| Level | Triggers | Default behavior |
|-------|----------|-----------------|
| `low` | Analysis, file writes, planning tasks | Executes automatically |
| `guarded` | External URLs, external AI capability requirements | Queued; may require approval |
| `high_risk` | Shell commands, Claude-bridge adapter, CLI agents | Blocked until approved |
| `forbidden` | Explicitly prohibited actions | Rejected immediately |

### Approval procedure

1. If a task is flagged `requires_approval`, it will not execute until explicitly approved via the approval store (`governance_runtime/approvals.db`).
2. To approve: set `status = 'approved'` with an `operator_note` documenting the reason.
3. To deny: set `status = 'denied'`; the task will not retry and the mission may be marked `failed`.
4. **Never approve high-risk tasks without reading the `payload_json` field** to confirm the exact command or action.
5. If uncertain, deny and resubmit a scoped-down version of the request.

---

## 5. Execution Procedure

### Submitting a standard request

1. Open the Telegram chat with the Jarvis bot.
2. Send a natural-language message describing the objective. Be specific:
   - **Good:** `"Analyze the latest sales CSV in artifacts/output and write a summary report"`
   - **Avoid:** `"Do the thing we talked about"` â ambiguous; may route to chat instead of mission
3. The bot forwards your message to `/api/respond`.
4. Observe the response:
   - A **mission reply** confirms a mission was created (includes `mission_id`).
   - A **chat reply** means the router classified it as a question â rephrase with action-oriented language if a task was intended.
5. Note the `mission_id` returned. You will need it for all follow-up commands.

### Using explicit commands

| Command | Purpose |
|---------|---------|
| `/missions` | List all missions with current status |
| `/mission <id>` | Detailed status of a specific mission |
| `/logs <id>` | Last 20 task execution results for a mission |
| `/memory <id>` | Full event log for a mission |
| `/context <id>` | Resume context (summaries + recent events) |
| `/retry <id>` | Requeue all failed tasks in a mission |
| `/cancel <id>` | Cancel all queued and running tasks |
| `/diag` | System diagnostics: policy, LLM status, stats |
| `/tools` | List registered task executor types |

### Monitoring a running mission

1. Send `/mission <id>` periodically; watch for status transitions: `planned` â `running` â `completed` / `failed`.
2. If the mission stalls in `running` for more than 2â3 minutes, send `/logs <id>` to check for recurring task failures or dependency deadlocks.
3. The worker heartbeat timeout is 15 seconds; stale tasks are auto-recovered within 20 seconds. If recovery does not occur, proceed to section 7.

---

## 6. Post-Run Validation

After a mission reaches `completed` status:

1. **Read task results.** Send `/logs <id>` and confirm each task shows `status: completed` with expected output.
2. **Check for silent failures.** A mission may show `completed` even if individual tasks failed and exhausted retries. Inspect `/logs <id>` for any `status: failed` entries.
3. **Verify artifacts.** File-write tasks output to `artifacts/output/`. Confirm the expected file exists and is not truncated (`[truncated by policy]` suffix indicates oversized output).
4. **Review mission memory.** Send `/memory <id>` to review the full event sequence. Confirm no unexpected `task_failed` events occurred mid-run.
5. **For missions using shell or external calls**, confirm that the actual output matches the intended scope â no side effects beyond what was requested.

---

## 7. Failure Handling

### Task failed, retries exhausted

1. Send `/logs <id>` and read the `last_error` field to identify the root cause.
2. Common causes and resolutions:

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| `shell_enabled = false` error | Policy profile blocks shell | Switch to `dev`/`admin` profile or rephrase without shell |
| LLM timeout / fallback mode | Ollama is down | Restart Ollama, then `/retry <id>` |
| `[truncated by policy]` in output | File size exceeded policy max | Split into smaller scoped tasks |
| Dependency task stuck in `queued` | Prerequisite task failed | Fix root task first, then `/retry <id>` |

3. After resolving root cause, send `/retry <id>` to requeue failed tasks.
4. If the mission cannot be salvaged, send `/cancel <id>` and create a new mission.

### Mission stuck in `running`

1. Send `/diag` and check worker status and active run timestamps.
2. The continuity system auto-recovers stale runs after 20 seconds with no heartbeat. Wait one full minute before intervening.
3. If still stuck: inspect `state/active_run_store.json` for stale snapshots and restart the worker process if needed.

### Backend unreachable from Telegram

1. The bot automatically retries 2â4 times with exponential backoff. Do not resubmit during this window.
2. Send `/health` separately to confirm backend status.
3. Once backend recovers, resubmit only if the original request did not produce a mission.

---

## 8. Evidence and Artifacts

All operator actions and system events are automatically logged.

### What is automatically recorded

| Evidence type | Location | Retention |
|---------------|----------|-----------|
| Mission definitions and task specs | `state/missions.json` | Permanent |
| Task execution results and errors | `state/missions.json` â `task_results[]` | Per-mission |
| Full event timeline per mission | `state/mission_memory.json` | Permanent; compressed after 20 events |
| Task queue history | `state/task_queue.json` | Per queue entry |
| Worker execution state | `state/worker_state.json` | Last run only |
| Approval records with operator notes | `governance_runtime/approvals.db` | Permanent |
| File output artifacts | `artifacts/output/` | Permanent |

### Post-incident evidence retrieval

1. Use `/memory <id>` to export the full event log for a mission.
2. Use `/logs <id>` to retrieve the last 20 task results.
3. For approval audit trail: query `approvals.db` by `mission_id` â each record includes `operator_note` and decision timestamp.
4. If auto-compression ran, use `/context <id>` to retrieve summarized history alongside recent raw events.

### Operator note discipline

When approving or denying high-risk tasks, write a meaningful `operator_note`.

- **Good:** `"Approved: verified output path is local artifacts/ only, no external calls"`
- **Insufficient:** `"ok"` or `"yes"` â these do not constitute adequate audit evidence.

---

The document has been written and is ready for approval to save. Let me know if you'd like to adjust any section or if you approve saving it to the file.