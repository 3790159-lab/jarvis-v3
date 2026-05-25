# Jarvis Day 5 Handoff

Created May 25-26 2026 (evening through late night).

## Shipped today
- **B-48 verified** in production — podExec removal works, no GraphQL 400 noise on cold start
- **B-50 verified** — orchestrator dedupe holding, no batch target replication  
- **B-51 verified** — bot wiring transport dedupe shipped (commit 0bd8ddc), Day-5 test showed 5 incoming = 4 batch targets (1 source + 4 unique targets)
- **B-51 investigation document** committed (4370997) — full mechanism breakdown
- **Animation pipeline fixed** (commit 6b71d70) — skip resumed EXITED pods + sniper-style supply retry. First end-to-end successful test: 3 swap + 3 animation videos generated.
- **SSH push setup** — switched origin to SSH (git@github.com), key added to GitHub (Jarvis Desktop)

## Status
- HEAD: 6b71d70 on phase-3.0-inventory-stop-reliability, synced with origin
- All pods EXITED (billing zero)
- Phase 3.0 (inventory + stop reliability) effectively complete

## Open for Day 6+
- **Animation prompt UX** — main next feature. Three design options on table (extended command / new stateful command / both). Touches: face_swap_handler.py, batch_orchestrator.confirm_animate, runpod_comfy_engine.generate, workflow_v20.json text encoder node, bot wiring state machine.
- **Face validator quality** — InsightFace falls back to Haar cascade (sees 3/5 faces in similar photos). Fix: investigate why InsightFace unavailable, repair pip install / ONNX Runtime config.
- **B-51 latent webhook bug** (docs/B-51_INVESTIGATION.md §6) — webhook-mode process_update creates throwaway buffer, never flushed. Out of B-51 scope but tracked.
- **Untracked file cleanup** — стрэ-файл `)`, docs/*20260520*.md, scripts/*.py untracked, могут быть в gitignore или commit'нуты.

## Notes
- credential.credentialStore = cache stays in global gitconfig (broken on Windows) — SSH path used instead, fine to leave as-is or fix later via `git config --global credential.credentialStore wincredman`
- start_jarvis_session.ps1 not saved (placeholder accidentally got into file, was removed). The inline block is in shell history; can be saved properly tomorrow.

## Day 6 start
1. ssh jarvis-desktop
2. paste stack startup block from Day 5 chat history (or recreate the .ps1)
3. wait for sniper catch + ComfyUI ready alert
4. design + implement animation prompt UX (Option A/B/C decision first)
