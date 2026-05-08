# SOP: Telegram Workflow for Handling Real Jarvis Operator Requests

## Purpose

This SOP defines the production workflow for handling real Jarvis operator requests received through Telegram.

A **Jarvis operator** is the human authorized to issue Telegram commands to the running Jarvis supervisor instance from the allowed Telegram chat configured in `TELEGRAM_ALLOWED_CHAT_ID`.

This SOP is grounded in the current codebase and maps directly to:

| Section | Based on |
|---|---|
| **Preconditions** | `.env` config vars, `TELEGRAM_ALLOWED_CHAT_ID`, OLLAMA startup checks, directory requirements |
| **Pre-run checks** | `/health`, `/status`, `/missions` commands in `supervisor_core.py`; path guard logic in `safety.py` |
| **Approval & risk control** | `risk_policies.py` risk levels (`low/guarded/high_risk/forbidden`), `approval_store.py` SQLite approval flow, approval API endpoints |
| **Execution procedure** | Telegram commands from `telegram_bot.py`, mission lifecycle from `planner.py` + `task_queue.py`, tool execution with retries from `tool_retry_executor.py` |
| **Post-run validation** | Mission log path `artifacts/logs/{mission_id}.log`, artifact manifest at `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`, semantic memory search endpoint |
| **Failure handling** | LLM fallback in `llm_router.py`, lock/heartbeat pattern in `mission_resume_store.py`, `/cancel` + `/retry` commands |
| **Evidence & artifacts** | `state/missions.json`, `state/mission_memory.json`, approval SQLite DB, artifact manifest — all real paths from the codebase |

---

## 1. Preconditions

Before accepting or executing any Telegram operator request, confirm all of the following:

### 1.1 Required configuration is present

Verify the runtime environment is configured with the required `.env` values, including:

- Telegram bot credentials
- `TELEGRAM_ALLOWED_CHAT_ID`
- Any LLM routing configuration required by `llm_router.py`
- Any OLLAMA-related configuration required for local model startup and health

Do not process operator requests if `TELEGRAM_ALLOWED_CHAT_ID` is missing or incorrect. Only the configured allowed chat may issue real operator commands.

### 1.2 Required services are available

Confirm the Jarvis supervisor stack is running and reachable, including:

- Telegram bot handling from `telegram_bot.py`
- Supervisor command handling in `supervisor_core.py`
- Approval flow backed by `approval_store.py`
- Mission planning and queueing via `planner.py` and `task_queue.py`
- Tool execution path via `tool_retry_executor.py`
- LLM routing via `llm_router.py`
- OLLAMA availability if the configured route depends on it

If OLLAMA is required by the active model route and is not healthy, do not start a real mission until the route is restored or fallback behavior is confirmed operational.

### 1.3 Required directories and writable state exist

Confirm the process can read/write the expected runtime locations used by the system, including:

- `artifacts/logs/`
- `jarvis_stage3_artifacts/artifact_runtime/missions/`
- `state/missions.json`
- `state/mission_memory.json`

Also confirm the approval store backing `approval_store.py` is writable.

If any required path is missing or not writable, stop and correct that before handling operator requests.

---

## 2. Pre-Run Checks

Run these checks before acting on a new Telegram request.

### 2.1 Confirm bot health

Use the Telegram health command implemented in `supervisor_core.py`:

- `/health`

Expected result:
- Supervisor responds successfully
- Core services report healthy enough to accept work

If `/health` fails, do not proceed with mission execution.

### 2.2 Confirm current runtime status

Use:

- `/status`

Review for:
- Current mission activity
- Whether the system is already executing another mission
- Any degraded model/tool state
- Any pending approval or resume condition

If the system is already in a conflicting active state, resolve that first.

### 2.3 Review active and recent missions

Use:

- `/missions`

Review:
- Active mission IDs
- Recently completed or failed missions
- Whether the incoming request is a duplicate, retry, or continuation

Do not create duplicate missions for the same operator intent if an existing mission is already active or resumable.

### 2.4 Apply path safety expectations

Before approving or executing any request that touches files, paths, or artifacts, ensure the requested action is consistent with the path guard logic in `safety.py`.

Reject or escalate any request that attempts:
- Unsafe path traversal
- Access outside allowed working areas
- Destructive or ambiguous file targeting not clearly permitted by the safety layer

---

