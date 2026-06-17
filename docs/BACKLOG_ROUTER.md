# Router / Bot cleanup backlog (deferred — do NOT do during the hybrid-tools work)

These items were found during the read-only capability audit (2026-06-16). They are
intentionally OUT OF SCOPE for the router-hybrid-tools plan
(`docs/superpowers/plans/2026-06-16-router-hybrid-tools.md`). Each needs its own task.

## Dead-code duplicate command handlers (tools/jarvis_smart_telegram_control.py)
The dispatcher is an `if cmd == ...: return` chain, so an earlier branch shadows any
later one with the same command name:
- `/cancel` — branch at line ~4370 wins; `cmd_cancel` at ~4570 is unreachable.
- `/history` — branch at line ~4326 wins; persona `handle_history` at ~4882 is unreachable.
- `/lora_status` — persona branch ~4633 wins; Photo Studio `_ps_cmd_map` version ~4958 is unreachable.

Action (later): remove the shadowed branches; add a regression test asserting each
command resolves to exactly one handler. Do NOT touch during hybrid-tools work.

## Old Ollama router (app/services/llm_router.py)
- Single-file `route_message` on `llama3.2` (NOT the unified `app/services/unified/llm_router/` package).
- NOT used by the Telegram bot. Importers: `app/services/supervisor_core.py`, `app/services/dashboard_service.py`.

Action (later): confirm those two callers, then either delete or move under a clearly
named legacy module. Out of scope for router-hybrid work.

## Priority-2 router tools (separate plan, after enable)
- `brain_plan` (/brain) — ⚠️ templated plan + embedded web research; not a real autonomous analysis.
- `engineer_review` (/engineer) — ⚠️ templated plan/recommendation; `safe_to_auto_apply:false` always.
- `create_reminder` (/remind) — NL reminder scheduling.

Until added as tools, they remain command-only and are described honestly in the
router system prompt (see plan Stage 5).

## Media-group photo loss in swapbatch (NOT a router bug)
Found during the router enable-smoke (2026-06-16), test 6a. Sent 20 target photos as a
Telegram album; the bot reported "Получено 10 файлов" and only 5 reached the batch
estimate. Album messages arrive as a `media_group` of separate updates and some are
being dropped — looks like the media-group dedupe path (cf. backlog item **B-51**).

Scope: this is the legacy file/photo intake path, independent of the LLM router
(routing to `swap_batch_start_source` worked correctly). Action (later): trace the
media-group collection in the photo handler, confirm against B-51, add a test that an
N-photo album yields N targets. Out of scope for router-hybrid work.

## OpenAI API key invalid (401) — voice in/out blocked (NOT a router bug)
Found during router enable-smoke (2026-06-16), test 7 (voice). Voice-in failed with
`❌ Ошибка Whisper: 401 - Incorrect API key provided sk-svcac...KpMA`. Both Whisper
(speech-to-text, voice-in) and TTS (voice-out, `reply_with_voice`) run on OpenAI, so a
dead `OPENAI_API_KEY` blocks both. Suspected invalid in prior sessions — now confirmed.

Scope: external dependency / secrets, NOT the router (routing logic for voice was never
reached). Action (later): obtain and set a valid `OPENAI_API_KEY`, then re-run smoke
test 7 (7a voice-in → e.g. `web_research`; 7b "ответь голосом" → `reply_with_voice`).
Smoke test 7 is recorded as BLOCKED, not a router failure.

## Orphan bootstrap duplicate: runpod/bootstrap.sh (NOT a router item)
Found 2026-06-16 during the video-swap occlusion work. There are TWO bootstrap scripts:
- `scripts/remote/bootstrap_pod.sh` — the REAL one. The RunPod template's startCmd runs
  it as `/workspace/bootstrap.sh` from the network volume (see
  docs/runpod_template_config.md); deployed via `scripts/deploy_patch_pod.py` + manual SSH.
- `runpod/bootstrap.sh` — referenced nowhere in code/docs; uses different conventions
  (`COMFY_DIR`, `_clone_node`, no version gate, no ComfyUI launch). Effectively dead.

RESOLVED 2026-06-17 (commit 828b2c3): confirmed `runpod/bootstrap.sh` was truly unused and
deleted it. It had no unique custom-node clones worth folding into the real script. It was a
trap — editing it had no effect on real pods. Was out of scope for the occlusion feature.
