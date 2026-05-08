# Optimization notes for V1.5

## Implemented in this release

1. **Intent separation**
   - normal questions stay in the assistant layer
   - project commands go into planner/executor

2. **Safer fallback**
   - the assistant no longer defaults to `list files` for every unknown message
   - low-confidence unmatched requests now ask for clarification

3. **Typo handling**
   - common Python misspellings are normalized before routing

4. **Optional LLM layer**
   - interpretation and response formatting can be upgraded later without changing the architecture

## High-value next upgrades

1. **Per-chat memory in Telegram**
   - remember each chat's preferred project root
   - remember recent tasks and last successful strategy

2. **Status and history commands**
   - `/status`
   - `/last`
   - `/project`

3. **Safer execution controls**
   - confirmation for risky shell commands
   - allowlist of approved task types in Telegram mode

4. **Richer planner coverage**
   - `git diff`
   - `run tests`
   - `open README`
   - `find error logs`
   - `inspect .env`

5. **Task result compaction**
   - summarize long outputs automatically
   - attach raw output only when explicitly requested

6. **Notification model**
   - accepted -> running -> completed updates in Telegram
   - optional progress messages for long Codex tasks

7. **Codex improvement path**
   - parse Codex output into structured findings
   - include next-step suggestions and patch summary