## 3. Intake of a Real Telegram Operator Request

### 3.1 Validate sender context

Accept the request only if it originates from the Telegram chat configured in `TELEGRAM_ALLOWED_CHAT_ID`.

If the message comes from any other chat:
- Do not execute
- Do not create a mission
- Treat as unauthorized input

### 3.2 Determine whether the request is operational

A real operator request is one that asks Jarvis to:
- Inspect system state
- Plan or execute a mission
- Use tools
- Read or write approved files
- Produce artifacts
- Retry, cancel, or resume prior work

If the message is conversational only and does not require mission execution, handle it without creating unnecessary mission state.

### 3.3 Normalize the request into an actionable mission intent

Before execution, identify:
- The operator’s exact objective
- Expected outputs or artifacts
- Whether tools or file operations are required
- Whether approval is likely required under `risk_policies.py`

If the request is ambiguous, clarify before execution rather than guessing.

---

## 4. Approval and Risk Control

All real operator requests must be evaluated under the risk model implemented in `risk_policies.py`.

### 4.1 Risk levels

Use the risk levels defined in code:

- `low`
- `guarded`
- `high_risk`
- `forbidden`

Do not rename or reinterpret these levels.

### 4.2 Required handling by risk level

#### `low`
- May proceed without special approval if no other policy blocks execution
- Still subject to path safety and normal mission logging

#### `guarded`
- Proceed only within the guardrails enforced by the system
- If the action triggers approval flow, use the approval mechanism in `approval_store.py`
- Confirm the operator understands the constrained scope if needed

#### `high_risk`
- Do not execute automatically
- Route through the approval flow backed by `approval_store.py`
- Wait for explicit approval through the configured approval API endpoints before continuing

#### `forbidden`
- Do not execute
- Do not attempt workaround behavior
- Respond that the requested action is blocked by policy

### 4.3 Approval flow

When approval is required:

1. Create or reference the approval record in the SQLite-backed approval system used by `approval_store.py`
2. Ensure the approval request is tied to the correct mission/action
3. Wait for approval through the configured approval API endpoints
4. Only continue after approval is recorded successfully

If approval is denied or expires:
- Do not execute the blocked action
- Mark the mission accordingly
- Inform the operator of the blocked state

### 4.4 Operator decision points for risky actions

For `high_risk` requests, the operator must explicitly approve before execution continues.

For `forbidden` requests:
- Stop immediately
- Do not decompose the request into smaller steps intended to bypass policy

---

## 5. Execution Procedure

### 5.1 Start from the Telegram command path

Handle the request through the Telegram command flow implemented in `telegram_bot.py`.

Use the Telegram interface as the operator control plane for:
- Starting work
- Checking status
- Reviewing missions
- Canceling
- Retrying

### 5.2 Create or continue the mission lifecycle

Mission lifecycle is coordinated through:

- `planner.py`
- `task_queue.py`

Operational steps:

1. Convert the operator request into a mission plan
2. Queue the mission through the task system
3. Execute tasks in the planned order
4. Persist mission state as execution progresses

Use existing mission state where appropriate instead of creating duplicate work.

### 5.3 Tool execution and retries

Tool execution is handled through `tool_retry_executor.py`.

When a tool step runs:
- Allow the built-in retry behavior to handle transient failures
- Do not manually duplicate the same tool action unless the retry path has completed and the result is still unresolved
- Record the final outcome in mission state and logs

If a tool repeatedly fails after retry handling:
- Mark the step failed
- Escalate to failure handling rather than looping indefinitely

### 5.4 File and artifact operations

When the mission writes outputs:
- Ensure writes stay within allowed paths enforced by `safety.py`
- Preserve generated artifacts in the runtime artifact structure
- Do not write outside the approved artifact or working directories

Expected artifact location pattern:

- `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`

Expected mission log path:

- `artifacts/logs/{mission_id}.log`

### 5.5 Mission state persistence

During execution, ensure state is persisted to the real runtime stores, including:

- `state/missions.json`
- `state/mission_memory.json`

Do not treat the mission as complete until state and artifacts are written successfully.

---

## 6. Post-Run Validation

After mission execution, validate the result before considering the operator request complete.

### 6.1 Check mission log

Open and review:

- `artifacts/logs/{mission_id}.log`

Confirm the log shows:
- Mission start
- Major execution steps
- Approval events if applicable
- Final success, failure, cancel, or retry state

