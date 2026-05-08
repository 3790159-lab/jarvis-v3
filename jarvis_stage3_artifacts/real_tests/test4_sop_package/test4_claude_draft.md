The write needs your approval â please accept the file write permission. Once granted it will create `docs/jarvis_real_task_sop.md`.

While you decide, here's a summary of what's in the document and why each section is grounded in the actual system:

**What the SOP covers (and how it maps to real code):**

| Section | Grounded in |
|---|---|
| Preconditions | `start_api.bat`, `start_worker.bat`, `.env` setup from README |
| Pre-run checks | `LocalShellExecutor` runs without sandbox; `LLM_MODE` affects interpretation |
| Risk classification | `shell_local` has no command filtering; `codex_cloud` needs credentials |
| `/chat` procedure | `ChatRequest` model fields (`wait_for_completion`, `max_wait_seconds`) |
| `/intake` procedure | Goal â Mission â Task hierarchy from `models.py` |
| Executor notes | Shell timeout (600s default), `DEFAULT_SHELL` setting, `codex` CLI dependency |
| Post-run validation | `returncode` in `result`, `TaskStatus` enum (`queued/running/succeeded/failed`) |
| Failure handling | `unhandled_exception` flag in worker, stuck-`running` task scenario |
| Artifacts | `jarvis_stage3_artifacts/reports/` path already exists in the repo |