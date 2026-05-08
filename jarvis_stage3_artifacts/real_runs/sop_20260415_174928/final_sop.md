# Jarvis Real Task Execution SOP

> Generated via real operator workflow.
> Mission ID: real_run_sop_7334d718
> Topic: Telegram workflow for handling real Jarvis operator requests

## Final SOP
---

# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

### Version 1.0
### Audience  Jarvis operators

### Scope
Handling live task and agent invocation requests through the Telegram bot interface

### Purpose:

 Define the standard procedure for operators to receive, evaluate, approve or reject, and audit a received `Jarvis task request` delivered via the Telegram bot. Ensure `all actions are carried out without unnecessary risk.

---

## 1. Preconditions

*   Check if the Telegram bot is running and reachable ([/health](https:// telegram.org/msgapi\/bot?method=getHealth)]). Returns "ok".
*   The operator's Telegram [chat\_id](https://developer\.\(org)/(api/docs/bot/api\/setUpdates)is registered and authenticated with the backend.
*   Jarvis supervisor backend is running and the SQLite approval store (for all user approvals).

## 2. Pre-run Checks

*   Check `/health` again (as in `Step 1`) to ensure it's still working
*   Check if the active execution mode (`safe` / `restricted` / full). This affects what actions can be executed.
    -   **Safe actions**: Shell, Python, HTTP tools are blocked.
    -   **Restricted actions**: Only shell commands will be allowed.
    -   **Full** allows all available capabilities:
*   Verify there are no orphaned pending approvals from the last session
*   Check if you have access to the mission information or context:

    ```
     "mission_id": int,      # Task ID
        * "step"          # List of next possible mission steps
```
### Step 3: Receive and Process Request through Telegram Bot

A request is received via Telegram. The bot immediately sends it to `/api/respond` (the backend). If a `task request` triggers `requires_approval` or you're not sure why this step is happening, an approval form pops up asking you for verification.

### Step 4: Evaluate and Decide on Request (Handle Evaluation)

A decision must be reached within the following timeframe if it's possible with current tools:

*   For "very low" risk `| mission_id` / `step_id` are known; proceed.
*   A request that triggers a denied risk classification should not be executed. If you think it necessary, let the supervisor know (see Failure Handling).
*   Request summary:
    -   You may also use an additional check to verify payload data and its relevance using tools like *`/api/payloads/{payload-id` in order to get the JSON information of the payload for further verification.*
*   The action was approved.
*   Reject: Send `POST /api/approvals/{approval_id}/reject` with an explanatory note - note that if there's any discrepancy about the message this could be flagged as human intervention and may trigger a deeper review process.

## 5. Execution Procedure (Do Things)

After approval, check the task transitions:
    *   To `pending` -> `leased`.
*   Monitor tool execution logs.
**Multi-step missions** involve a loop of repeat Step 4 for each step that triggers `requires_approval`, while you first start to do any action (as per step execution rules).

*   Always send an "operator note" during the decision making process. This is in the event something more could have been done differently.

## Step 6: Post-run Validation

Validate the task's status:
    *   Is completed?
        *   Wasn't failed or became stuck in "leased"?
        If a step was rejected, ensure this has been noted.
*   Review artifact logs `tool_runtime/logs/{} .log`
*   Ensure that the mission can be verified to follow all constraints:

    `mission_result.json` lists files:
        expected content size and file contents
*   Check external connections have been successfully made and are in expected states.

## Step 7: Failures Handling

If a mistake is happening during execution, such as tool failure etc:

*   Identify if an error exists in the logs and determine the reason (reaching out for more info might help identify what steps to recover from).

    *   First ask yourself:
        -   "Could it have been triggered by user input?" or
        -   "Would a restart do anything better?"

        If still unsure, report back the specific issue. Then you can retry, if possible.

### 8. Evidence and Artifacts

Ensure these elements are stored as part of your artifact record after every successful task:

| Artifact | Contents          | Location                               |
| ---    | ----------------- | -------------------------------------- |
| `approval record` | full data            | SQLite      `approvals` table                 |
| `mission_result.json`        | mission progress     | `mistrions/[Mission_ID]/mission_result.json`  |
| `artifact_manifest.json`   | expected file sizes | `mistrions/[Mission_ID]/artifact_manifest.json`
| `tool_runnign Logs (full log)` | logs from the specific tool used        |   jarvis_stage3_artifacts \[tool-type] / logs \{\{run}\} .log |

Store only when you've confirmed this completes your task:

---

## Review Summary
### Improvements
- Remove redundant phrases, such as "Ensure that no high-risk or ambiguous action is executed without deliberate human review." (Implicit in Step 5)
- Make sure all sections include the specific tasks operators will perform.
- Replace the "forbidden" tier description with a link to a dedicated page about forbidden actions. This maintains consistency and avoids confusing the user with too much internal detail.
- Ensure clear separation between the procedure flow (`Execution Procedure` section) and potential failure scenarios (Failure Handling). Consider separating these sections further with bold headings or clear section dividers if needed.

### Clarifications
- While "Approval" is implied in Steps 3 through 6, consider adding a more specific title for clarity. This could include an action like 'Evaluate Request' where requested evaluations occur.
- Add instructions on the expected response of the operator via Telegram once a decision or potential problem regarding execution has been made.

### Future Enhancements
- Provide examples with `forbidden` risk levels per the provided policy table.
- Define if high risk commands can be configured to have an option to either reject immediately or ask human confirmation rather than wait for potential user response through operator feedback.

---

## Artifact Paths
- Claude draft: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_174928\claude_draft.md
- OpenAI review: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_174928\openai_review.md
- Final SOP: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_174928\final_sop.md
- Package report: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_runs\sop_20260415_174928\package_report.txt