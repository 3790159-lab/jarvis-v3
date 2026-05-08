# Jarvis Real Task Execution SOP

> Generated via real operator workflow.
> Mission ID: real_run_sop_5a0acac7
> Topic: Telegram workflow for handling real Jarvis operator requests

## Final SOP
**Review Notes**
===============

### Improvements

* Section 4's risk evaluation section is too long and may be confusing. Consider breaking it into smaller sections or providing an overview before diving into specific rules.
* The pre-run checks in Section 3 could benefit from a more descriptive title, as they cover multiple aspects of setup and configuration.
* There are several mentions of "the user" throughout the SOP. While this might need to be explained in context later on, it's essential now to avoid being too vague or conversational.
* Missing information about human factors for high-risk requests, how operators should handle escalated situations without direct access to supervisor tools.

### Simplifications and Clarifications

* Section 2 could benefit from clearer requirements language. What does "TELEGRAM_ALLOWED_CHAT_ID matches your chat" truly mean?
* Section 3's list format is sometimes confusing; consider more concise bullet points or separate sections for each check.
* Section 6 needs some reorganization; it currently jumps between task completion, file validation, mission step reviews, and worker status without clear headings.

### Standardization

* Consider referencing other related SOPs (e.g., Supervisor's workflow) when needed, to establish consistency across the platform's documentation.
* To further ensure efficiency in handling operator tasks, integrate the automated approval process into the core task processing flow where applicable.

**Final Revised SOP**
=====================

### 1. Purpose and Scope
Define the standard steps an operator follows when a real task arrives via Telegram, covering all stages from intake to completion, emphasizing consistent execution with safety gates in place for any skipped process.

### 2. Preconditions
The following conditions must be met before processing can occur:

| Condition | How to Verify          |
|-----------|--------------------------|
| Backend | Send `GET /health` in Telegram or check backend status directly    |
| Telegram | Ensure `/start` command works correctly and response comes with available commands   |
| Operator | Confirm match between `TELEGram_ALLOWED_CHAT_ID` and your chat ID via `/id`
| LLM Provider | Check `OLLAMA_ENABLED` / `OPENAI_COMPAT_ENABLED` with respective `.env` configuration; successful message send with a simple text confirms readiness    |
| Approval DB | Verify databaseâs existence at jarvis_stage3_artifacts/governance_runtime/approvals.db     |
| Policy Profile | Check that the profile is correctly set as "safe" under `.env` or confirm a deliberate policy shift.            |
| Worker Pool | At least two workers must be active (`WORKER_COUNT > 2`). Use task marketplace logs to validate.

### 3. Pre-run Checks
Ensure these conditions are met before processing:

1. **Policy Shell Status Confirmation**
    - **Warning**: If `Ploicy_Shell_ENABLED=false` in `.env`, shell commands will block during risk evaluation. Review this setting.
2. **Allowed Shell Prefix Verification**
    Check that your selected prefixes match what is allowed under `Poliay_allow_shell_prefixes` to avoid unexpected behavior.

### 4. Approval and Risk Control
Manage and control risks:

#### Risk Levels

| Level     | Trigger Conditions                 | Action Required |
|-----------|-------------------------------------|-----------------|
| Low       | Standard Chat, file reads within roots | No action        |
| Guarded   | External URL targets, AI capabilities | Pre-approval      |
| High Risk | CLAUDE code activation, external command | Pre-approval needed |
| Forbidden | Explicitly blocked operations          | Reject immediately |

#### Handling Approval
For an approval request (`requires_approval: true` in the response):

1. Read the summary returned (as in API response or logged to `approvals.db`). Understand what operation is involved.
2. Inspect the full payload (found under `payload_json` for reference). Confirm it matches the actual prompt requested.

...

 rest of your draft

### 5. Execution Process
Handle single and multi-step tasks, agent invocations, and task market requests with clear procedural steps.

#### Standard Conversational Request

1. Handle user message forwarded by Telegram to `POST /api/respond`.
2. Route request to a suitable tool or LLM provider based on the classified intent.
3. Return response to the sender in Telegram if positive; prompt clarification if indeterminate.
4. If ambiguous, do not re-enter payload blindly but instead ask for rephrasing.

#### Mission Request

1. Detect multi-step requirement and guide to matching `mission_templates.py` templates or create a 'MultiStepMissionRequest' object with necessary fields for unique execution tracking.
2. Send it through `POST /apixissions/multistep/execute` upon completion check that all required steps ended in success (`status: completed`) before forwarding the request details to the user.

#### Agent Invocation Request
1. Find suitable adapter based on request type and set dry-run status; confirm if successful.
2. Submit final submission with `dry_run: false` to process.

... rest of your draft

---

## Review Summary
OpenAI response did not match the exact two-section template, so the full response was used as the revised SOP.

---

## Artifact Paths
- Claude draft: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_131119\claude_draft.md
- OpenAI review: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_131119\openai_review.md
- Final SOP: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_131119\final_sop.md
- Package report: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_131119\package_report.txt