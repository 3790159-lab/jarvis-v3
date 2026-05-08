## Review Notes
### Improvements:
- Add links to existing documentation or relevant sections in the SOP.
- Merge similar commands into `/tools` list.
- Expand on failure handling steps, especially for `task failed, retries exhausted`.
- Simplify section 8 'Evidence and Artifacts' by highlighting key retention metrics and possible storage formats (JSONL databases)
### Minor Clarifications:
- "Policy profile context" definition in the # Scope Section. Provide a brief summary of policy profile and its associated risks.
- Use consistent verb tenses throughout SOPs to maintain operational clarity.

## Final Revised SOP

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Scope:** Operators interacting with the Jarvis Supervisor system via Telegram
*Overview:* This Standard Operating Procedure (SOP) outlines a clear process for operators to communicate with and interact with the Jarvis Supervisor through the Telegram interface.
**Policy Profile Context**

The policy profile defines access permissions, risks associated with each permission level (i.e., developer (`dev`), safe). Access levels must be in compliance or under specific exception.

---

## 1. Purpose

This SOP aims to define end-to-end procedures for an authorized operator submitting and validating Jarvis task requests via the Telegram interface ensuring safety by assessing risk before execution verifying outcomes, as well as securely handling failures â reducing runaway tasks or lost execution evidence risks.

---

## 2. Preconditions

Before sending any operational request confirm all:
| Condition | # | How to Verify |
|-----------|---|---------------|
| Listed in `ALLOWED_CHAT_ID` | 1 | Send `/id` to Jarvis bot; match the returned ID in config |
| Backend Availability | 2 | Send `/health`; receive '200 OK' response and status details |
| Currently Active Policy Profile (`safe`/`dev`/`admin`) | 3 | Send '/diag'; read policy in the response |
| Understanding Shell Execution Requirements | 4 | `shell_enabled` is false in safe; use dev\admin for shell tasks |
| Availability of Dependencies (Gmail, Calendar, etc.) | 5| Verify with `/gmail` or `\calendar` for Google; `/diag` shows LLM status |

---

## 3. Pre-Run Checks

Before submitting a request creating a mission or task:

1. **Check existing missions in progress** (`/missions`); do not submit duplicate if similar is already `planned` or `running`.
2. Check the Task queue; system underload (`worker count at maximum by /diag`) â consider waiting or reducing request scope.
3. **Assess available tools** (`/tools`); confirm required task type (e.g., file_write, shell_integration) is available.
   - Shell commands require `dev` or `admin` policy profile
   - External HTTP calls flagged as guarded; may need approval
4. For high-impact requests assess if policy permits:
  ```python
# High_Risk Tasks
Claude-Bridge or external Agent tasks are classified high-risk
```

---

## 4. Approval and Risk Control

Jarvis classifies missions by risk level.
Operator must know when manual approval gate triggers.

### Risk levels

| Level | Triggers           | Default behavior |
|-------|---------------------|-------------------|
| `low`   | Analysis, File Writes | Executes automatically        |
| `guarded`  | External URLs, AI reqs | Queue; may require approval |
| `high_risk` | Shell Commands, CLA bridge | Blocked until approved      |
| `forbidden` | Explicitly prohibited | Rejected immediately        |

### Approval procedure

1. If a task is flagged **requires_approval** no execute until explicitly approved via `/governance_runtime/approvals.db` store.
2. To approve: set `status = 'approved'; operator_note; documenting the reason`.
3. To deny: Set `status = "denied";` task will not re-run and may be marked failed.
4. *Never approve high-risk tasks without reading `[payload_json]`.* 5. If unsure deny & resubmit a scope-down version of request.

---

## 5. Execution Procedure

### Submitting Standard Request
1. Open Telegram chat with Jarvis bot
2. Send a natural-language message describing the objective.
3. The bot forwards your message to `/api/respond`.
4. Observe Response:
   - A mission reply confirms a mission created (`mission_id`)
   - Chat reply means LLM is down route to chat instead of mission, send rephrased action-oriented language
5. Note `mission_id`. Needed for all follow-up commands.

### Using Explicit Commands

| Command | Function     |
|---------|---------------|
| `/missions` | Mission statuses         |
| `/mission <id>` | Detailed missions status |
| `/logs <id>` | mission task results      |
| `/memory <id>` | Full event logs            |
| `/context <id>` | Resume context summary & events     |
| `/retry <id>`   | Requeue failed tasks        |
| `/cancel <id>`  | Cancel queued and running |
| `/diag`       | System diagnostics; policy LLM status, stats    |
| `/tools`      | Registered task executor types     |

### Monitoring Running Mission

1. Send `/mission ` periodically; watch for statuses transitions (`planned` `running`, completed/failed).
2. Stale tasks recover after 20 seconds workers reheartbeat.
3. If stalled in running >60 second, inspect /state/active_run_store.json for failures / deadlocks

---

## 6. Post-Run Validation

- **Read Task Results**: Send `/logs <id>` confirm task output completed with expected result (`status:** completed`)
- Silent failures: mission shows completed even individual tasks failed or exhaustion
  a confirm no unexpected âtask_failedâ events midrun.
- **Verify Artifacts Output** (when shell required): confirm actual match of anticipated result
- Review Mission Memory:
  *Send `/memory` <id>* for full event sequence
  *Task logs (`/logs`) to see if something amiss occurs

---

## 7. Handling Failures

### Task Failed Exhausted retries

1. Send `/logs & read `last_error`. Identify Cause
2., Symptom Causes Solutions: [Common / Recovery Steps / Misconceptions]
3., Once Root Issue Fixed, send `/retries `<id>>`
4. If Mission CANNOT be Salvaged, Cancel and NEW

### Mission Stuck in âRunningâ

1. Inspect Worker status; active timestamps
2., Recover After 20 seconds.
 - If Still Stalled Send `/diag`.