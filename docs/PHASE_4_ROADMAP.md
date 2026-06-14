# Jarvis V3 — Phase 4 Roadmap (Unified Architecture)

| | |
|---|---|
| **Date** | 2026-06-14 (updated after Step 2.7) |
| **Branch** | `phase-4.0-unified-jarvis` |
| **HEAD** | `f9e52e6` (feat(unified): Phase-4 Step 2.7 — harden LLM router for prod dogfooding) |
| **Status** | 🟡 **Foundation built + hardened, still "dark"** — the unified LLM router is REAL, unit-tested, and now production-hardened (history + retry + backends), but `JARVIS_ROUTER_ENABLED` is still unset in prod. **Next gate is Daniil flipping the flag and dogfooding** (see §11). |

> Audience: a fresh Claude Code session (or Daniil) should be able to read this top-to-bottom and immediately know where the project stands and what to do next. Statuses below reflect the **current code** (post-`f9e52e6`), not the stale Day-8 handoff docs.

---

## 1. TL;DR verdict

- ✅ The Phase-4 foundation (unified router, tool registry, voice in/out) is **real working code** that makes genuine Anthropic/OpenAI calls and is **well covered by unit tests**. As of Step 2.7 (`f9e52e6`) it is also **production-hardened** — per-chat history, retry/backoff with graceful degradation, and wired backends.
- 🔴 **But it is still not switched on.** In production the bot runs polling with `JARVIS_ROUTER_ENABLED` unset, so all text still flows through the legacy `handle()` dispatcher. The "LLM router dispatches to tools" centerpiece has effectively **never run in prod** — the code is ready; the flag is off.
- ⚠️ **The biggest risk is building Steps 3–8 (all "new tools under the router") on top of a router that has never been battle-tested in production.**
- 👍 The **architecture is sound** (capabilities register as tools in a single registry; extensions plug in without rewrites). The **ordering** needs one inserted prerequisite (activate + harden the router) and two reorderings (do the easy, low-risk steps first).

**The single most important next action: turn the router on and prove it in prod. Everything else is downstream of that.**

---

## 2. Current status — what's done / what's stub

| Step | Commit | Status | Notes |
|---|---|---|---|
| ✅ Step 1 — LLM router + tool registry | `a04b08c` | **REAL, but dark** | Genuine Anthropic tool_use loop; OFF by default (`JARVIS_ROUTER_ENABLED=0`). |
| ✅ Step 2 — Voice in/out (Whisper + TTS) | `e5cec95` | **REAL, opt-in** | OpenAI `whisper-1` transcription; `tts-1` reply gated by `JARVIS_VOICE_REPLY_ENABLED=0`. |
| ✅ Step 2.5 — Collapse poll loop → `process_update` | `599ced8` | **DONE** | Poll loop + webhook reader now share one dispatch chokepoint. |
| ✅ Step 2.6 — Webhook reader fix | `55aa683` | **DONE** | Persistent media_group buffer + 2 s stale-flush. |
| ✅ Step 2.7 — Harden router (P0) | `f9e52e6` | **CODE DONE — awaiting prod dogfood** | Conversation-history persistence, retry/backoff + graceful degradation, backends wired (stats real / persona stub), pricing verified. Router still OFF by default; Daniil flips `JARVIS_ROUTER_ENABLED=1` to dogfood (§11). |

**Stubs (shape only, not implemented)** — `app/services/unified/llm_router/tools/stubs.py`, **not registered** so Claude is never offered them:

| Stub tool | Target phase | Returns |
|---|---|---|
| `computer_use` | Phase B (Step 3) | "ещё не реализован (Phase B)" |
| `office_file` | Phase C (Step 4) | "ещё не реализован (Phase C)" |
| `live_face_swap` | Phase D (Step 6) | "ещё не реализован (Phase D)" |

---

## 3. Known gaps in the foundation (implemented-code, not features)

Status after Step 2.7 (`f9e52e6`) — three of five closed:

