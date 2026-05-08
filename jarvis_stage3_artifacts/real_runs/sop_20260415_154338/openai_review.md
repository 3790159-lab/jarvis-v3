## Review Notes
### General Suggestions
1. Consider adding a section for glossary terms to avoid confusion between jargon.
2. Include operator training requirements to ensure they understand the SOP.

### Specific Improvements
1. For Section 3, "Pre-Run Checks", consider removing item #4 (`check target path`) to reduce manual input burden on operators.
   Instead use a more streamlined way to validate paths under LLM guidance (if present) through `LLM_MODE` checking before `PathGuard`.
2. In Section 5 ("Execution Procedure") for the tool handler code, update the command description in item #6:
   ```markdown
### Tool Reference

| Tool | Description | Approval Required |
|---|---|---|
| `fs_list`, `git_status`, *and others like them in list* | [List specific commands] with minimal impact and visibility | No |

| run_python | Execute Python (includes scripts, extensions) | **Yes** |
| run_shell | Shell command execution via `shell_mode` enabled (`run_shell_cmd`) | Always necessary |
```
Add these for clarity to tool usage instructions.

3. Review Post-Run Validation #4 ("manually verify the affected path"):
   *   Provide a brief explanation of why manual verification is required beyond automated checks and include resources or best practices on how to properly inspect paths, especially in case of file system management rules requiring human observation.
   This ensures careful interpretation by operators concerning sensitive operations.

### Simplifying and Reducing Repetition
Simplify check duplicates:
Eliminate redundancy by referencing the appropriate approval record fields where possible instead of recalculating every time throughout SOP for consistency. Ensure any potential missteps are captured in the new workflow process.

Reduce duplication:
- Instead of listing common checks repeatedly mention these as part of each stage's pre-execution checks for safety and efficiency.

## Final Revised SOP
### 1. Purpose

Defines how operators handle real execution requests arriving through the Telegram bot interface of Jarvis V3 Supervisor, addressing full lifecycle needs including risk evaluation and conscious sign-off.

---

### 2. Preconditions

Before handling any operator request:

*   Verify `APP_HOST` and `TELEGRAM_BOT_TOKEN`.
*   Ensure chat ID is in `TELEGRM_ALLOWED_CHAT_ID`.
*   Set correct execution mode (`EXECUTION_MODE`).
*   Confirm database integrity (`DATABASE_PATH`).
*   Establish LLM backend reachability if required (`LLM_MODE`, `LLM_BASE_URL`).

---

### 3. Pre-Run Checks

1. **Verify source** â ensure chat ID approval.

2. **Read the full approval record**, including approved status, task details, and command execution risk level.
3. Determine if executing a command is required (`cmd_required`) based on initial checks (to reduce unnecessary steps for non-destruction tasks), or execute using `run_python`.

4. For destructive commands: Validate against blocked patterns (`BLOCKED_SHELL_PATTERNS`). If matches, request operator approval.

5. **Check for duplicate active tasks** to prevent task overrides.
6. **Confirm timeout**, using `TASK_DEFAULT_TIMEOUT_SECONDS`.

---

### 4. Approval and Risk Control

Manage command execution risks via the risk levels outlined in Section 5 ("Risk Levels"):

- Auto-approved for safety (`informational`, `standard`) commands
- Requiring operator approval for critical processes (`destructive`)

Approvers should only proceed if they understand the full operation's impact.

---

### 5. Execution Procedure

**Handling Request**

1. Validate execution mode and required permissions or tokens.
2. Confirm command authenticity before request dispatching.
3. Worker runs the task, utilizing a selected tool depending on `EXECUTION_MODE` & allowed parameters.
4. Upon task completion, monitor and report result directly in "Running task" Telegram updates.

### Pre-Handling Execution Step Checklists

```text
if destructive | | *executions not authorized|
    # Prompt for operator verification
    set_approval_status("rejected")
    break loop as the user cannot proceed

```

- Ensure task monitoring post-execution follows best practices and adheres to necessary communication protocols (post-run note update).

### 6. Post-Run Validation

1. Inspect completed tasks via Telegram or relevant logs.

2. Manual inspection of critical system management paths (`check manually verified`) is mandatory.

### 7. Failure Handling
If process fails: Verify potential error sources before retrying.

Check Telegram backend if offline to recover data (suspicious).

Restart the bot after failure and log data if required.

Review `results` for successful task outcome and implement proper documentation as outlined in evidence artifact sections to keep history of completed tasks within our scope for further training or debugging purposes.

---

### 8. Evidence and Artifacts

Keep records:

*   Approval (`status`) updates & corresponding execution details documented under approved status
*   Executed paths reviewed post-run according to system management rules and user verification procedures.
*   Telegram logs regarding successful mission completion

In regulated environments ensure adherence of these documentation requirements according to project policies.

This ensures compliance, helps training new operators, aids team growth through tracking data collection methods & provides operational insight necessary for smooth task execution and future process improvement initiatives.