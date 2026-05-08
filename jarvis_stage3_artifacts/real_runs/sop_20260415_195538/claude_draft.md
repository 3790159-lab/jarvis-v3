The document is ready. Here it is in full:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0
**Applies to:** Jarvis V1.5 Supervisor â Telegram Interface
**Audience:** Operators submitting and monitoring requests via Telegram

---

## 1. Purpose

This procedure defines how operators submit, monitor, and close-out real requests to the Jarvis V1.5 Supervisor via its Telegram bot interface. It covers the full lifecycle: from pre-run checks through execution, validation, and artifact capture. Following this procedure minimises risk of uncontrolled execution, data loss, or runaway tasks.

---

## 2. Preconditions

Before sending any operational request, all of the following must be true:

| # | Condition | How to verify |
|---|---|---|
| 1 | Your Telegram `chat_id` is listed in `TELEGRAM_ALLOWED_CHAT_ID` | Send `/id` â if you get a reply, you're authorised |
| 2 | API server is running on port 8010 | `/health` returns a service-name and status |
| 3 | Worker pool is running | `/diag` shows `queue` and `workers` fields |
| 4 | Active policy profile is appropriate for the request | `/diag` shows `policy_profile` (safe / dev / admin) |
| 5 | `.env` `DEFAULT_PROJECT_ROOT` points to the correct target project | Confirm with the team lead if unsure |

If any condition is not met, **stop**. Do not submit the request until the environment is verified.

---

## 3. Pre-run Checks

Run these commands in order before every non-trivial request:

1. `/health` â confirm the backend returns a healthy status, not an error.
2. `/diag` â review:
   - `policy_profile` â should be `safe` for production work; `dev`/`admin` only when explicitly authorised.
   - `queue_size` â if > 10, wait for the backlog to drain before adding more tasks.
   - `shell_enabled` â must be `false` in production unless shell access is explicitly approved.
3. `/missions` â scan recent missions. If a previous mission for the same goal is still `planned` or `running`, do not submit a duplicate; use `/mission <id>` to check its state first.
4. `/tools` â confirm the required executor is listed.

> If `shell_enabled` is `true` and the policy profile is not `admin`, treat this as a misconfiguration and report it before proceeding.

---

## 4. Approval and Risk Control

### Policy profiles and their authorisation requirements

| Profile | Shell | Max retries | When allowed |
|---|---|---|---|
| `safe` | Disabled | 2 | Default â all production requests |
| `dev` | Limited (allow-listed prefixes only) | 3 | Development/staging only â team-lead approval |
| `admin` | Extended | 4 | Explicit operator-lead sign-off per session |

### Request risk tiers

| Tier | Examples | Required action |
|---|---|---|
| **Low** | Status checks, read-only file queries, capability questions | Proceed directly |
| **Medium** | Writing artifacts, creating/modifying project files | Confirm `.env` write roots are correct (`/diag`) |
| **High** | Shell execution, external HTTP calls, multi-step missions with unknowns | Get verbal or written sign-off from team lead; note in operator log |

### Approval for high-risk requests

1. Describe the intended request to the team lead.
2. Receive explicit go-ahead.
3. Note the approver name in your operator log (see Â§8).
4. Set policy profile to the minimum needed, only for the duration of that session.

---

## 5. Execution Procedure

### 5.1 Sending a request

1. Open your authorised Telegram chat with the Jarvis bot.
2. For command-based operations, use the appropriate slash command (`/gmail`, `/calendar`, `/health`, `/diag`, `/missions`, etc.).
3. For task/mission requests, send a free-text message in plain Russian or English. Be specific about the goal, target path, and expected output:
   ```
   ÐÑÐ¾Ð²ÐµÑÑ Python Ð²ÐµÑÑÐ¸Ñ Ð² Ð¿ÑÐ¾ÐµÐºÑÐµ Ð¸ Ð·Ð°Ð¿Ð¸ÑÐ¸ ÑÐµÐ·ÑÐ»ÑÑÐ°Ñ Ð² Ð°ÑÑÐµÑÐ°ÐºÑ
   ```
4. Keep the message under 2,200 characters (system truncates longer inputs).

### 5.2 Confirming mission creation

5. The bot should reply with a mission summary containing a `mission_id`:
   ```
   Ð¡Ð¾Ð·Ð´Ð°Ð½Ð° Ð¸ Ð¿Ð¾ÑÑÐ°Ð²Ð»ÐµÐ½Ð° Ð² Ð¾ÑÐµÑÐµÐ´Ñ long-autonomy Ð¼Ð¸ÑÑÐ¸Ñ.
   mission_id: mission_a1b2c3d4
   tasks: 5
   status: planned
   ```