- ✅ **Router history persistence** — *closed in 2.7.* `_run_router` now threads a per-chat in-memory history (last 10 messages, user-first pairs) into `route_message` and records each successful turn.
- ✅ **Retry/backoff** — *closed in 2.7.* `_create_message` retries transient errors (429/408/409/5xx/timeout) with bounded exponential backoff (3 attempts); 4xx/auth raise immediately; on exhaustion `route_message` returns a graceful `RouterResponse(error=…)` and the bridge falls back to legacy `handle()`.
- ✅ **Backends wired** — *closed in 2.7.* `register_default_tools` now receives `stats_fn` (REAL — the `/my_stats` formatter) and `persona_generate_fn` (explicit graceful stub). Real `block_m1_persona.PhotoGenerator` wiring is a documented follow-up (needs Replicate client/storage/tracker + count→loop adapter).
- 🔴 **Tool results are text-only** (`tool_registry.to_tool_content`). No image-in-tool-result path → **directly blocks Computer Use** (screenshots must return as images). *(Still open — gates Step 3.)*
- 🟡 **Router limits are modest** — `max_iterations=6`, `max_tokens=1024`. Step 2.7 made these tunable but **did not raise the defaults** (out of its 4-item scope); long/agentic answers may still truncate. Revisit when dogfooding shows truncation, or before Step 3.

---

## 4. Technical debt (prioritized)

| Item | Severity | Detail |
|---|---|---|
| **Three LLM-routing layers** | 🟡 Med | `unified/llm_router/` (canonical) + legacy `app/services/llm_router.py` (Ollama, **still imported** by `dashboard_service.py` + `supervisor_core.py`) + dead `app/services/llm_client.py` (non-existent `client.responses.create()`, fallback model `gpt-5.2`, imported nowhere). |
| **Config drift** | 🟡 Med | ~654 scattered `os.getenv()` across 94 files; `app/settings.py` covers ~15. No single source of truth. |
| **Model-id drift** | 🟡 Med | `claude-sonnet-4-6`, `claude-sonnet-4-20250514`, `claude-sonnet-4-5`, `gpt-5.2` (dead), `gpt-4.1` hardcoded in different files. Consolidate + verify vs current catalog. |
| **Port inconsistency** | 🟢 Low | `settings.py` default `8015` vs `.env`/bot `8010`. |
| **Router pricing entry** | ✅ Resolved (2.7) | **Verified correct against the current catalog — no change.** `claude-opus-4-8` is genuinely $5/$25, `claude-sonnet-4-6` $3/$15, `claude-haiku-4-5` $1/$5; `MODEL_PRICING` already matches. The earlier "$15/$75" concern was stale (older Opus generations). |
| **Legacy dispatch adapter** | 🟢 Low | ~200-line router↔`handle()` bridge in the bot; transitional, fine for now. |
| **Legacy `voice_input.py`** | 🟢 Low | Superseded by `unified/voice/`; poll-loop usage removed in `599ced8` → now effectively dead. |

---

## 5. Known bugs (current status)

| Bug | Status | Detail |
|---|---|---|
| Webhook media-group total-loss | ✅ **FIXED** (`55aa683`) | Persistent buffer + stale-flush. *(Day-8 docs that call this "open" are stale.)* |
| B-51 media-group duplication | 🟡 **Partially mitigated** | Dedupe-by-`file_unique_id` + diagnostic logging shipped. **Root cause pending P0 evidence** (split-album vs flush-timer vs over-aggressive dedupe). |
| InsightFace ~60% fallback | 🔴 **OPEN, high pain** | ONNX Runtime / Python 3.14 incompat → OpenCV Haar fallback rejects ~40% of real faces as "без лиц". Gates Step 6; also hurts current batch UX. |
| Watchdog heartbeat false-positive | 🟢 Cosmetic | Spammy stale alerts; mitigated by keeping backend down at night. |
| Bootstrap drift (Fannovel16 vs ComfyUI-VFI) | 🟢 Cosmetic | Live pods serve the correct node; only the from-scratch bootstrap URL is confusing. |

---

## 6. Reordered roadmap

Effort key: **S** = hours–1 day · **M** = days · **L** = week+ · **L+** = multi-week / subsystem.

### 🔴 P0 — close before ANY new feature (the foundation is dark)

