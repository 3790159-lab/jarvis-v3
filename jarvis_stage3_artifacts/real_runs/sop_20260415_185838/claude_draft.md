The document is ready. Once you approve the write permission it will save to `SOP_telegram_operator_workflow.md` in the project root.

Here's a summary of what was generated and why each section is grounded in the actual codebase:

---

**Section breakdown:**

1. **Purpose** â scoped to the exact operator-facing Telegram interface
2. **Preconditions** â maps to real env vars (`TELEGRAM_ALLOWED_CHAT_ID`, `SUPERVISOR_POLICY_PROFILE`, `OLLAMA_ENABLED`, etc.)
3. **Pre-run checks** â uses real commands (`/health`, `/diag`, `/missions`) and actual state files
4. **Approval and risk control** â reflects the actual `safe_manual` / `operator_assisted` / `autonomous_limited` modes from `autonomy/modes.py`, including the known gap that approval resolution is manual in v1.5
5. **Execution procedure** â separate flows for slash commands, NL missions, shell tasks, and Google integrations, mapped to actual task types (`shell`, `file_write`, `integration`, etc.)
6. **Post-run validation** â checks real outputs: `/logs`, `/context`, `artifacts/output/`, escalation log
7. **Failure handling** â covers retry backoff (2sâ4sâ8sâ20s cap), stale worker recovery, Ollama fallback, and connectivity errors â all from actual code
8. **Evidence and artifacts** â exact file paths and what each contains, plus a per-request checklist