# Jarvis V3 — Day 8 Handoff Brief

**For:** Next Claude chat picking up partnership with Daniil
**Last updated:** 2026-06-05 — ✅ **Day 8 COMPLETE** (all 5 tasks shipped + pushed)
**Save location:** `C:\jarvis\artifacts\obsidian_vault\03_Issues\Jarvis-Day8-Handoff.md`

---

## 🎯 Critical first read (in order)

1. **You're partnering with Daniil Lapin** — restaurant/events owner in Kyiv, building Jarvis V3 personal AI automation as side project. He calls you **"напарник" (partner)**. Direct, peer-to-peer collaboration — explicit step-by-step execution over abstract guidance.

2. **Communication is Russian-primary** with English technical terms mixed in. Cyrillic + Latin keyboard occasionally mixed (typos like "длч"/"для"). Casual + enthusiastic tone. Don't over-formalize.

3. **Time zone:** Kyiv (EEST). Daniil works late nights often (sessions till 02:00-04:00). When wrap-up needed, be empathetic about hour.

4. **Project canonical path:** `C:\jarvis` on Windows desktop PC `jarvis-desktop`. Accessed via SSH from laptop (Cloudflare Tunnel `jarvis-d.com`, key `jarvis_desktop` ed25519).

5. **Branch:** `phase-3.0-inventory-stop-reliability` on `git@github.com:3790159-lab/jarvis-v3.git`

6. **Python 3.14, FastAPI + Telegram bot + RunPod GPUs + ComfyUI + Wan 2.2 i2v**

---

## 📊 Current state — ✅ Day 8 COMPLETE (HEAD `8ceecc2`)

### Commits ahead since Day 7 (HEAD `e5666cf`)

| # | Commit | Description | Status |
|---|--------|-------------|--------|
| Task 1 | `a8843d4` | fix(video): retry ComfyUI cold start on fresh pod (max 3 attempts) | ✅ shipped |
| Task 2 | `77f346f` | feat(auth): add user whitelist for bot access control | ✅ shipped |
| Task 3 | `fd2fa17` | feat(audit): per-day jsonl audit log + admin Telegram forwards | ✅ shipped |
| Task 4 | `fd1b269` | feat(cost): per-user cost tracking with /my_stats and /admin_costs | ✅ shipped |
| Task 5 | `8ceecc2` | feat(bot): B-51 media_group dedupe diagnostic logging (no behavior change) | ✅ shipped |

**All 5 Day 8 tasks shipped and pushed to `origin/phase-3.0-inventory-stop-reliability`.** Plus handoff `f09417c`.

### Day 8 plan (Path C, hybrid reliability + sharing) — DONE

5 tasks total. Goal: ship reliability fixes + enable safe sharing of bot with friends. ✅ All achieved: cold-start retry, whitelist access control, audit logging + admin forwards, per-user cost visibility, and B-51 diagnostic instrumentation.

### Verified production state (as of Day 7 / overnight test)