### 6.2 Check artifact output

Inspect:

- `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`

Confirm:
- Expected files exist
- Outputs match the operator request
- No unexpected or unsafe files were written

### 6.3 Check mission state records

Verify mission persistence in:

- `state/missions.json`
- `state/mission_memory.json`

Confirm the mission status, summary, and any memory entries are consistent with the actual run.

### 6.4 Validate semantic memory linkage if used

If the mission depends on memory retrieval or artifact recall, verify the semantic memory search endpoint returns the expected mission/artifact references.

### 6.5 Close the operator loop

Report back through Telegram with:
- Mission outcome
- Mission ID
- Key artifact locations
- Any approvals used
- Any follow-up action required

### 6.6 Retention note

Mission logs, mission state, approval records, and artifacts remain in their runtime locations unless separately cleaned up by an explicit retention or maintenance process. Do not delete them as part of normal mission completion unless a separate approved procedure requires it.

---

## 7. Failure Handling

### 7.1 LLM routing or model failure

If the primary model path fails, rely on fallback behavior implemented in `llm_router.py` where available.

If fallback succeeds:
- Continue the mission
- Record the route change in logs if visible in mission output

If fallback does not succeed:
- Fail the mission cleanly
- Report the model failure to the operator

### 7.2 Interrupted or stalled mission recovery

Use the lock/heartbeat recovery pattern implemented in `mission_resume_store.py` to determine whether a mission can be resumed safely.

Before resuming:
- Confirm the mission is not actively running elsewhere
- Confirm lock/heartbeat state permits recovery
- Resume only once

Do not start a second concurrent execution of the same mission.

### 7.3 Operator-driven cancellation

Use:

- `/cancel`

When cancellation is requested:
- Stop further execution as soon as the mission framework allows
- Preserve logs and partial artifacts already created
- Mark the mission canceled in state

### 7.4 Operator-driven retry

Use:

- `/retry`

Before retrying:
- Review the original failure in `artifacts/logs/{mission_id}.log`
- Confirm the retry will not duplicate unsafe side effects
- Reuse or create mission state according to the retry behavior implemented by the system

### 7.5 Approval-related failure

If approval cannot be recorded, retrieved, or validated through the approval API endpoints or SQLite store:
- Do not execute the gated action
- Mark the mission blocked or failed
- Notify the operator that approval infrastructure prevented execution

---

## 8. Evidence and Artifacts

The following runtime artifacts are the authoritative evidence trail for Telegram-handled operator work:

- `state/missions.json`
- `state/mission_memory.json`
- Approval SQLite DB used by `approval_store.py`
- `artifacts/logs/{mission_id}.log`
- `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`

Use these locations for:
- Audit
- Troubleshooting
- Resume/retry decisions
- Verification of what was approved, executed, and produced

Do not rely on Telegram chat history alone as the system of record when mission state or artifacts are in question.

---

## 9. Operator Checklist

For each real Telegram operator request, complete this sequence:

1. Confirm request came from `TELEGRAM_ALLOWED_CHAT_ID`
2. Run `/health`
3. Run `/status`
4. Run `/missions`
5. Check whether the request triggers path safety concerns under `safety.py`
6. Evaluate risk using `risk_policies.py`
7. If required, obtain approval through `approval_store.py` and approval API endpoints
8. Execute through the mission flow in `telegram_bot.py`, `planner.py`, and `task_queue.py`
9. Allow tool retries through `tool_retry_executor.py`
10. Validate:
    - `artifacts/logs/{mission_id}.log`
    - `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`
    - `state/missions.json`
    - `state/mission_memory.json`
11. Report outcome and artifact locations back to the operator
12. If failed, use `/cancel`, `/retry`, fallback handling in `llm_router.py`, or resume logic in `mission_resume_store.py` as appropriate

---

## 10. Stop Conditions

Do not proceed with a Telegram operator request if any of the following is true:

- The message did not come from `TELEGRAM_ALLOWED_CHAT_ID`
- `/health` indicates the system is not ready
- Required runtime paths are missing or not writable
- The request is classified as `forbidden`
- The request is `high_risk` and approval has not been recorded
- Path safety checks in `safety.py` would be violated
- Mission resume/lock state indicates another active execution is in progress

In these cases, stop, preserve state, and return a clear Telegram response describing why execution did not proceed.
