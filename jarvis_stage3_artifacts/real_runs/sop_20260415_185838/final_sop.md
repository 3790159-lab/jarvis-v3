# Jarvis Real Task Execution SOP

> Generated via real operator workflow.
> Mission ID: real_run_sop_865c12e7
> Topic: Telegram workflow for handling real Jarvis operator requests

## Final SOP
### Purpose
The purpose of this SOP is to define the workflow for receiving and processing real Jarvis operator requests via Telegram.

### Preconditions
To execute this SOP, the following environment variables must be set:
- `TELEGRAM_ALLOWED_CHAT_ID`: The ID of the allowed chat forTelegram workflow
- `SUPERVISOR_POLICY_PROFILE`: The approved supervisor policy profile for autonomous limited mode
- `OLLAMA_ENABLED`: Whether Ollama is enabled

### Pre-run checks
The following pre-run checks will be performed:
1. `/health` command to check the health of the Jarvis operator
2. `/diag` command to perform a diagnostic check
3. Checking the state files for any updates or required tasks

### Approval and risk control
Approval and risk assessment are managed through the `autonomy/modes.py` file, including:
- `safe_manual`
- `operator_assisted`
- `autonomous_limited`

However, please note that there is a known gap regarding approval resolution in version 1.5; manual intervention might be required for certain flows.

### Execution procedure
The following separate procedures will be executed based on the request type:
1. **Slash commands:** Will follow a standard format.
2. **NL missions:** Map to `task_type` as "missions"
3. **Shell tasks:** Map to `task_type` as "shell"
4. **Google integrations:** Follow an actual integration process

Each procedure will be managed through the task management system.

### Post-run validation
The post-run validation includes the following checks:
1. `/logs`: Verify that the logs are correctly written and properly cleaned up.
2. `/context`: Confirm the context values have been set according to the required policies.
3. `artifacts/output/`: Check for successful completion of any output file operations, if applicable.
4. Escalation log check: The final task result will be stored as part of this process.

Any failures here will trigger a review of the logs, task inputs, and context values.

### Failure handling
This SOP outlines the following failure types:
1. **Retry backoff**: Apply retry mechanism with increasing timeouts (2s, 4s, 8s, up to 20s cap).
2. **Stale worker recovery**: Identify appropriate procedures for reviving/respawning a stalled worker.
3. **Ollama fallback**: Offer an alternative workflow or task in the event of failures due to Ollama errors.
4. **Connectivity Errors**: Automatically trigger failure and escalate issues as needed, using existing support processes.

### Evidence and artifacts
- Any required logs: Specify file paths and what each contains.
- Final task report outputs and input files checked against expected states for compliance.
- List of tasks including any necessary context validation files.

---

## Review Summary
- While the draft provides detailed information on the various components of the Telegram workflow for handling real Jarvis operator requests, it could benefit from a more structured approach to readability.
- The `Preconditions` section maps well to specific environment variables (`TELEGRAM_ALLOWED_CHAT_ID`, etc.). However, it might be helpful to explicitly state the purpose of each variable to avoid confusion in the future.
- In the `Approval and risk control` section, there's a mention of a known gap regarding approval resolution being manual in version 1.5. Consider providing more context or references for clarity.
- It would be beneficial to include more details on how the post-run validation process ensures the workflow is thoroughly checked for any potential errors.
- The failure handling section provides good coverage, but consider adding more specific examples of potential failures and how they might be handled.

---

## Artifact Paths
- Claude draft: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_185838\claude_draft.md
- OpenAI review: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_185838\openai_review.md
- Final SOP: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_185838\final_sop.md
- Package report: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_185838\package_report.txt