6. Copy and save the `mission_id` â you need it for all follow-up commands.
7. If the bot replies with a direct answer and no `mission_id`, the request was handled as a chat/info response. No further monitoring needed.

### 5.3 Monitoring execution

8. Check mission progress:
   ```
   /mission mission_a1b2c3d4
   ```
   Expected status sequence: `planned` â `running` â `completed` (or `failed`).

9. For task-level detail:
   ```
   /logs mission_a1b2c3d4
   ```

10. If slower than expected, re-run `/diag` to check queue size and worker health.

### 5.4 Cancelling if needed

11. To stop an in-progress mission:
    ```
    /cancel mission_a1b2c3d4
    ```
    Tasks already `running` receive a signal but may complete their current step. Tasks still `planned` are dropped immediately.

---

## 6. Post-run Validation

1. Run `/mission <id>` â confirm overall status is `completed` with no tasks left in `running` or `planned`.
2. Run `/logs <id>` â every task should show `completed`. Investigate any `failed` or `skipped` entries.
3. If artifacts were expected: verify the output file exists in `artifacts/output/`, check it is within the size limit (12 KB safe / 18 KB dev / 24 KB admin), and spot-check the content.
4. Run `/memory <id>` â confirm the mission memory captured the intended goal and outcome summary.
5. Confirm the result matches the original request. If incomplete or incorrect, assess whether to retry (Â§7) or escalate.

---

## 7. Failure Handling

### Common failures and recovery actions

| Symptom | Likely cause | Action |
|---|---|---|
| "Backend ÑÐ»Ð¸ÑÐºÐ¾Ð¼ Ð´Ð¾Ð»Ð³Ð¾ Ð¾ÑÐ²ÐµÑÐ°ÐµÑ" | Backend timeout | Run `/health`; restart API server if unresponsive |
| "ÐÑÐ¸Ð±ÐºÐ° Ð·Ð°Ð¿ÑÐ¾ÑÐ° Ðº backend" | API server down | Restart `start_api.bat`; re-run `/health` |
| Mission stuck at `planned` | Worker pool not running | Restart `start_worker.bat`; check `/diag` |
| Task `failed`, others `skipped` | Dependency failure | Run `/logs <id>` for error detail; fix root cause; then `/retry <id>` |
| "artifact size exceeded" | Output too large | Reduce output scope or request a policy-profile change |
| LLM routes request incorrectly | Ollama unavailable or ambiguous input | Rephrase more specifically; check Ollama service |
| "Ð­ÑÐ¾Ñ chat id Ð½Ðµ ÑÐ°Ð·ÑÐµÑÑÐ½" | Chat ID not in allow-list | Add ID to `TELEGRAM_ALLOWED_CHAT_ID` in `.env`; restart bot |

### Retry procedure

1. Identify the failed task via `/logs <id>`.
2. Determine whether the failure is transient (timeout, connectivity) or structural (bad input, policy block).
3. For transient failures: `/retry mission_a1b2c3d4`
4. For structural failures: fix the root cause, then resubmit as a new request â do not retry indefinitely.
5. After two consecutive retries with no improvement, escalate to team lead with the `mission_id` and `/logs` output.

---

## 8. Evidence and Artifacts

Record the following for every non-trivial or elevated-risk request:

| Item | Where to capture |
|---|---|
| Request text sent | Operator log / Telegram message history |
| `mission_id` returned | Operator log |
| Approver name (for medium/high-risk) | Operator log |
| Final mission status from `/mission <id>` | Screenshot or copy-paste |
| `/logs <id>` output | Screenshot or copy-paste |
| Artifact file path and spot-check result | Operator log |
| Any retry events and outcomes | Operator log |

**Minimum log entry format:**

```
Date       : 2026-04-15
Operator   : <your name>
Request    : <request text>
mission_id : mission_a1b2c3d4
Approver   : <name or "self â low risk">
Outcome    : completed / failed
Artifacts  : artifacts/output/mission_note.txt (checked: OK)
Notes      : <anomalies or retries>
```

Retain logs for at least 30 days or per team policy. Preserve file artifacts alongside their log entry.

---

Would you like me to save this as `SOP_Telegram_Operator_Workflow.md` in the project directory, or adjust any section?