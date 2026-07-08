# Backup Audit — May 20, 2026

_Read-only analysis ahead of pushing this repo to an off-site remote. Nothing is modified outside this document. All credential strings, where present, are truncated at 12 chars._

## Section 1 — Current `.gitignore`

```gitignore
.venv/
__pycache__/
.pytest_cache/
*.pyc
*.pyo
.env
*.log
node_modules/
.DS_Store

# RunPod secrets
.env.runpod

# Phase 2 additions
state/
logs/
.env.backup_*
.env.before_*
*.before_*
backup_*/
backup_*.py
backup_*.env
.claude/
creds/
*_oauth_token*.json
*.pid
bot_heartbeat.txt

# Block M.2 smoke test fixtures and local run logs
data/test_inputs/
scripts/smoke_test_*.log
scripts/runpod_recreate_*.log
```

The file exists and has 33 lines covering the common danger zones. Two gaps observed (see Section 5 below): the `.env.runpod.bak_*` naming variant doesn't match any current pattern, and there's no rule for the large `jarvis_stage3_artifacts/internet_tools/research_*.json` accumulation.

## Section 2 — Untracked file audit

`git status --porcelain | grep -E "^\?\?"` returned 35 entries. Grouped by category:

### 2a. Backup files (should stay ignored)
- `.env.runpod.bak_phase3.2_20260514_200659` — RunPod env backup. Currently slips past `.env.backup_*` and `.env.runpod` patterns because of the `.bak_*` suffix.

### 2b. Log / error files
- `scripts/runpod_recreate_20260519_022155.log.err` — partial match by `scripts/runpod_recreate_*.log` but the `.log.err` extension defeats the pattern.

### 2c. Runtime / state artifacts (heavy noise, no value to commit)
- 24 × `jarvis_stage3_artifacts/internet_tools/research_*.json` — research result dumps from May 9-18.
- 1 × `data/block_m2_video/` — output directory created by today's Wan i2v work. Should likely follow `data/test_inputs/` precedent and be gitignored.
- The single tracked modification (`jarvis_stage3_artifacts/telegram_smart_control/brain_v2_state.json`) is runtime brain state — same category, but already in the index; needs `git update-index --skip-worktree` or `git rm --cached` plus an ignore rule.

### 2d. Debug / scratch scripts
- `scripts/_pod_count.py`, `scripts/check_gpus.py`, `scripts/debug_names.py`, `scripts/debug_persona.py`, `scripts/gpu_probe.py`, `scripts/list_gpus.py`, `scripts/list_personas.py` — exploratory throwaways. Author decision: commit useful ones explicitly under non-debug names, ignore the `debug_*` + `_*` prefixes globally.

### 2e. Real artifacts that may want committing later
- `scripts/remote/install_section_a_and_e.ps1` — looks like an installer, not a one-shot debug script. Review before tomorrow.
- This doc + `SESSION_HANDOFF_20260520.md` + `PHASE_E2_PLAN_20260520.md` — intentionally untracked per the prep pack spec.

## Section 3 — Sensitive file presence (path-only)

Scanned for `.env*`, `*.key`, `*.pem` within depth 3 (excluding `.venv`).

### `.env*` files

