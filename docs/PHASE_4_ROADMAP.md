# Jarvis V3 — Phase 4 Roadmap (Unified Architecture)

| | |
|---|---|
| **Date** | 2026-06-14 |
| **Branch** | `phase-4.0-unified-jarvis` |
| **HEAD** | `55aa683` (fix(webhook): persistent media_group buffer + stale-group flush) |
| **Status** | 🟡 **Foundation built but "dark"** — the unified LLM router is REAL and unit-tested, but `JARVIS_ROUTER_ENABLED=0` in prod, so it has never routed a real user message. |

> Audience: a fresh Claude Code session (or Daniil) should be able to read this top-to-bottom and immediately know where the project stands and what to do next. Statuses below reflect the **current code** (post-`55aa683`), not the stale Day-8 handoff docs.

---

## 1. TL;DR verdict

- ✅ The Phase-4 foundation (unified router, tool registry, voice in/out) is **real working code** that makes genuine Anthropic/OpenAI calls and is **well covered by unit tests**.
- 🔴 **But it is not switched on.** In production the bot runs polling + `JARVIS_ROUTER_ENABLED=0`, so all text still flows through the legacy `handle()` dispatcher. The "LLM router dispatches to tools" centerpiece has effectively **never run in prod**.
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

**Stubs (shape only, not implemented)** — `app/services/unified/llm_router/tools/stubs.py`, **not registered** so Claude is never offered them:

| Stub tool | Target phase | Returns |
|---|---|---|
| `computer_use` | Phase B (Step 3) | "ещё не реализован (Phase B)" |
| `office_file` | Phase C (Step 4) | "ещё не реализован (Phase C)" |
| `live_face_swap` | Phase D (Step 6) | "ещё не реализован (Phase D)" |

---

## 3. Known gaps in the foundation (implemented-code, not features)

These are gaps **inside code that exists today** — they must be closed before the dependent steps:

- 🔴 **Router is stateless.** `route_message` accepts `conversation_history` but the bridge (`_run_router`) never passes or persists it — every message starts fresh. *(Blocks multi-turn: Steps 3, 7.)*
- 🔴 **No retry/backoff** around `messages.create` in `router.py`. A transient API error propagates out of `route_message` (only per-*tool* execution is guarded).
- 🔴 **Tool results are text-only** (`tool_registry.to_tool_content`). No image-in-tool-result path → **directly blocks Computer Use** (screenshots must return as images).
- 🟡 **Bridge wires only 2 of 4 injectable backends** — `register_default_tools(..., dispatch_fn, set_quality_fn)`; `persona_generate_fn` and `stats_fn` are **not** passed. `get_user_stats` has a real default backend, but `generate_persona_photo` would **raise** (`_default_generate`) if the router ever called it. *(требует уточнения: intentional?)*
- 🟡 **Router limits are modest** — `max_iterations=6`, `max_tokens=1024`; long/agentic answers may truncate.

---

## 4. Technical debt (prioritized)

| Item | Severity | Detail |
|---|---|---|
| **Three LLM-routing layers** | 🟡 Med | `unified/llm_router/` (canonical) + legacy `app/services/llm_router.py` (Ollama, **still imported** by `dashboard_service.py` + `supervisor_core.py`) + dead `app/services/llm_client.py` (non-existent `client.responses.create()`, fallback model `gpt-5.2`, imported nowhere). |
| **Config drift** | 🟡 Med | ~654 scattered `os.getenv()` across 94 files; `app/settings.py` covers ~15. No single source of truth. |
| **Model-id drift** | 🟡 Med | `claude-sonnet-4-6`, `claude-sonnet-4-20250514`, `claude-sonnet-4-5`, `gpt-5.2` (dead), `gpt-4.1` hardcoded in different files. Consolidate + verify vs current catalog. |
| **Port inconsistency** | 🟢 Low | `settings.py` default `8015` vs `.env`/bot `8010`. |
| **Router pricing entry** | ⚠️ Decision | `claude-opus-4-8` priced $5/$25 per 1M (`unified/llm_router/llm_client.py:25`) — **below published Opus list pricing**. Only affects the router's own cost estimate, and only if model is overridden off Sonnet. See §9. |
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

| Item | Effort | Why |
|---|---|---|
| **Step 2.7 — Activate + harden the router in prod** | **M** | Turn on behind the flag and dogfood. Add conversation-history persistence + retry/backoff; raise `max_tokens`; wire (or deliberately stub) `persona_generate_fn`/`stats_fn`. **This is the inserted prerequisite the original plan lacked.** |
| **Polling-loop resilience tests** | **S–M** | `_main_inner` `while True` is the one production path with **0 coverage** (getUpdates errors, offset recovery, retry). `process_update` is well tested; the I/O loop is not. |

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

- **Router activation/hardening gates Steps 3, 4, 8** — all are "tools under the router," which is currently off and unproven.
- **Image tool_results** are needed for Step 3 (Computer Use screenshots; also useful for vision).
- **InsightFace fix gates Step 6** (and improves today's batch quality).
- **Conversation-history persistence is needed for Steps 3 and 7** (multi-turn / long-running).
- **The missions-stack decision blocks Step 7** — two parallel autonomy systems is the real trap.

```
Step 2.7 (router on + hardened) ──┬─> Step 4 Office
                                  ├─> Step 8 MCP
                                  ├─> [+ image tool_results] ─> Step 3 Computer Use
                                  └─> [+ history persistence] ─> Step 3, Step 7

InsightFace/ONNX fix ─> Step 6 Deep-Live-Cam
Missions-vs-router decision ─> Step 7 Long autonomous
```

---

## 8. ⚠️ HUMAN DECISIONS required (Daniil)

| # | Decision | Context |
|---|---|---|
| ⚠️ **D1** | **Router pricing entry** — `claude-opus-4-8` at $5/$25 per 1M vs the real catalog rate. | Verify against the current Anthropic pricing catalog and correct `unified/llm_router/llm_client.py:25`. Low blast radius (router cost estimate only, Sonnet is the default), but wrong numbers corrupt cost tracking if Opus is ever used. |
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

---

*Generated from the 2026-06-14 read-only architecture audit + plan validation. Code facts cited; items marked "требует уточнения" / "⚠️ HUMAN DECISION" need confirmation before acting.*