| Item | Effort | Status / Why |
|---|---|---|
| ✅ **Step 2.7 — Harden the router** | **M** | **CODE DONE (`f9e52e6`).** History persistence + retry/backoff + graceful degradation + backends wired + pricing verified. **Remaining:** (a) Daniil **flips `JARVIS_ROUTER_ENABLED=1` and dogfoods** (§11) — the real "prove it in prod" gate; (b) raise `max_tokens` default if dogfooding shows truncation (deferred from 2.7's scope). |
| ⬜ **Polling-loop resilience tests** | **S–M** | Still open. `_main_inner` `while True` is the one production path with **0 coverage** (getUpdates errors, offset recovery, retry). `process_update` is well tested; the I/O loop is not. |

### 🟡 P1 — cheap, high-leverage cleanup (run in parallel with P0)

| Item | Effort | Why |
|---|---|---|
| **Single source of truth for LLM routing** | **M** | Quarantine legacy `llm_router.py` (Ollama), delete dead `llm_client.py` / `gpt-5.2`. |
| **Config + model-id consolidation** | **M** | Ports, scattered `getenv`, model IDs → one settings layer. |
| **Decision: missions-stack vs unified-router** | **S** (decision) | Written architecture call. **Blocks Step 7.** See §9. |

### 🟢 Features — reordered by risk

| Step | Effort | Risk | Notes |
|---|---|---|---|
| **Step 4 — Office tools** (xlsx/docx/pptx) | **M** | Low | Fastest real win. Mature libs (openpyxl/python-docx/python-pptx) + reuse existing `spreadsheet_service.py` + `file_parsers.py`. Mostly: wire a real `office_file` handler. |
| **Step 5 — Web panel** | **M** | Low–Med | **Big head start, not greenfield.** `jarvis_dashboard_router.py` + `app/static/dashboard.html` + `dashboard_service.py` already serve `/dashboard`, `/api/status`, `/api/chat`, `/api/analytics`, `/api/export`; ~200 backend endpoints exist. Reframe as *enhance/secure existing dashboard*. Auth today is just `chat_id`; WebSocket push требует уточнения. |
| **Step 8 — MCP server** (Gmail/Notion/Slack/Drive) | **M–L** | Med | Groundwork exists (`test_mcp_server.py`, `figma_client` MCP, `google_workspace_*` OAuth). Tool-registry makes "plug in as tools" clean — **once the router is on**. Mostly OAuth/integration glue. |
| **InsightFace / ONNX fix** | **M** | — | Pull **before** Step 6; also fixes current batch UX pain (~60% fallback). |
| **Step 3 — Computer Use** | **L** | High | After router hardening + **image tool_results**. Needs a dedicated agent loop (not the `max_tokens=1024`/6-iter message router), screenshot+action executor, and it controls the *same Windows PC the bot runs on* → real safety surface. |
| **Step 7 — Long autonomous tasks** | **L** | High (architectural) | After the P1 missions-stack decision. Overlaps a large existing stack (`goals_router`, `missions`, `resume_recovery`, `night_workflows`, `agent_control_plane`). The real work is *reconciliation*, not greenfield. |
| **Step 6 — Deep-Live-Cam** (live face swap) | **L+** | Very high | Treat as a **de-risking spike, not a linear step.** Real-time ≠ the current batch RunPod/ComfyUI pipeline (pods + queue + history poll = seconds–minutes latency). Needs low-latency GPU, webcam capture, virtual-cam output, and a **working InsightFace**. Gated on the InsightFace fix. |

---

## 7. Dependencies the original plan missed

- **Router *activation* gates Steps 3, 4, 8** — hardening is done (2.7); the remaining gate is flipping the flag and proving it in prod.
- **Image tool_results** are needed for Step 3 (Computer Use screenshots; also useful for vision). *(Still open.)*
- **InsightFace fix gates Step 6** (and improves today's batch quality).
- ✅ **Conversation-history persistence** — done in 2.7; no longer blocks Steps 3 / 7.
- **The missions-stack decision blocks Step 7** — two parallel autonomy systems is the real trap.

```
Step 2.7 hardened ✅ ──(flip flag + dogfood, §11)──┬─> Step 4 Office
                                                   ├─> Step 8 MCP
                                                   ├─> [+ image tool_results ⬜] ─> Step 3 Computer Use
                                                   └─> [history persistence ✅]  ─> Step 3, Step 7

InsightFace/ONNX fix ─> Step 6 Deep-Live-Cam
Missions-vs-router decision ─> Step 7 Long autonomous
```

---

## 8. ⚠️ HUMAN DECISIONS required (Daniil)

| # | Decision | Context |
|---|---|---|
| ✅ **D1** | ~~Router pricing entry~~ — **RESOLVED in 2.7.** | Verified against the current catalog: `claude-opus-4-8` $5/$25, `claude-sonnet-4-6` $3/$15, `claude-haiku-4-5` $1/$5 — `MODEL_PRICING` already correct, no change made. |
| ⚠️ **D2** | **Missions-stack vs unified-router** — integrate or replace? | The repo already has a large autonomy stack (`goals_router` / `missions` / `night_workflows` / `agent_control_plane` / `resume_recovery`). The unified-router vision re-approaches "autonomous tasks" from a different angle. **Decide before Step 7** — building a second autonomy system without reconciling the first is the plan's biggest architectural risk. |

---

## 9. Honest assessment — where the plan was naive / where it's easier

**Naive / under-scoped:**
- **Step 6 (Deep-Live-Cam)** — a single roadmap line that is actually a real-time-video subsystem sharing almost nothing with the batch pipeline. The `live_face_swap(source)` stub massively understates it.
- **Step 3 (Computer Use)** — understates the agent-loop + image-tool-result + safety work; it's not just "register a tool."
- **Step 7 (Long autonomous)** — understates that a half-built autonomy stack already exists and must be reconciled, not ignored.

**Easier than it looks:**
- **Step 4 (Office)** — mature libraries + existing `spreadsheet_service.py` / `file_parsers.py` to reuse.
- **Step 5 (Web panel)** — substantial existing dashboard + ~200 endpoints; enhance, don't rebuild.

**The one thing that matters most:** activate the router and prove it in production. Steps 3–8 are all downstream of a working, battle-tested router — keep that as the gate.

---

## 10. Quick reference — key paths

| Area | Path |
|---|---|
| Unified router | `app/services/unified/llm_router/router.py`, `llm_client.py`, `tool_registry.py` |
| Router tools | `app/services/unified/llm_router/tools/` (`persona_photo`, `swap_batch`, `cost_stats`, `stubs`) |
| Voice | `app/services/unified/voice/transcribe.py`, `synthesize.py` |
| Bot dispatch | `tools/jarvis_smart_telegram_control.py` → `process_update` (chokepoint), `_run_router`, `_route_voice` |
| Backend / endpoints | `app/main.py`, `app/routers/`, `app/api/` |
| Existing dashboard (Step 5 reuse) | `app/routers/jarvis_dashboard_router.py`, `app/static/dashboard.html`, `app/services/dashboard_service.py` |
| Existing autonomy (Step 7 / D2) | `app/api/goals_router.py`, `app/api/missions.py`, `app/services/night_workflows.py`, `app/routers/agent_control_plane.py`, `app/services/resume_recovery.py` |
| Router enable flags | `JARVIS_ROUTER_ENABLED` (default 0), `JARVIS_VOICE_REPLY_ENABLED` (default 0), `JARVIS_ROUTER_MODEL` |
| Step 2.7 tests | `tests/test_router_hardening.py` (retry · history · backends) |

---

## 11. ⚠️ Daniil — turn the router on & dogfood it

Step 2.7 hardened the router but left it **OFF**. To run the "prove it in prod" gate:

**1. Env (startup):**
```
JARVIS_ROUTER_ENABLED=1          # ON
ANTHROPIC_API_KEY=sk-ant-...     # required, else silent fallback to legacy
JARVIS_ROUTER_MODEL=claude-opus-4-8   # optional; default claude-sonnet-4-6
```
Restart the bot. Missing key/SDK → `[router] build failed, using legacy dispatcher` and everything still works via `handle()`.

**2. Telegram test phrases** (plain text — `/commands` always bypass the router):

| Test | Send | Expect |
|---|---|---|
| Stats tool (REAL) | `сколько я потратил?` | Same table as `/my_stats` |
| Persona stub | `сгенерируй фото персоны X на пляже` | Graceful "ещё не подключена…" — not a crash |
| Plain chat | `привет, как дела?` | Short text reply, no tool |
| **History** | `запомни число 7` → `какое число я просил запомнить?` | Second reply references **7** |
| Resilience | rapid-fire several messages | No crash on transient API error; hard failure → silent legacy fallback |

**3. Watch logs:** `[router] route_message failed, falling back` (clean degradation) + per-call cost/audit entries.

**4. If dogfooding shows truncation/verbosity:** raise `max_tokens` / tune `effort` and the system prompt in `router.py` (the deferred 2.7 sub-item).

---

*Generated from the 2026-06-14 read-only architecture audit + plan validation; updated after Step 2.7 (`f9e52e6`). Code facts cited; items marked "требует уточнения" / "⚠️ HUMAN DECISION" need confirmation before acting.*