| Path | Tracked? | Gitignored? | Notes |
|---|---|---|---|
| `.env` | NO | ✅ yes (line 6) | Standard. |
| `.env.example` | **YES** | no | Template; safe to keep tracked. |
| `.env.runpod` | NO | ✅ yes (line 12) | Standard. |
| `.env.runpod.example` | **YES** | no | Template; safe. |
| `.env.runpod.bak_phase3.2_20260514_200659` | NO (untracked) | **NO** | ⚠️ Slips through current patterns. See Section 5. |
| `.env.stage3` | **YES** | no | ⚠️ **Tracked file with `.env` prefix.** Filename suggests live config, not template. **Must inspect before pushing.** |
| `.env.backup_20260501_201858` | NO | ✅ yes (`.env.backup_*`) | Standard. |
| `.env.before_path_fix_20260507_032412` | NO | ✅ yes (`*.before_*`) | Standard. |
| `.env.before_path_fix_20260507_171731` | NO | ✅ yes (`*.before_*`) | Standard. |
| `n8n_local/.env` | NO | ✅ yes (`.env`) | Standard. |
| `infra/docker/n8n/.env` | NO | ✅ yes (`.env`) | Standard. |
| `n8n_docker_strong/.env` | NO | ✅ yes (`.env`) | Standard. |
| `jarvis_claude_review_pack/.env.example` | **YES** | no | Template; safe. |
| `jarvis_stage3_artifacts/.../module.env.example` | **YES** | no | Template; safe. |
| `jarvis_stage3_artifacts/.../module.local.env` | **YES** | no | ⚠️ **Tracked `.local.env` — naming suggests real credentials. Must inspect before pushing.** |
| `jarvis_stage3_artifacts/.../module.local.env.example` | **YES** | no | Template; safe. |

### `*.key` files
None outside `.venv/`. ✅

### `*.pem` files
All matches are CA bundles inside vendored `certifi` directories under `.venv/` or under `jarvis_stage3_artifacts/generated_projects/*/.venv/`. The `.venv/` rule covers the project's own venv; the nested generated-project venvs are **not** covered and contain redundant copies of `cacert.pem`. Low secret risk (these are public root certs) but they bloat the working tree.

## Section 4 — Git history secret scan

Ran `git log -p --all 2>&1 | grep -iE "(api[_-]?key|secret|token|password|bot[_-]?token).{0,4}=.{8,}"` and reviewed the matches.

| Source pattern (truncated) | Commit hash | Classification |
|---|---|---|
| `BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()` | various | FALSE POSITIVE — variable read from env, no value |
| `api_key=SecretStr("rpa_test_xxx` | various test fixtures | FALSE POSITIVE — placeholder for tests (`rpa_test_`) |
| `api_key=SecretStr("rpa_test_ke` | various test fixtures | FALSE POSITIVE — placeholder |
| `auth_token="secret_bear` | comfyui test | FALSE POSITIVE — literal `"secret_bearer_token"` test string |
| `bot_token="testtoken12` | notifier test | FALSE POSITIVE — literal `"testtoken123456"` |
| `bot_token="abc12345679` | notifier test | FALSE POSITIVE — literal `"abc1234567890"` |
| `TELEGRAM_BOT_TOKEN=<your_bot_token>` | `.env.example` | FALSE POSITIVE — placeholder bracket syntax |
| `ANTHROPIC_API_KEY=sk-ant-..` | `.env.example` | FALSE POSITIVE — ellipsis placeholder |
| `PERPLEXITY_API_KEY=pplx-...` | `.env.example` | FALSE POSITIVE — ellipsis placeholder |
| `TAVILY_API_KEY=tvly-...` | `.env.example` | FALSE POSITIVE — ellipsis placeholder |
| `N8N_API_KEY=<your_n8n_a` | `.env.example` | FALSE POSITIVE — bracket placeholder |
| `INFLUENCER_API_KEY=<your_key>` | `.env.example` | FALSE POSITIVE — bracket placeholder |
| `TELEGRAM_BOT_TOKEN=ваш_токе` | docs / Russian template | FALSE POSITIVE — `ваш_токен` = "your_token" in Russian |
| `OPENAI_API_KEY=sk-...` | `.env.example` | FALSE POSITIVE — ellipsis placeholder |
| `REPLICATE_API_KEY=your_key_h` | `.env.example` | FALSE POSITIVE — literal placeholder |
| `REPLICATE_API_KEY=r8_xxxxxx` | `.env.example` | FALSE POSITIVE — `r8_xxxxxxxx` placeholder, not a real Replicate key |
| `api_key=my_secret_ad` | docs curl example | FALSE POSITIVE — literal `my_secret_admin_key` example |
| All `os.getenv("..._KEY")`, `os.getenv("..._TOKEN")` reads | various code | FALSE POSITIVE — env reads, no values |

