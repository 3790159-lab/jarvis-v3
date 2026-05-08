The file write needs your approval â please grant it. Once you do I'll save the document. Here's the full SOP for review in the meantime:

---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

**Version:** 1.0 | **System:** Jarvis V3 Supervisor (v1.5) | **Audience:** Operators

---

## 1. Purpose

Defines how operators handle real execution requests arriving through the Telegram bot interface of Jarvis V3 Supervisor. Covers the full lifecycle: receiving a request, evaluating risk, granting or denying approval, overseeing execution, and confirming results. The goal is to ensure risky or destructive operations are never executed without conscious operator sign-off.

---

## 2. Preconditions

Before handling any operator request, confirm all of the following:

- [ ] Backend is running (`APP_HOST:APP_PORT`, default `127.0.0.1:8010`)
- [ ] `TELEGRAM_BOT_TOKEN` is set and the bot is live (test with `/health` in chat)
- [ ] Your chat ID matches `TELEGRAM_ALLOWED_CHAT_ID` (if restriction is enabled)
- [ ] `EXECUTION_MODE` is set correctly: `safe` (default), `dry_run` (testing), or `real` (full execution)
- [ ] `DATABASE_PATH` points to a reachable SQLite file
- [ ] `POLICY_SHELL_ENABLED=true` only if shell execution is intentionally needed
- [ ] LLM backend is reachable if the request requires classification (`LLM_MODE`, `LLM_BASE_URL`)

---

## 3. Pre-Run Checks

1. **Verify the source** â confirm `chat_id` matches the authorized operator chat.
2. **Read the full approval record** â retrieve `approval_id`, `summary`, `risk_level`, `payload_json` from the DB.
3. **Inspect the command** against destructive tokens: `del`, `rm`, `rmdir`, `format`, `shutdown`, `git reset --hard`, plus anything in `BLOCKED_SHELL_PATTERNS`. Any match means `approval_required: true`.
4. **Check the target path** â must be within `DEFAULT_PROJECT_ROOT` or `PathGuard`-allowed roots. Reject if outside.
5. **Check for duplicate active tasks** â if a task for the same mission is already `running`, do not issue another approval.
6. **Confirm timeout** â `TASK_DEFAULT_TIMEOUT_SECONDS` (default 60s). Raise before approval for long-running commands.

---

## 4. Approval and Risk Control

### Risk Levels

| Level | Trigger | Default |
|---|---|---|
| `informational` | File reads, `fs_list`, `git_status` | Auto-approved |
| `standard` | `run_python`, `fs_search` | Auto-approved (safe mode) |
| `destructive` | Any shell command with destructive tokens | **Operator approval required** |

### Approval Steps

1. Retrieve the pending record (`status = pending`) from the `approvals` table.
2. Read `summary` and `payload_json` â this is the exact operation that will execute.
3. Choose one action:
   - **Approve:** `set_approval_status(approval_id, "approved", operator_note="<reason>")`
   - **Reject:** `set_approval_status(approval_id, "rejected", operator_note="<reason>")`
   - **Escalate:** reject with note `"escalated â pending review"`, consult before re-running.
4. Never approve without reading `payload_json`. Approvals are single-use; each re-run needs a new record.

---

## 5. Execution Procedure

1. **Worker picks up the task** â polls every 2 seconds; after approval, task transitions `queued` â `running`.
2. **Tool is dispatched** â `WorkerRunner` calls the handler (`run_shell`, `run_python`, `fs_list`, etc.) with `approval_granted=True`.
3. **Monitor status** in the `tasks` DB table or Telegram chat reply:
   - `running` â in progress
   - `succeeded` â completed
   - `failed` â check the `error` field
4. Do not interrupt a running task unless it has clearly hung past its timeout.
5. Shell commands execute in the context of `DEFAULT_PROJECT_ROOT` â confirm before approval.

### Tool Reference

| Tool | Description | Approval Required |
|---|---|---|
| `fs_list` | List directory | No |
| `fs_read` | Read file | No |
| `fs_search` | Grep files | No |
| `git_status` | Repo status | No |
| `run_python` | Execute Python | No (safe mode) |
| `run_shell` | Shell command | **Yes, always** |

---

## 6. Post-Run Validation

1. Read `result_json` in the `tasks` table â confirm output matches the expected outcome.
2. For destructive commands, verify the affected path manually (e.g., run `git_status` or `fs_list`).
3. Confirm the Telegram bot reply matches `result_json` (not stale/cached).
4. If part of a multi-step mission, confirm `ConversationOrchestrator` stored the result for follow-up context.
5. Add a closing `operator_note` to the approval record: e.g., `"completed â output verified"`.

---

## 7. Failure Handling

**Task fails immediately:**
1. Check `error` field in `tasks` table.
2. Common causes: path outside allowed roots (`PathGuard`), command in blocklist, LLM unavailable, approval not granted before dispatch.
3. Fix the root cause before retrying.

**Task hangs:**
1. Let `TASK_DEFAULT_TIMEOUT_SECONDS` elapse â worker marks it `failed` automatically.
2. If process does not terminate: stop the worker, investigate the subprocess, restart cleanly.
3. Never delete a `running` task record â set it to `failed` via the DB and document in `operator_note`.

**Bot stops responding:**
1. `GET http://APP_HOST:APP_PORT/health` â should return `{"status": "ok", "version": "Jarvis V3 Supervisor"}`.
2. Check the Telegram bot process (`TELEGRAM_BACKEND_URL` config).
3. Restart bot only after confirming no tasks are `running`.

**Wrong result / wrong route:**
1. Check `route` field in the API response to see which tool was dispatched.
2. Rephrase the request with more specific keywords.
3. Note the phrase that triggered the wrong `task_hint` for future config tuning.

---

## 8. Evidence and Artifacts

For any request involving `approval_required: true`, retain:

| Artifact | Location | Retention |
|---|---|---|
| Approval record | `approvals` table (`DATABASE_PATH`) | Permanent |
| Task record + result | `tasks` table, `result_json` field | Permanent |
| Operator note | `operator_note` in `approvals` table | Permanent |
| Telegram message log | Telegram chat history | Per org policy |

Before closing any destructive operation, record: `approval_id`, `mission_id`, exact command from `payload_json`, `result_json` output, and whether post-run validation passed. For regulated environments, export the relevant rows as JSON/CSV and store outside the runtime DB.

---

Once you grant write permission, this will be saved as `SOP_telegram_operator_requests.md` in the project root.