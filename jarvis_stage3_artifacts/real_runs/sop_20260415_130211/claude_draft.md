The file write needs your approval â please allow it in the permission prompt.

Once you approve, the file `SOP_Telegram_Operator_Requests.md` will be saved to the project root. Here's a summary of what's in it, grounded directly in the codebase:

---

**Section breakdown:**

| # | Section | Key content |
|---|---|---|
| 1 | Purpose | Scope: full lifecycle from Telegram message to execution confirmation |
| 2 | Preconditions | Checklist: bot process, worker loop, both SQLite DBs, `.env` policy, LLM backend, API access |
| 3 | Pre-run checks | Intent classification confidence threshold, pending queue depth limit, `EXECUTION_MODE=safe` enforcement |
| 4 | Approval & risk | Exact `low/guarded/high_risk/forbidden` decision table from `risk_policies.py`; API calls for approve/reject; auto-reject blocklist from `safety.py`; escalation criteria |
| 5 | Execution procedure | Task state machine (`queuedârunningâcompleted`), worker dispatch, 60 s timeout behavior, 3 500-char message split |
| 6 | Post-run validation | Shell `returncode` check, trace record review (`latency_ms` > 30 s flag), Telegram delivery confirmation, multi-step mission closure |
| 7 | Failure handling | Error pattern table with causes and actions, `/retry` limit, stale approval SLA (15 min), bot-down protocol |
| 8 | Evidence & artifacts | Four artifact types with exact DB/field locations; minimum evidence standard for `high_risk`; 30-day retention rule |