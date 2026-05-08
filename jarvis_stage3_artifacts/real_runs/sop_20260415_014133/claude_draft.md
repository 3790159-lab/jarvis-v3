The file write needs your permission. You can approve it, or I can just output the document here so you can save it yourself.

Here's the full SOP:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version**: 1.0
**System**: Jarvis V3 Supervisor (supervisor_v1_5_smart_telegram)
**Audience**: Operators running live requests via Telegram

---

## 1. Purpose

This SOP defines the standard procedure for receiving, assessing, approving, executing, and validating real requests sent through the Telegram bot to the Jarvis V3 Supervisor backend. It ensures that every operator action is deliberate, audited, and recoverable.

---

## 2. Preconditions

Before handling any real request, confirm the following are in place:

- [ ] Telegram bot is running (`telegram_bot.py` active, polling Telegram API)
- [ ] Backend is live at the configured port (`APP_PORT`, default `8010`)
- [ ] `TELEGRAM_ALLOWED_CHAT_ID` is set â only your chat ID has access
- [ ] `SUPERVISOR_POLICY_PROFILE` is known and appropriate (`safe` / `dev` / `admin`)
- [ ] `JARVIS_EXECUTION_MODE` is confirmed (`hybrid`, `real`, or `dry_run`)
- [ ] Secrets are set in `.env`: `TELEGRAM_BOT_TOKEN`, any `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in use
- [ ] Artifact and log directories exist under `jarvis_stage3_artifacts/`

---

## 3. Pre-Run Checks

### 3.1 Verify Backend Health

Send in Telegram:
```
/health
```
Expected: backend responds with status `ok`. If unhealthy, do not proceed â check `logs/jarvis_backend_stderr.log`.

### 3.2 Check Active Missions

```
/missions
```
Review currently running or stalled missions. Do not issue a new request that conflicts with in-progress work. Note any missions in `stale` or `failed` status before continuing.

### 3.3 Check Pending Approvals

```
GET /api/approvals?status=pending
```
If approvals are pending from a prior session, resolve them first. Unresolved approvals block dependent task execution.

### 3.4 Confirm Policy Profile

```
/diag
```
Confirm `policy.shell_enabled`, `policy.allow_shell_prefixes`, and `policy.max_retries` match expectations for the task. If not, update `.env` and restart the backend.

---

## 4. Approval and Risk Control

### 4.1 Risk Levels

| Level | Requires Approval | Trigger Examples |
|---|---|---|
| `low` | No | Local echo adapter, safe read-only tools |
| `guarded` | Yes | External HTTP calls, `openai_compatible_http` adapter, external AI capability |
| `high_risk` | Yes | `claude_code_bridge` adapter, `shell` capability or tool, `command` field in payload |
| `forbidden` | Blocked outright | Hard-coded rejection rules |

### 4.2 Reviewing an Approval Request

1. Query pending approvals: `GET /api/approvals?status=pending`
2. For each item, examine:
   - `summary` â plain-language description of what will execute
   - `risk_level` â `guarded` or `high_risk`
   - `payload_json` â exact parameters passed to the adapter
   - `mission_id` / `step_id` â which step triggered the request
3. Ask: Is the payload what you intended? Is the adapter appropriate? Are shell or external calls expected here?

### 4.3 Approving or Rejecting

**Approve:**
```
POST /api/approvals/{approval_id}/approve
{ "operator_note": "Reviewed payload; shell command is whitelisted echo. Approved." }
```

**Reject:**
```
POST /api/approvals/{approval_id}/reject
{ "operator_note": "Unexpected shell command detected. Rejecting pending investigation." }
```

> Always provide a meaningful `operator_note` â it is the primary audit record for the decision.

---

## 5. Execution Procedure

### 5.1 Sending a Request

1. Open your authorized Telegram chat with the bot.
2. Send the request in natural language or via a slash command.
   - Natural language is classified by `llm_router.py` and dispatched automatically.
   - Slash commands bypass routing and go directly to the command parser.
3. Confirm the bot acknowledges (typing indicator, then reply).

### 5.2 Tracking the Mission

Once a mission is created you receive a `mission_id` in the reply.

1. Get full details: `/mission <mission_id>`
2. Stream task results: `/logs <mission_id>`
3. If a step is waiting on an approval, handle it per Section 4 before execution can continue.

### 5.3 Key Slash Commands

| Command | When to Use |
|---|---|
| `/missions` | Overview of all recent missions |
| `/mission <id>` | Detailed step-level status |
| `/logs <id>` | Task-by-task execution results |
| `/retry <id>` | Re-queue failed tasks |
| `/cancel <id>` | Cancel all queued/running tasks |
| `/run <id>` | Resume or restart a stalled mission |
| `/memory <id>` | Inspect mission memory events |
| `/context <id>` | Get the resume bundle for interrupted sessions |
| `/compress <id>` | Compress mission context |
| `/diag` | Full system diagnostics snapshot |
| `/tools` | List available tools and their safety flags |

### 5.4 Google Workspace Operations

1. Use `/gmail` to inspect recent emails before issuing follow-up requests.
2. Use `/calendar` to verify upcoming events before creating new ones.
3. Send create/send requests in natural language; Jarvis routes them to `/api/google/execute`.
4. Confirm the response includes the created event ID or message thread ID.

---

## 6. Post-Run Validation

After a mission reaches `completed`:

1. **Review task results:** `/logs <mission_id>` â all tasks should show `status: completed`.
2. **Verify the artifact:** Check `jarvis_stage3_artifacts/artifact_runtime/` for `role: output` entries in the manifest.
3. **Confirm memory ingestion:** `/memory <mission_id>` â verify the result is in semantic memory.
4. **Snapshot integrity:** Open `resume_runtime/snapshots/<mission_id>.json` â confirm `status: completed`, `error: null`, and a recent `updated_at`.
5. **Check approval audit:** `GET /api/approvals?status=approved` â confirm all approvals for this mission have your `operator_note`.

---

## 7. Failure Handling

### 7.1 Mission Fails Mid-Execution

1. `/logs <mission_id>` â identify which task failed and the error.
2. Transient error (network, LLM unavailable): `/retry <mission_id>`
3. Policy or config error: fix `.env`, then `/run <mission_id>`
4. Unrecoverable: `/cancel <mission_id>` â document the failure reason first.

### 7.2 Approval Blocked

1. `GET /api/approvals/{approval_id}` â understand what triggered it.
2. If trigger is unexpected (wrong adapter, unintended shell command): reject and revise the request.
3. If trigger is legitimate: approve with a clear note.

### 7.3 Bot Not Responding

1. `/health` â no reply in 15 s means the backend may be down.
2. Check `logs/jarvis_backend_stderr.log` for crash output.
3. Restart the backend. Before resubmitting, verify no stale lock exists at `resume_runtime/locks/<mission_id>.lock`.

### 7.4 Stale Mission Lock

1. Confirm lock file exists: `resume_runtime/locks/<mission_id>.lock`
2. Verify no active worker holds it.
3. Delete the lock file only if no process is running.
4. `/run <mission_id>` to resume.

### 7.5 LLM Routing Misclassification

1. Use an explicit slash command instead of natural language.
2. If `LLM_MODE=offline`, the rule-based classifier is active â its routing coverage is narrower.
3. For structured tasks, POST directly to `/api/respond` with a precise `text` field.

---

## 8. Evidence and Artifacts

Confirm these artifacts exist after every real operator session:

| Artifact | Location | Purpose |
|---|---|---|
| Mission event log | `resume_runtime/logs/<mission_id>.log` | Timestamped step-by-step execution trace |
| Mission snapshot | `resume_runtime/snapshots/<mission_id>.json` | Final mission state (status, steps, errors) |
| Approval records | `governance_runtime/approvals.db` | Full approval audit trail with operator notes |
| Task results | `agent_runtime/task_marketplace.db` | Per-task status, attempt count, result |
| Semantic memory | `memory_runtime/semantic_memory.db` | Ingested mission summaries for future recall |
| Output artifacts | `artifact_runtime/` | Files, reports, or data generated |
| Obsidian note | `memory_runtime/obsidian_notes/<mission_id>.md` | Human-readable markdown summary |
| Application log | `logs/jarvis.log` | Backend-level events and errors |

**Retention guidance:** Do not delete mission logs or snapshots until a review is complete. Archive `approvals.db` entries for any `high_risk` approval separately. If a mission produced sensitive output (emails sent, files written), note the artifact path in your incident record.

---

The document is grounded in the actual codebase â every command, path, API endpoint, risk level, and artifact location was pulled directly from the source files. Let me know if you want any section expanded or adjusted, or if you'd like me to save the file.