✅ Block M.2.5 (face-swap + animation) PRODUCTION-COMPLETE
✅ Custom per-frame motion prompts (Day 6)
✅ Variable duration 3-15s (Day 7 Task C)
✅ Photo limit 20 + "долго" warning (Day 7 Task B)
✅ Post-swap menu visibility (Day 7 Task A)
✅ FPS interpolation 21-60 verified + unlocked (Day 7)
✅ First 30 fps production video shipped (validated)
✅ First 24 fps "cinema feel" overnight test (Daniil's preference confirmed — 30fps was "too smooth", 24fps "вроде нормально")
✅ Continue-on-failure animation logic working in production
✅ Day 8 ALL 5 tasks shipped (cold-start retry, whitelist, audit, cost tracking, B-51 diagnostics)

---

## 🚀 How we work (operational conventions)

### TDD discipline (strict)

Every CC task starts with:
1. CC loads `superpowers:test-driven-development` skill via Skill tool
2. Maps existing code + tests
3. Writes failing tests (RED)
4. Implements minimum to pass (GREEN)
5. Verifies no regressions in affected test files
6. Verifies full suite still has same pre-existing failures only (currently ~4-6, varies by run; subset of historical 7)
7. Commits with conventional message
8. Pushes origin

### Single CC session per task (HARD RULE — learned painfully)

⚠️ **Day 8 incident:** Daniil ran Task 1 in parallel CC sessions accidentally. Both worked, second discovered first had already pushed. Caused collision around stop_pod vs terminate_pod semantics. Resolution: accept first commit, drop second's redundant tests.

**Lesson:** ONE CC session per task at a time. Wait for "commit hash X, pushed to origin" message before starting next task.

### Claude Code launch convention

Always `--dangerously-skip-permissions`. Daniil uses Claude Code in `C:\jarvis`. Pattern: paste detailed prompt, CC executes autonomously, reports back with commit hash.

### Prompt structure that works

Heavy structured prompts with explicit:
- CONTEXT (branch, HEAD, files involved, Day N task)
- CURRENT BEHAVIOR (what exists now)
- REQUIRED BEHAVIOR (what to change)
- CONSTRAINTS (TDD, no breaks, file boundaries)
- TESTS list (concrete test names)
- WORKFLOW (step-by-step)
- REPORT BACK (what to send back)

CC at this scale needs spec-quality prompts, not casual asks.

### Russian comments OK, code English

Code identifiers in English. Comments in Russian or English (mixed acceptable). User-facing messages (Telegram, error texts) usually Russian. Documentation tends English.

---

## 🛠️ Tech stack quick map

```
Frontend:    Telegram bot (single user → expanding to whitelist)
Backend:     FastAPI on 127.0.0.1:8010 (local) + Cloudflare Tunnel jarvis-d.com
AI Routing:  n8n cloud (outside repo) + multi-AI providers
Image AI:    Replicate (FLUX 1.1 Pro for personas, ~$0.02/photo)
Video AI:    RunPod (Wan 2.2 i2v Remix NSFW, A100 80GB ~$1.49/hr, ~$0.30/video)
Face swap:   ReActor on RunPod ComfyUI (custom pod template)
FPS boost:   ComfyUI-VFI RIFEInterpolation (NOT Fannovel16 — see Day 7 bootstrap drift note)
State:       Local JSON in C:\jarvis\state\ + git
Pod region:  EU-RO-1 (A100 PCIe 80GB / SXM4-80GB only; L40S not available)
```

---

## 📋 Active issues / things to watch

### 🔴 Known bugs (in Day 8 backlog)

1. **B-51 media_group dedupe / buffer flush** — Daniil sends N photos, bot receives <N. Pattern observed: 5→2, 5→3 losses. Root cause still unconfirmed, but **Task 5 (`8ceecc2`) shipped diagnostic logging** — next album loss will be greppable. Grep `media_group:` in `logs/jarvis_bot.log`. Decision tree once a loss reproduces:
   - `NEW media_group_id` lines during ONE album → Telegram split the album across group ids → re-key buffer on chat_id + time window.
   - `gap_since_last` >2s between photos → flush timer split the album → raise the `2.0`s threshold (hardcoded at `_main_inner` ~line 5560).
   - `SKIPPED duplicate` for photos meant to be distinct → Telegram reused `file_unique_id` (cached/forwarded media) → dedupe too aggressive.

   **🔴 NEW CRITICAL FINDING #1 — latent webhook-path total-loss bug (separate from B-51 dedupe).** There are TWO media_group code paths:
   - **Polling loop** (`_main_inner`, getUpdates — the ACTIVE production path): uses B-51 dedupe via `_buffer_media_group_msg` + `_flush_media_group` with a shared buffer and a 2s flush timer. ✅ instrumented by Task 5.
   - **Webhook path** (`process_update`, used only when `WEBHOOK_URL` set): has its OWN inline buffer (`{"msgs","last_seen"}`, NO dedupe) AND `webhook_reader_thread` calls `process_update(update)` **without passing a shared buffer** → the `media_group_buffer` param defaults to a **fresh `{}` every call** → each album photo lands in a throwaway dict and **is never flushed → total album loss in webhook mode.** Not the cause of the observed 5→2/5→3 (prod runs polling), but a real correctness gap. NOT fixed (out of Task 5 scope = behavior change). See Day 9 backlog P1.

   **🟡 NEW CRITICAL FINDING #2 — dedupe is append-time, not flush-time.** `_buffer_media_group_msg` drops duplicates *as they arrive* (keyed on `seen_uids`), so they never enter `msgs`. Implication: `buffer_size_after` in the logs already reflects the deduped count, and a too-aggressive `file_unique_id` collision silently discards a genuine photo at arrival — there is no flush-time reconciliation to recover it. If loss is dedupe-driven, the `SKIPPED duplicate` log line is the smoking gun (look for distinct intended photos sharing one `file_unique_id`).

2. **InsightFace face validator fallback** — InsightFace fails to load (ONNX Runtime / Python 3.14 issue), falls back to OpenCV Haar cascade with ~60% detection rate. Many photos rejected as "без лиц" even with faces. Need investigation.

3. **Watchdog/heartbeat false-positive** — `system_watchdog.py` reads heartbeat file, claims stale despite writer updating every second. Spams Telegram "Bot heartbeat stale" every minute when backend up. Workaround: keep backend DOWN at night. Need fix.

4. **ComfyUI cold-start failures** — pod spawns but ComfyUI doesn't bind 8188 (~10-15% rate). **FIXED Day 8 Task 1** via retry up to 3 attempts. Daniil's overnight #1 video was lost to this exact bug; with the fix, would have automatically recovered.

5. **Bootstrap drift** — `runpod/bootstrap.sh:47` clones Fannovel16 ComfyUI-Frame-Interpolation URL but pod runs ComfyUI-VFI (different repo). Hypothesis: clone skipped because dir pre-exists on network volume. Cosmetic, doesn't block work. Day 8+ task.

### ⚠️ Operational gotchas

- **Pod billing accumulates** — leave even idle pods running = $1.49/hr each. Always verify zero RUNNING pods at end of session.
- **Pod stop after each animation** — Day 5 fix `6b71d70` spawns fresh pod per generation, auto-stops. Don't manually keep `jarvis-m2-gen_*` pods running.
- **Telegram album max 10 photos per send** — our `MAX_TARGETS=20` allows multi-album accumulation, but single album hard-capped at 10.
- **Heavy workflow per video** — Wan 2.2 Remix NSFW dual-pass + RIFE = ~25-30 min per 10s @ 30fps video. Don't expect <5min generations.

---

## 📂 Key file paths (verbatim Windows)

### Code (mainly modified during Day 6-8)

```
C:\jarvis\app\services\block_m2_face_swap\
  ├── batch_orchestrator.py        # session FSM, MAX_TARGETS=20, custom prompts
  ├── face_swap_engine.py          # ReActor swap on RunPod
  ├── face_validator.py            # InsightFace + Haar fallback
  ├── cost_estimator.py            # "долго" warning for ≥2h batches
  ├── prompt_parser.py             # Day 6 custom prompt numbered list parser
  └── quality_settings.py          # Day 7 Task C duration + fps

C:\jarvis\app\services\block_m2_video\engines\
  ├── runpod_comfy_engine.py       # animation engine, _COLD_START_MAX_ATTEMPTS=3 (Day 8 Task 1)
  └── workflows\wan22_i2v_v20.json # node 7 prompt, node 21 VHS_VideoCombine, node 22 RIFE injected

C:\jarvis\app\services\block_m2_video\runpod\
  └── runpod_client.py             # spawn/stop/list pods, SUPPLY_CONSTRAINT retry (Day 5)

C:\jarvis\app\services\auth\        # Day 8 Task 2 NEW
  ├── __init__.py
  └── whitelist.py                  # load_admin_user_id, is_allowed, REJECT_MESSAGE

C:\jarvis\app\services\audit\       # Day 8 Tasks 3 + 4 ✅
  ├── __init__.py
  ├── audit_logger.py               # Task 3: per-day JSONL log + admin forwards
  └── cost_tracker.py               # Task 4: per-user spend, /my_stats + /admin_costs

C:\jarvis\app\handlers\
  └── face_swap_handler.py         # bot wiring, /swapbatch_*, set_quality

C:\jarvis\tools\
  └── jarvis_smart_telegram_control.py  # bot main; _whitelist_gate (T2), _audit_message (T3),
                                        #   _cost_command_intercept /my_stats /admin_costs (T4),
                                        #   _buffer_media_group_msg + _flush_media_group B-51 logs (T5)

C:\jarvis\runpod\
  └── bootstrap.sh                  # pod init, line 47 Fannovel16 URL but ComfyUI-VFI installed
```

### Tests (per-task)

```
C:\jarvis\tests\
  ├── test_runpod_comfy_engine.py            # Day 7 fps, Day 8 cold-start retry
  ├── test_swapbatch_orchestrator.py         # Day 6/7 features
  ├── test_swapbatch_handler.py
  ├── test_quality_settings.py               # Day 7 Task C
  ├── test_whitelist.py                       # Day 8 Task 2 (11 tests)
  ├── test_bot_whitelist_integration.py       # Day 8 Task 2 (4 tests)
  ├── test_audit_logger.py                    # Day 8 Task 3 ✅
  ├── test_bot_audit_integration.py           # Day 8 Task 3 ✅
  ├── test_cost_tracker.py                    # Day 8 Task 4 ✅ (15 tests)
  ├── test_bot_cost_commands_integration.py   # Day 8 Task 4 ✅ (4 tests)
  └── test_b51_media_group_dedupe.py          # Day 8 Task 5 ✅ (+5 diagnostic-log tests)
```

### State / Config (gitignored)

```
C:\jarvis\.env                                # SECRETS, has FACE_SWAP_POD_ID, ENABLE_FPS_INTERPOLATION=1, JARVIS_ADMIN_USER_ID
C:\jarvis\.env.example                        # tracked, documents new vars per task
C:\jarvis\state\face_swap\batches\{chat_id}\session.json  # persisted batch sessions
C:\jarvis\state\bot_heartbeat.txt             # watchdog reader (buggy)
C:\jarvis\state\audit\{YYYY-MM-DD}.jsonl      # Day 8 Task 3 will create
C:\jarvis\logs\jarvis.log                     # main backend
C:\jarvis\logs\bot_stdout.log, bot_stderr.log # bot
C:\jarvis\logs\sniper_*.log                   # GPU sniper script
```

### Docs

```
C:\jarvis\artifacts\obsidian_vault\03_Issues\
  ├── Jarvis-Day6-Handoff.md       # Day 6+7 detailed handoff (existing)
  └── Jarvis-Day8-Handoff.md       # THIS file
```

---

## 🎬 Day 6-7 commit chain (for context)

```
Day 7 fps interpolation:
  e5666cf docs(bootstrap): correct frame-interpolation pack reference
  eb17297 feat(video): enable fps interpolation (gate flipped on)
  8dbc2d3 fix(video): correct RIFE schema to match ComfyUI-VFI live pod

Day 7 Task C (variable duration + gated fps):
  3a97d9d b7a56fc 1a58eeb 4e83f16 3ce59cf  # (cluster — quality_settings, engine, orchestrator, handler)

Day 7 Task B (photo limit):
  aa38505 feat(face-swap): raise MAX_TARGETS 5→20 + "долго" warning

Day 7 Task A (post-swap menu):
  36f8dc0 fix(face-swap): post-swap menu shows all 4 branches

Day 6 (custom prompts):
  ef2fdab f7f2346 041f43d 1c1fd79 bd5aee5 4ea9272

Day 8 (COMPLETE):
  a8843d4 fix(video): retry ComfyUI cold start on fresh pod (max 3 attempts)
  77f346f feat(auth): add user whitelist for bot access control
  fd2fa17 feat(audit): per-day jsonl audit log + admin Telegram forwards
  f09417c docs(handoff): Day 8 brief for next Claude chat
  fd1b269 feat(cost): per-user cost tracking with /my_stats and /admin_costs
  8ceecc2 feat(bot): B-51 media_group dedupe diagnostic logging (no behavior change)
```

---

## 🔁 RunPod operational reference

### Spawn / stop / list pods (Python from C:\jarvis)

```powershell
cd C:\jarvis; .venv\Scripts\Activate.ps1

# List RUNNING pods
python -c "from app.services.block_m2_video.runpod.runpod_client import RunpodClient, get_runpod_config; import asyncio; c = RunpodClient(get_runpod_config()); pods = asyncio.run(c.list_pods()); running = [p for p in pods if p.desired_status == 'RUNNING']; print(f'RUNNING ({len(running)}):'); [print(f'  {p.id} | {p.name}') for p in running]"

# Stop a specific pod
python -c "from app.services.block_m2_video.runpod.runpod_client import RunpodClient, get_runpod_config; import asyncio; c = RunpodClient(get_runpod_config()); print(asyncio.run(c.stop_pod('POD_ID_HERE')))"

# Stop ALL running
python -c "from app.services.block_m2_video.runpod.runpod_client import RunpodClient, get_runpod_config; import asyncio; c = RunpodClient(get_runpod_config()); running = [p for p in asyncio.run(c.list_pods()) if p.desired_status == 'RUNNING']; [print(f'{p.id}:', asyncio.run(c.stop_pod(p.id))) for p in running]"
```

### Sniper for catching swap pod (when supply tight)

```powershell
# Start sniper background
$bot = Start-Process C:\jarvis\.venv\Scripts\python.exe -ArgumentList "scripts\runpod_gpu_sniper.py" -WorkingDirectory C:\jarvis -RedirectStandardOutput C:\jarvis\logs\sniper_stdout.log -RedirectStandardError C:\jarvis\logs\sniper_stderr.log -WindowStyle Hidden -PassThru
```

### Bot restart sequence

```powershell
Get-WmiObject Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*jarvis_smart_telegram_control*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 2
Get-Content C:\jarvis\.env | % { if ($_ -match '^([^#][^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') } }
Remove-Item C:\jarvis\logs\bot_*.log -EA SilentlyContinue
$bot = Start-Process C:\jarvis\.venv\Scripts\python.exe -ArgumentList "tools\jarvis_smart_telegram_control.py" -WorkingDirectory C:\jarvis -RedirectStandardOutput C:\jarvis\logs\bot_stdout.log -RedirectStandardError C:\jarvis\logs\bot_stderr.log -WindowStyle Hidden -PassThru
Write-Host "Bot PID $($bot.Id)" -F Green
```

### Backend usually stays DOWN at night

Watchdog has bug — spams "Bot heartbeat stale" every minute when backend up. Backend NOT needed for swap+animate (in-process). Only enable if explicit feature requested.

---

## 💡 Glossary

- **напарник** — "partner". Daniil's term for Claude in this collab. Use sparingly but acknowledge.
- **Jarvis V3** — the project. V1/V2 were earlier iterations.
- **Block M.2.5** — face swap + animation pipeline (current production).
- **persona** — AI character (e.g. "Вера", `persona_af2f54ee`) trained on FLUX LoRA.
- **swap pod** — long-lived pod for face swap (`FACE_SWAP_KEEP_POD_RUNNING=1`).
- **gen pod** — short-lived pod spawned per animation, auto-stopped (Day 5 fix).
- **sniper** — `runpod_gpu_sniper.py` script that polls for available A100 80GB and reserves one.
- **B-XX** — bug tag (e.g. B-51 = media_group dedupe). Tracked in code TODOs.

---

## 🎯 Next actions when resuming (Day 9 kickoff)

Day 8 is closed — all 5 tasks shipped. Start Day 9 by deciding whether to keep
hardening reliability (B-51 family) or pivot to new features. **Don't run
parallel CC sessions** (see lesson learned above). Open with a greeting to
Daniil as напарник and confirm the Day 9 priority order below.

### Day 9 backlog (prioritized by what Task 5 logging revealed)

| P | Item | Why now | Effort |
|---|------|---------|--------|
| **P0** | **Reproduce a B-51 loss with the new logs** | Task 5 instrumentation is live but unproven. Have Daniil send a known-count album (e.g. 5 photos); grep `media_group:` in `logs/jarvis_bot.log`; read off which branch fired (`NEW media_group_id` / `gap_since_last>2s` / `SKIPPED duplicate`). This DECIDES every fix below — don't guess, let the log say. | 15 min + 1 album |
| **P1** | **Fix webhook-path total-loss bug (Finding #1)** | If the bot ever runs in webhook mode (`WEBHOOK_URL` set), albums are silently 100% lost — `webhook_reader_thread` passes no shared buffer to `process_update` and that path never flushes. Fix: thread a persistent buffer + flush loop into the webhook reader, OR route webhook media groups through the same `_buffer_media_group_msg`/`_flush_media_group` as polling. **TDD; this is a behavior change so it was out of Task 5 scope.** | ~1-2h |
| **P2** | **B-51 root-cause fix — branch chosen by P0 evidence** | • Split-album (`NEW media_group_id`) → re-key buffer on `chat_id`+time-window, merge groups within the flush window. • Flush-too-early (`gap_since_last`>2s) → raise/make-adaptive the hardcoded `2.0`s threshold (`_main_inner` ~line 5560). • Over-aggressive dedupe (`SKIPPED duplicate` of distinct photos, Finding #2) → add a secondary discriminator (file_size/dims) before skipping, since dedupe is append-time with no flush-time recovery. | ~1-3h depending on branch |
| **P3** | **InsightFace validator fallback** (~60% Haar detection) | Pre-existing; rejects real faces as "без лиц". Independent of B-51 but high user-facing pain. ONNX Runtime / Python 3.14 load failure. | investigation-first |
| **P4** | **Watchdog/heartbeat false-positive** | Spams "Bot heartbeat stale"; forces backend-DOWN-at-night workaround. Quality-of-life. | ~1h |
| **P5** | **Promote B-51 logs to structured JSON** (optional) | If grep-by-eye proves clumsy during P0, mirror the `audit_logger` one-JSON-line-per-event format for machine-parseable post-mortems. Only if P0 shows the need. | ~30 min |

**Suggested Day 9 opener:** P0 first (cheap, unblocks everything), then P1 (clear-cut correctness bug, no waiting on Daniil), then P2 once P0 evidence is in hand.

---

## 🤝 Communication tips

- **Long answers OK when planning/explaining** — Daniil reads thoroughly when context warranted.
- **Short answers preferred for execution** — quick PS one-liner, decision-tree, "do this now" works better than essay.
- **Confirm key decisions before executing** — e.g. "Option A or B?" rather than assume.
- **Honest about risks** — Daniil appreciates real talk about cost, time, what could fail.
- **Celebrate ship moments** — commit pushed = real milestone. Acknowledge.
- **Empathetic about hours** — if it's 02:00+ his time, suggest wrap if appropriate.
- **Don't ask too many clarifying questions** — read context, propose default with rationale, let him correct if wrong.
- **Use bullets/tables/PS code blocks heavily** — visual structure helps Russian + technical dense info.
- **Emojis used regularly** — 🔴 🟡 🟢 ✅ ⚠️ 🚀 for status, but don't overdo.

---

**End of Brief**

If you're a new Claude reading this — Day 8 is ✅ COMPLETE (all 5 tasks shipped, HEAD `8ceecc2`). Greet Daniil as напарник, confirm the Day 9 priority order (P0: reproduce a B-51 loss with the new logs → P1: webhook-path total-loss fix → P2: B-51 root-cause fix), and continue execution. Welcome to the project. 🤝