**No POTENTIAL LEAKs and no VERIFIED LEAKs found in the scanned slice of history.**

⚠️ **Caveat:** This grep covers `git log -p --all`, which only shows what's committed. The two tracked-but-uninspected files from Section 3 (`.env.stage3` and `module.local.env`) could already be in history with real values. The grep above did not return hits from them, which suggests they're either (a) committed with placeholder/empty values, or (b) committed with values that don't match the regex pattern. **Before pushing, manually inspect both:**

```powershell
git log --oneline -- .env.stage3
git log --oneline -- jarvis_stage3_artifacts/generated_modules/telegram_sms_responder_v1/config/module.local.env
git show <commit>:.env.stage3 | head -20    # head only — never paste full output anywhere
```

If contents are placeholders, leave them tracked. If they contain real keys, jump to Section 6 step 3 (history scrub vs rotate decision).

## Section 5 — Proposed `.gitignore` additions

Ready-to-paste block. Designed to catch the gaps found in Section 2-3 without breaking existing rules.

```gitignore
# --- Phase 3 additions (May 20, 2026) ---

# Env-variant backups missed by existing patterns
.env.*.bak_*
.env.runpod.bak_*

# Research result dumps (non-source artifacts)
jarvis_stage3_artifacts/internet_tools/research_*.json

# Block M.2 video output directory (mirror of data/test_inputs/ rule)
data/block_m2_video/

# Brain / runtime state JSON shouldn't be tracked
jarvis_stage3_artifacts/telegram_smart_control/brain_v2_state.json

# Debug / scratch scripts (commit useful ones under proper names instead)
scripts/debug_*.py
scripts/_*.py

# .log.err sibling files alongside .log
scripts/*.log.err

# Nested generated-project venvs (recursive certifi/pip bloat)
jarvis_stage3_artifacts/generated_projects/*/.venv/
```

After applying these, `git status` should drop from 35 untracked entries to roughly 4-5 (the three prep docs + `scripts/remote/install_section_a_and_e.ps1` if you want to keep it).

## Section 6 — Recommended remediation path

Five steps, in order. Each step is gated by the previous step's result.

1. **Apply Section 5 additions to `.gitignore`.**
   - Append the proposed block.
   - Run `git status --short` to verify previously-noisy paths disappear.

2. **Decide on the two tracked-but-suspect files** (`.env.stage3` and `module.local.env`).
   - `git show HEAD:.env.stage3 | head -20` — peek at contents.
   - If placeholders: leave tracked. If real credentials: `git rm --cached <file>`, add the path explicitly to `.gitignore`, and continue to step 3.
   - Also commit `git rm --cached jarvis_stage3_artifacts/telegram_smart_control/brain_v2_state.json` since the state file is being modified locally without intent to track.

3. **History scrub vs rotate — only if step 2 found real credentials.**
   - **Rotate (recommended for most cases):** revoke leaked keys on the providers (Anthropic, OpenAI, Replicate, RunPod, Telegram bot token via @BotFather), generate new ones, update local `.env*` files. History stays as-is; the leaked values become useless.
   - **Scrub:** `git filter-repo --invert-paths --path .env.stage3` (do NOT run without a backup of the repo first; rewrites every commit hash). Only worth it if you're certain no one else has the history and you cannot rotate (rare).

4. **Create PRIVATE repo on GitHub or GitLab.**
   - Verify visibility is **Private** before push. Public-by-accident is the most common backup mishap.
   - Naming suggestion: `jarvis-v3` or `jarvis` under your personal account.

5. **Push.**
   ```bash
   git remote add origin <https://github.com/.../jarvis.git>
   git push -u origin phase-3.0-inventory-stop-reliability
   git push origin --all      # only if you want all local branches mirrored
   ```
   - Verify on the remote UI that the latest commit visible is `c6ed64d`.
   - Set up branch protection later — not blocking for the backup goal.
