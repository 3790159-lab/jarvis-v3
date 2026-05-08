## Review Notes

*   Consider reorganizing sections for better flow, such as having an introduction before "Preconditions" or splitting the "Failure Handling" section into multiple smaller topics.
*   There may be redundant checks; verify if similar checks can be removed without compromising effectiveness.
*   Provide more context about what each risk level (`low`, `guarded`, `high_risk`) is and how they're used. This could involve including brief explanations in the SOP or referencing a separate document that delves into risk levels, threshold settings, and associated policies.
*   Consider rewording Section 4 ("Approval and Risk Control") for better clarity and concision; using an example might help illustrate how a decision is made regarding approval, rejection, etc. Ensure that only relevant checks are performed at each step.
*   Some sections (like post-run validation) cover broad topics such as output review, memory integrity, guard metrics reset, and more. Break these into smaller, actionable steps for better clarity on the expected sequence of tasks when running a mission completion process to find areas requiring immediate attention.
*   Consider having separate sections for "General Guidelines" or "Best Practices" at the top part that outlines additional advice like how often the bot should be run, which scenarios call for manual intervention in case of high error probabilities due to some particular patterns, the importance of maintaining all logs, how much data needs to be reviewed during each step of the process before making any further actions.

## Final Revised SOP

### General Guidelines
*   Ensure daily/periodic cleanings or health checks of the `SOP_Telegram_Operator_Workflow.md` file with specific focus areas that need more attention.
*   Manual intervention is strongly discouraged but may be necessary if certain thresholds are exceeded for longer durations than intended or other data points reveal a possible problem in the overall system stability.

### Version
Set to be continuously maintained as part of the core operations, aiming for at least one change every few months depending on growth and operational updates.

### Scope
Affects all operators managing live Jarvis missions via Telegram who have permission; changes may also affect some level of other teams due to dependencies in how the workflows operate together.

---

### **Purpose**

Define how the use of Telegram affects reviewing, approving (with associated judgments concerning allowed commands), monitoring & recovering or cancelling (to save resources) real-life tasks. Emphasize the necessity of human oversight before potential high-risk tasks start executing, and provide for an audit trail in case something undesirable happens.

---

### **Preconditions**

Before proceeding with any tasks:
1\. Confirm your Telegram account's chat ID has been registered in `ALLOWED_CHAT_IDS`.
2\. Make sure you have read access to necessary files.
3\. Understand the current settings & permissions that apply (check project config).

---

### **Pre-Run Checks**

Before approving or initiating actions, perform these actions:

1. Cross-check the request with what was requested, based on conversation in the chat.
2. Verify mission status via `/mission <id>`.
3. Compare risk level; make sure it is guarded, and not high_risk (the risks here could easily become real-life failures).
4. Check guard metrics:
   - Run `/diag` at some intervals & compare results against maximum allowed thresholds here (`shell_guard.py`)
5. Review payload before executing any external process: Ensure appropriate permissions and review declared intent; this helps ensure a safe outcome.
6. Verify commands, if required to execute anything like shell scripts; you must use the approved command list here.

---

### **Approval Procedure**

To decide on an action with approved or rejected status:

1. Get escalated alert & confirm message with any issues you're about to encounter
2. Review the issue via `/mission` and ensure you identify its blocked step's `approval_id`.
3. Access the required database record for this operation: Check for declared intent, check against the risk level (guard, normal), evaluate what it could be trying to do.
4. Decide whether:
   - To approve or reject depending on findings from above (`approve` if successful; `reject` otherwise).
   - Record your thought process with an operator note explaining why.
5. Save the decision as set.

> Never approve high-risk without reading it fully and, for any forbidden commands approved will never use them under any circumstances.

---

### **Execution Procedure**

1\. For ongoing missions:

*   Check the most recent statuses with `/missions`
*   Follow `/mission <id>` to see what tasks break down into the step-by-step current state
*   Inspect what happened at `[last log in /memory]` if a mission includes contextual multi-step actions

2\. Triggering the "run" process:

*   Check first that running status has set `planned` or `failed`
*   Use `/run <id>` for planned states and observe with `/logs`.
*   Do similarly for retrying but be sure to stop at each point if necessary.

3\. If any errors occur upon recovery, find the source of failure from logs, verify the threshold reached and follow instructions provided below for that specific error case.

---

### **Failure Handling**

| Error | Cause | Action |
| --- | --- | --- |
| Guard Block Triggered | High guard limit hit. Wait to run / diag if your console says so. | Continue monitoring; Run `/diag` after at least half of the threshold period has passed, or reset manually. |
| Process Exit due to Mode Misaligned | System cannot complete task as per policy & you might not be aware due to misaligned policy profiles. Investigate via `/Mission <id>` and fix accordingly | Escalate if unsure, use `supervisor_policy` or other guidance depending on project updates. |

> If there's more than 3 consecutive failures in a mission the system triggers for your attention.

### Post-run Validation

After each success run you must check:

*   Status is complete (`status=completed`)
*   Confirm output files are where they should be; ensure they make sense for what was asked to do during processing
*   Review `/logs [id]` for errors; report such findings in relevant logs or through the designated notice system (Telegram, Discord)
*   Log your final thoughts on mission run if you're an operator that handles these operations at scale

---

### **Evidence and Artifacts**
The below must be kept by each team in full to maintain audit trail.

#### **Required Before Any Changes:**

Log files, data records from the various operations in `approvals.db`.
Checkpoint logs for system integrity (e.g., `/logs/` or just `/memory`).

[Note]