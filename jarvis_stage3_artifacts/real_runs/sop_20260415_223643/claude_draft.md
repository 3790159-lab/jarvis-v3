The file write needs your approval. Once you grant it, the file will be created at:

`supervisor_v1_5_smart_telegram/SOP_Telegram_Operator_Requests.md`

While you decide, here's a summary of what the document covers and why each section is grounded in your actual codebase:

---

**Document structure and codebase mapping:**

| Section | Based on |
|---|---|
| **Preconditions** | `.env` config vars, `TELEGRAM_ALLOWED_CHAT_ID`, OLLAMA startup checks, directory requirements |
| **Pre-run checks** | `/health`, `/status`, `/missions` commands in `supervisor_core.py`; path guard logic in `safety.py` |
| **Approval & risk control** | `risk_policies.py` risk levels (`low/guarded/high_risk/forbidden`), `approval_store.py` SQLite approval flow, approval API endpoints |
| **Execution procedure** | Telegram commands from `telegram_bot.py`, mission lifecycle from `planner.py` + `task_queue.py`, tool execution with retries from `tool_retry_executor.py` |
| **Post-run validation** | Mission log path `artifacts/logs/{mission_id}.log`, artifact manifest at `jarvis_stage3_artifacts/artifact_runtime/missions/{mission_id}/`, semantic memory search endpoint |
| **Failure handling** | LLM fallback in `llm_router.py`, lock/heartbeat pattern in `mission_resume_store.py`, `/cancel` + `/retry` commands |
| **Evidence & artifacts** | `state/missions.json`, `state/mission_memory.json`, approval SQLite DB, artifact manifest â all real paths from the codebase |