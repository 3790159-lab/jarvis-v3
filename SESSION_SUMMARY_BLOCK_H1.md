# Session Summary — BLOCK H1: Critical Hotfixes

**Date**: 2026-05-01  
**Commits**: H1.2 → H1.5 (4 phases, 37 new tests)

---

## Bugs Fixed

### BUG #1 — Replicate Image Generation HTTP 500 (Phase H1.2)
**Root cause**: Wrong API endpoint + wrong payload format.
- Was: `POST /v1/predictions` with `{"version": "black-forest-labs/flux-1.1-pro", "input": {...}}`
- Replicate requires SHA hash in `"version"`, not a model name string
- Fix: Model-level endpoint `POST /v1/models/black-forest-labs/flux-1.1-pro/predictions` with `{"input": {...}}` only (no `"version"` field)
- Also added `.env` auto-reload so `REPLICATE_API_KEY` is picked up without restart

### BUG #3 — Obsidian Button Returns 404 (Phase H1.3)
**Root cause**: Router existed in tools but was never registered in `app/main.py`.
- Created `app/routers/obsidian_save_router.py` with:
  - `POST /api/jarvis/tools/obsidian/save` — write markdown to vault
  - `GET /api/jarvis/tools/obsidian/health` — check vault path config
- Registered in `app/main.py`

### BUG #4 — Dashboard Chat Timeout (Phase H1.4)
**Root cause**: `_process_query` used blocking `urllib.urlopen(timeout=60)` directly in async handler, blocking the event loop.
- Fix: Two-stage approach with `asyncio.wait_for` + `run_in_executor`:
  1. Try `quick_answer()` first (Claude Haiku, 8s timeout) for simple factual questions
  2. Fall back to research endpoint (30s timeout)
  3. Return `"⏱ Запрос занял >30s"` on timeout
- JS: `AbortController` with 35s client-side timeout

### BUG #2 — Backend Crashes (Phase H1.5)
**Root cause**: Multiple contributing factors:
- `_get_status()` read `decisions.jsonl` + `errors.log` every 5s **per WebSocket client** — O(N×M) reads under load
- No WebSocket connection limit — unlimited concurrent connections possible
- No periodic state file cleanup — files could grow unbounded
- Fix:
  - `_get_status_cached()` with 10s TTL (all WS clients share one read per 10s)
  - `_WS_MAX_CONNECTIONS = 10` with counter + `finally` decrement
  - `_periodic_cleanup()` async task started on startup: hourly `cleanup_old_logs` + `archive_old_decisions`

---

## Test Coverage Added

| Phase | Tests | Coverage |
|-------|-------|----------|
| H1.2 | 18 | Replicate URL, payload, polling, HTTP errors |
| H1.3 | 13 | Obsidian save, health, folder creation, sanitization |
| H1.4 | 12 | quick_answer path, research fallback, timeout, error |
| H1.5 | 12 | Cache TTL, WS limit, cleanup functions, startup task |
| **Total** | **55** | |

---

## Files Changed

- `app/services/replicate_image_gen.py` — FIXED URL + payload
- `app/routers/obsidian_save_router.py` — NEW
- `app/routers/jarvis_dashboard_router.py` — async chat + cache + WS limit
- `app/main.py` — .env reload + obsidian router + periodic cleanup task
- `tests/test_replicate_image_gen.py` — updated for new constants
- `tests/test_obsidian_save.py` — NEW
- `tests/test_dashboard_chat.py` — NEW
- `tests/test_stability_fixes.py` — NEW
