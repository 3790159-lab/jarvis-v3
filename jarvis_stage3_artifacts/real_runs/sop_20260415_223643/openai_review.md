## Review Notes

* Consider adding a clear definition of "jarvis operator" in the introduction to ensure understanding of who this SOP is intended for.
* In the approval & risk control section, provide more context on how risk levels are assigned and what decisions are made when risks are high or forbidden. This will help operators understand the decision-making process.
* In the post-run validation section, consider adding a note about data retention policies for the mission logs and artifacts.

## Final Revised SOP

### Document Structure and Codebase Mapping

| Section | Based on |
|---|---|
| **Preconditions** | `.env` config vars, `TELEGRAM_ALLOWED_CHAT_ID`, OLLAMA startup checks, directory requirements |
| **Pre-run Checks** | `/health`, `/status`, `/missions` commands in `supervisor_core.py`; path guard logic in `safety.py` |
| **Approval & Risk Control**
   - Risk levels assigned using `.env` config vars and `risk_policies.py`
   - Approval flow stored in SQLite database (`approval_store.py`)
   - API endpoints for approval control
| **Execution Procedure**
   1. Telegram commands executed from `telegram_bot.py`
   2. Mission lifecycle coordinated between `planner.py`, `task_queue.py`, and artifact management
   3. Tool execution with retries implemented in `tool_retry_executor.py` |
| **Post-Run Validation**
   - Mission log stored at `artifacts/logs/{mission_id}.log`
   - Artifact manifest located at `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`
   - Semantic memory search endpoint checks for artifact existence
   - Data retention policies to be reviewed and implemented as needed |
| **Failure Handling**
   - LLM fallback in place in `llm_router.py`
   - Lock/heartbeat pattern for mission resumes in `mission_resume_store.py`
   - `/cancel` + `/retry` commands available |
| **Evidence & Artifacts**
   - Real-time data at the following paths:
     * `state/missions.json`
     * `state/mission_memory.json`
     * Approval SQLite DB (`approval_store.py`)
     * Artifact manifest (`jarvis_stage3_artifacts/artifact_runtime/`) |

This revised SOP maintains its focus on operators and presents a clear, logical structure for Telegram workflow handling.