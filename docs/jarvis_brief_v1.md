---
title: "Jarvis V3 — Project Brief"
version: "v1.0"
status: "living-document"
owner: "Daniil Lapin"
generated: "2026-05-19"
generated_by: "Claude (Opus 4.7, 1M context) — full-repo audit"
audience: "Daniil himself · future contributors · investors · curious friends"
purpose: "Memory · Vision · Onboarding — one document"
---

# Jarvis V3 — Project Brief

> [!abstract] TL;DR
> **Jarvis V3** is a personal AI operator owned by **Daniil Lapin** (Kyiv — restaurant & events entrepreneur).
> It is one repository (`C:\jarvis`, ~150 service modules, ~1,976 tests, 43 routers, 321 HTTP endpoints) that wires together LLMs, image/video generators, n8n workflows, Google Workspace, Figma, RunPod GPUs and a Telegram bot into one assistant that should — eventually — feel like Tony Stark's J.A.R.V.I.S.
> Today it already creates AI personas, trains LoRAs, generates photos and videos, swaps faces in batch, designs landing pages, builds Figma briefs, runs n8n pipelines, answers in Russian over Telegram, manages a calendar and Gmail inbox, runs night-time autonomous workflows, and writes its own decisions into an Obsidian vault.
> The current frontier is **Block M.2.5** (face-swap on RunPod ComfyUI with batch orchestration) and **Phase E.1** (voice rewrite — making Jarvis *sound* like Jarvis).

> [!note]+ Verification pass — 2026-05-19, extension session
> The brief was re-verified before adding sections 17–20. Findings:
> - **Service modules:** ~150 stated → **156 direct + 220 with nested subpackages** (block_m*/, ai/, memory/, execution/). The brief's "~150" referred to top-level service files; deeper packages push it higher.
> - **Tests:** 1,976 functions across 117 files → **exact match** ✅
> - **Routers / endpoints:** 43 *loaded at startup* / 321 endpoints → confirmed (file count is higher — 35 in `app/routers/` + 37 in `app/api/` = 72 files — but not all are wired into `app/main.py`).
> - **YouTube subsystem:** Daniil mentioned managing a YouTube channel from Jarvis. **Re-confirmed absent from this repo.** No `youtube*` files, no `googleapiclient` / `pytube` / `yt_dlp` imports, no `YOUTUBE_*` env vars. The functionality is either manual, in an n8n workflow outside the repo, or planned. See [[#13 Open Questions & Decisions Pending]] #6.
> - **HEAD commit:** still `529d558 docs: Phase E.1 voice audit (discovery)`.
> - **No surprise subsystems missed.** Sections 17–20 below are new content, not corrections.

---

## 1. What Jarvis Is

### 1.1 The one-line definition

> **Jarvis V3 Supervisor** is Daniil's local, multi-agent AI operator: a backend (FastAPI on port 8010) + a Telegram bot + dozens of executors and integrations that together let one person run a creative-and-operations workload that would otherwise need a team.

The system is intentionally named after Stark's `J.A.R.V.I.S.`. The current phase-E.1 voice audit makes that ambition explicit: *"Inject personality into `identity_core.JARVIS_CORE_IDENTITY` — Stark-Jarvis traits: dry wit, formal address, deferential confidence."* (see [[#11.1 Voice Profile]])

### 1.2 What it actually does today

> [!info]+ Active capability surface (as of 2026-05-19)
> - **Telegram-first UX** — every capability is reachable from one chat with one bot.
> - **AI personas** — create a named persona from a 3-step dialog → seed photos via FLUX 1.1 Pro → LoRA training via Replicate (`ostris/flux-dev-lora-trainer`, ~$10, ~20 min) → photo & video generation with that persona.
> - **Photo & video** — Replicate (FLUX 1.1 Pro / Pro Ultra for stills, Kling v2.1 / Wan 2.5 I2V Fast for video), plus self-hosted **Wan 2.2 i2v** on **RunPod ComfyUI** for higher-quality / cheaper batch runs.
> - **Face swap (batch)** — Block M.2.5: ReActor on RunPod ComfyUI, with cost estimator, source/target collection over Telegram, animation upsell.
> - **Restaurant & events mode** — menu photography (4 styles), party promo posters (8 themes, 9:16), invitation cards, food-photography prompt enhancement (Jonathan Lovekin style).
> - **Landing pages & web** — `/design` Figma brief generation, `/landing` HTML+Tailwind generator, `/landing_brief` 8-step interview with 5 visual styles, `/create_app` bolt.diy spec generator, `/simple_game` HTML5 games (Snake, 2048, Memory, Tic-Tac-Toe).
> - **n8n** — deep, bidirectional. Jarvis discovers workflows, triggers them, materialises new ones, builds canvases programmatically. Both n8n Cloud (`daniliyc.app.n8n.cloud`) and self-hosted (Docker, PostgreSQL + Redis Bull queue, ports 5678/5680).
> - **Internet research** — Tavily (search) + Perplexity sonar-pro (research with citations) + structured XLSX/CSV table delivery directly to Telegram as files.
> - **Google Workspace** — Gmail (list/send), Calendar (`/calendar`, `/addevent`), Sheets, Drive (content uploads).
> - **Voice & vision** — voice messages → Whisper transcription (OpenAI) → normal processing; photos without caption → Claude Vision analysis.
> - **MCP server** — Jarvis exposes itself as an MCP server so Claude Desktop can call `jarvis_research`, `jarvis_create_table`, `jarvis_parse_file`.
> - **Night autonomy** — Block H5: 5 scheduled phases run during quiet hours (daily recap → auto content → self-improvement → trend analysis → smart scheduling) with a dashboard at `GET /dashboard/autonomy`.
> - **Cowork bridge** — filesystem-polling bridge that lets Jarvis delegate tasks to Claude Desktop (or another agent) and get the result back, sub-1s detection.
> - **Self-healing watchdog** — disk cleanup, memory monitoring, backend health checks (30s), auto-restart through `start_jarvis.ps1`.
> - **Conversation memory** — SQLite-backed semantic memory, automatic Obsidian vault notes at `C:\Users\Daniil Lapin\Documents\JarvisVault`.

### 1.3 What it is not (and never tries to be)

- Not a SaaS or a product sold to others. It is a one-operator system; the only "user" is Daniil. Permissions are enforced by `TELEGRAM_ALLOWED_CHAT_ID`.
- Not Perplexity, not Luxify Assistant, not a third-party Jarvis from the internet — the `BAD_IDENTITY_PATTERNS` regex in `app/services/identity_core.py:37-46` is the codified line in the sand. If the underlying LLM tries to introduce itself as something else, it gets `[FILTERED]`.
- Not single-purpose. The defining property is that **the same chat** dispatches creative work (video, persona, design), operations (calendar, email, n8n), research (Tavily/Perplexity), and infrastructure (RunPod pods, watchdog). The bet is that one operator + one chat surface beats ten purpose-built apps.

---

## 2. The Owner & The Why

### 2.1 Daniil's context

- **Where:** Kyiv, Ukraine.
- **What he runs:** A restaurant and an events / party business.
- **Why Jarvis exists:** To compress the work of marketing, content, ops, scheduling, and tool-stitching into a single conversational surface so one operator can run multiple businesses without hiring a marketing/content team.
- **Language:** Russian. ~85% of the user-facing text in the codebase is Russian; ~15% is English (system alerts, model names, technical labels). Jarvis defaults to Russian replies; addressing convention is being formalised to «вы» + occasionally "Daniil" by name (see [[#11.1 Voice Profile]]).

### 2.2 The business problems Jarvis directly serves

| Problem | How Jarvis answers it | Subsystem |
|---|---|---|
| "I need a dish photo for the menu / Instagram, right now" | `/menu_photo` → 4 styles (rustic, modern, dark, instagram), ~30 s each | `restaurant_mode.py`, [[#9.1 Persona & Media]] |
| "I'm running a party next Saturday, I need a poster" | `/party_promo` → 8 themes × 9:16 vertical | `party_mode.py` |
| "Send personalised invitations to 30 guests" | `/invite_card` with name + event placeholders | `party_mode.py` |
| "I want a landing page for this event by tomorrow" | `/landing_brief` (8-step interview) → `/landing` (HTML+Tailwind) | `landing_generator_v2.py` |
| "I need to put this on Figma and iterate" | `/design` → Figma brief queue → Figma client | `figma_client.py`, `figma_brief_generator.py` |
| "Schedule my week and remind me" | `/calendar`, `/addevent`, scheduler | `google_workspace_tools.py`, `scheduler.py` |
| "Research this competitor / topic for me" | `/research` (Perplexity) + `/table` (XLSX with sources) | `jarvis_internet_tools.py` |
| "Wake me up with a recap of yesterday" | Night autonomy → daily recap → Telegram | `night_workflows.py`, `daily_recap.py` |
| "Make a video of my AI model wearing X in Y location" | `/create_persona` → `/train_lora` → `/persona_video` | [[#9.1 Persona & Media]] |
| "Swap my face into these 20 photos and animate the good ones" | `/swapbatch_*` flow (M.2.5) | `block_m2_face_swap/` |

> [!note] This is the operator's life. The brief is meant to make it obvious *why* the codebase looks the way it does — every block of code maps to one of the rows above (or to the plumbing that makes those rows fast and reliable).

---

## 3. Architecture at 30,000 ft

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            TELEGRAM (1 chat)                                │
│   /commands  ·  natural language  ·  inline buttons  ·  photo/voice         │
└────────┬───────────────────────────────────────────────────┬────────────────┘
         │ (long polling)                                    │
         ▼                                                   ▼
┌────────────────────────────────┐                ┌──────────────────────────┐
│ tools/jarvis_smart_telegram_…  │ <─────POST────│  app/telegram_bot.py     │
│   (5,272 lines, real "brain") │                │  (thin polling client,   │
│   60+ commands, callbacks      │                │   forwards /api/respond) │
└────────┬───────────────────────┘                └────────┬─────────────────┘
         │                                                 │
         └────────────────HTTP─────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              FastAPI backend  ·  app/main.py  ·  uvicorn :8010              │
│         43 routers · 321 endpoints · startup-loaded with try/except         │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐        │
│  │ Brain V2 │ Mesh ×5  │ Missions │ n8n ×4   │ Persona  │ AI       │        │
│  │ /brain   │ /agent-… │ /missions│ /n8n     │ /persona │ /ai      │        │
│  └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘        │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
┌────────────────┐       ┌──────────────────┐      ┌────────────────────┐
│ LLM PROVIDERS  │       │  MEDIA & COMPUTE │      │ AUTOMATION & DATA  │
│ Anthropic      │       │  Replicate       │      │ n8n cloud + self   │
│ OpenAI         │       │  RunPod ComfyUI  │      │ Google Workspace   │
│ Ollama (local) │       │  Wan 2.2 / Kling │      │ Figma              │
│ Perplexity     │       │  ReActor face    │      │ Obsidian vault     │
│ Tavily         │       │  FLUX 1.1 Pro    │      │ MCP server         │
└────────────────┘       └──────────────────┘      └────────────────────┘
```

### 3.1 The three planes

1. **Conversational plane** — Telegram bot (`tools/jarvis_smart_telegram_control.py` is the real one; `app/telegram_bot.py` is a thin fallback). Handles intent classification, slash commands, inline buttons, photo/voice messages, callback queries.
2. **Reasoning & orchestration plane** — Brain V2 (`app/routers/jarvis_brain_v2.py` + `app/services/identity_core.py`) + the Agent Mesh (5 routers, 58 endpoints, `app/services/smart_router.py`). Decides which mode to run (control/brain/engineer/research/mission/n8n), which agent(s), what plan.
3. **Execution & integration plane** — services (~150 modules) that actually call external APIs, manage state, generate media, write to Drive/Obsidian, talk to RunPod.

### 3.2 Why so many modules

The codebase grew through ~20 named "blocks" and ~35 numbered "phases". Each one solved a specific operator pain (see [[#10 Evolution Stages → Blocks → Phases]]). The result is unusual for a personal project: dense, with substantial test coverage (~1,976 tests), but also carrying noticeable legacy (`jarvis_stage3_artifacts/` has 100+ historical subdirectories, kept on purpose as a graveyard of decisions that informed today's code).

> [!warning] This is a deliberate trade. Pruning is in progress (`PROGRESS.md` — Phases 1 + 2 done, Phase 3 paused on decisions). Treat anything inside `jarvis_stage3_artifacts/` as **historical artifact**, not live code.

---

## 4. Entry Points & How to Run

### 4.1 Quick start

```powershell
# From C:\jarvis
.\start_jarvis.ps1
```

Opens two terminal windows: **Backend** on `http://127.0.0.1:8010` (FastAPI + uvicorn) and **Telegram bot** (long-poll).
Health check: `curl http://127.0.0.1:8010/health` → `{"status":"ok"}`.

### 4.2 Service map

| Service | Port | Command | Required for |
|---|---|---|---|
| FastAPI backend | 8010 | `uvicorn app.main:app --port 8010` | Everything |
| Telegram bot (smart) | — | `python tools/jarvis_smart_telegram_control.py` | All user-facing flows |
| Telegram bot (thin) | — | `python -m app.telegram_bot` | Fallback only — points to 8010 |
| n8n self-hosted | 5678 / 5680 | `docker compose -f n8n_docker_strong/docker-compose.yml up` | n8n local workflows |
| RunPod ComfyUI | dynamic | pod created on demand by `runpod_client.py` | Wan 2.2 i2v video, ReActor face-swap |
| Ollama (optional) | 11434 | `ollama serve` (external) | Local LLM fallback |

### 4.3 Boot scripts (for orientation only — don't add new ones)

The repo root has many `start_*` and `stop_*` `.bat` and `.ps1` files reflecting iterations of the launcher. The current canonical entry is **`start_jarvis.ps1`**. Older variants (`start_jarvis_console_v3.bat`, `start_jarvis_operator_panel_v4.bat`, etc.) are kept because some launch one-window flows for diagnostics.

---

## 5. External Integrations — Complete Inventory

> [!info]+ Source-of-truth lookups
> All env-var key names below are read from `app/settings.py`, `tools/jarvis_smart_telegram_control.py`, and `n8n_docker_strong/docker-compose.yml`. Values themselves are never read or stored in this brief.

### 5.1 LLM providers

| Provider | Module | Use | Env key(s) | Status |
|---|---|---|---|---|
| **Anthropic (Claude)** | `app/services/ai/provider_anthropic.py`, `app/services/claude_helper.py` | Architecture, deep code, engineering, reasoning, **Vision**, **MCP** | `ANTHROPIC_API_KEY`, `AI_ROUTER_ANTHROPIC_MODEL` | ✅ production |
| **OpenAI (GPT)** | `app/services/ai/provider_openai.py`, `app/ai_provider.py` | Dialogue, classification, fast reasoning, **Whisper voice** | `OPENAI_API_KEY`, `AI_ROUTER_OPENAI_MODEL` | ✅ production |
| **Ollama (local)** | `app/services/ai/provider_ollama.py` | Local fallback (`llama3.1:latest`) | `AI_ROUTER_OLLAMA_BASE_URL`, `AI_ROUTER_OLLAMA_MODEL`, `AI_ROUTER_ENABLE_OLLAMA` | ✅ production (requires local server) |
| **Perplexity** | `app/services/internet_agent.py`, `app/services/jarvis_internet_tools.py` | Internet research with citations (sonar-pro) | `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL` | ✅ production |
| **OpenAI-compatible** | `app/services/agent_adapters.py` | Generic compat layer | `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY` | 🧪 experimental |

**Routing logic** (`multi_ai_orchestrator_v1.py`): `engineer / architect / n8n` → Anthropic → OpenAI; `chat / control / mission / research` → OpenAI → Anthropic; everything → Ollama if no external keys.

### 5.2 Media generation

#### 5.2.1 Replicate

Active model IDs:

```
black-forest-labs/flux-1.1-pro           # stills, $0.04/pred
black-forest-labs/flux-1.1-pro-ultra     # stills HQ, $0.06/pred
black-forest-labs/flux-redux-dev         # img2img, $0.025/pred
omniedgeio/face-swap                     # face swap, $0.005/pred
codeplugtech/face-swap                   # alt face swap, $0.01/pred
tencentarc/photomaker                    # face-conditioned, $0.05/pred
tencentarc/gfpgan                        # face restoration, $0.002/pred
kwaivgi/kling-v2.1                       # video, image-to-video
wan-… (Wan 2.5 I2V Fast)                 # video, Persona M.1
ostris/flux-dev-lora-trainer             # LoRA training
```

Env: `REPLICATE_API_TOKEN`.

#### 5.2.2 RunPod (GPU compute)

| Component | Module | Purpose |
|---|---|---|
| Pod lifecycle | `app/services/block_m2_video/runpod/runpod_client.py` | Spawn, monitor, terminate GPU pods on demand |
| ComfyUI engine | `app/services/block_m2_video/engines/runpod_comfy_engine.py` | Wan 2.2 i2v video via ComfyUI on RunPod |
| ReActor engine | `app/services/block_m2_face_swap/engines/` | Face swap on RunPod ComfyUI |
| Guardian | `runpod/runpod_guardian.py` | Auto-stop runaway pods, enforce daily budget |

GPU preference order (after Phase 3.2 prep): **H200 NVL ($0.50/h)** → **A100 ($1.19/h)** → H100 → L40S → A40 → RTX PRO 6000 (current primary).
Docker image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`.
Datacenter pinned to **EU-RO-1** (latency + supply).
ComfyUI exposed on port **8188**.
Budget guardrail: `RUNPOD_MAX_BUDGET_USD_PER_DAY`, pod TTL `RUNPOD_MAX_POD_LIFETIME_MIN=60` minutes for full Wan 2.2 cycle.

#### 5.2.3 ComfyUI (local + remote)

- Local: `http://127.0.0.1:8188` (`app/services/comfyui_client.py`)
- Remote on RunPod: same client, different URL via `runpod_client.get_pod_public_url()` (supports HTTP proxy URLs since Phase B.3 fix).
- Workflow JSON lives in `app/services/block_m2_video/runpod/workflows/` (synced from pod's network volume).

#### 5.2.4 InfluencerStudio (third-party SaaS)

Configured but optional: `INFLUENCER_API_KEY`, `INFLUENCER_BASE_URL` (default `https://influencerstudio.com/api/v1`), `INFLUENCER_WORKSPACE_ID`, `INFLUENCER_ID`. Used by the older `jarvis_v5_content_factory.py` content pipeline.

### 5.3 Automation — n8n (deep)

> [!important] n8n is the most-integrated external system after the LLMs.

| Component | Module | Purpose |
|---|---|---|
| n8n cloud client | `app/services/n8n_integration.py` | List, trigger, monitor workflows on `daniliyc.app.n8n.cloud` |
| Local bridge | `app/services/n8n_bridge.py` | Bidirectional webhook bridge, port 8030 default |
| Specialist agent | `app/services/jarvis_n8n_specialist.py` | Picks the right workflow for an intent |
| Super-agent | `app/services/jarvis_n8n_super_agent.py` | Generates n8n workflow JSON from scratch |
| Materialiser v1 + v2 | `app/services/n8n_workflow_materializer*.py` | Plan → executable workflow |
| Action router materialiser v1 + v2 | `app/services/n8n_action_router_materializer*.py` | Routes actions to nodes |
| Canvas builder | `app/services/n8n_canvas_builder.py` | Programmatic canvas composition |
| Bridge client | `app/services/jarvis_n8n_bridge_client.py` | REST client to local bridge |

Env: `N8N_BASE_URL`, `N8N_API_KEY` (cloud); `JARVIS_N8N_BASE_URL`, `JARVIS_N8N_TRIGGER_PATH=/webhook/jarvis/inbox_v2`, `JARVIS_N8N_SHARED_KEY` (local + auth).
Docker stack: `n8n_docker_strong/docker-compose.yml` — n8n-main (5680), n8n-worker, PostgreSQL, Redis (Bull queue mode).

### 5.4 Productivity — Google Workspace

| Service | Module | Scopes |
|---|---|---|
| Gmail | `app/api/google_tools.py`, `google_workspace_tools.py` | `gmail.send`, `gmail.readonly` |
| Calendar | `google_workspace_tools.py` | `calendar` (`GOOGLE_CALENDAR_ID`, `GOOGLE_CALENDAR_TIMEZONE=Europe/Kyiv`) |
| Drive | `jarvis_v5_content_factory.py` | OAuth file uploads (`google_oauth_token_drive.json`) |
| Sheets | `spreadsheet_service.py`, `app/api/spreadsheets.py` | `spreadsheets`, `drive` |

Auth: service account JSON (`GOOGLE_SERVICE_ACCOUNT_JSON`) + OAuth (`GOOGLE_OAUTH_CLIENT_SECRET_JSON`, token cache at `state/google_oauth_token.json`).

### 5.5 Design — Figma

`app/services/figma_client.py` + `figma_brief_generator.py` + `figma_queue.py`.
Env: `FIGMA_API_KEY`.
Flow: `/design` → Claude builds a brief → `figma_queue` → `figma_client` creates a wireframe-style file → Telegram returns the URL.

### 5.6 Communication & UI

- **Telegram** — `python-telegram-bot`-style polling. Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID` (single-user lock), `TELEGRAM_POLL_INTERVAL_SECONDS=3`, `TELEGRAM_WAIT_FOR_COMPLETION_SECONDS=60`, `TELEGRAM_BACKEND_URL=http://127.0.0.1:8010`.
- **Obsidian vault** — `JARVIS_OBSIDIAN_VAULT_PATH=C:\Users\Daniil Lapin\Documents\JarvisVault`. Mission decisions and memory entries are written as Markdown notes via `app/services/memory/obsidian_bridge_service.py`.
- **Email / SMTP** — only through Gmail API; no raw SMTP.

### 5.7 Research

| Tool | Backed by | Endpoint |
|---|---|---|
| `internet.search` | Tavily | `POST /api/jarvis/tools/internet/search` |
| `internet.research` | Perplexity sonar-pro | `POST /api/jarvis/tools/internet/research` |
| `internet.find_pipelines` | Tavily + Perplexity | (composed) |
| `internet.compare_services` | Perplexity | (composed) |
| `internet.engineer_brief` | Tavily + Perplexity | (composed) |
| `jarvis_create_table` | Tavily + openpyxl | `POST /api/jarvis/telegram-tools/internet-table` |

Env: `TAVILY_API_KEY`, `PERPLEXITY_API_KEY`.

### 5.8 MCP server (outbound)

`app/services/mcp_server.py` + `mcp_adapter.py`. JSON-RPC 2.0 over stdio. Exposes 3 tools to Claude Desktop:

- `jarvis_research` → `/api/jarvis/tools/internet/research`
- `jarvis_create_table` → `/api/jarvis/tools/table/create`
- `jarvis_parse_file` → `/api/jarvis/tools/file/parse`

### 5.9 Infra — Cloudflare Tunnel & SSH

`CLOUDFLARE_TUNNEL_SETUP.md` + `HOME_SERVER_SETUP.md` + `runpod/` SSH scripts.
Remote access to the home server / RunPod pods is via Cloudflare tunnel + SSH. Used for manual pod inventory and debugging.

### 5.10 NOT integrated (despite being mentioned somewhere)

| Service | Status |
|---|---|
| Discord, Slack | Planned, no live code |
| Notion | Planned |
| Deepseek, Mistral, Gemini, Groq | Not integrated |
| Midjourney, Stability AI, CivitAI | Not integrated |
| DuckDuckGo, SerpAPI, Brave, ScraperAPI | Not integrated |
| AWS, Vast.ai, Lambda Labs | Not integrated |
| **YouTube channel management** | Daniil mentions doing it, but **no YouTube API code is present in this repo**. Likely manual or via an n8n workflow. See [[#13 Open Questions]] #6. |

---

## 6. Telegram Surface — The Operator's Cockpit

> [!example]+ Complete command catalog
> 60+ slash commands, 11+ callback prefixes, ~10 natural-language intents. All routed through `tools/jarvis_smart_telegram_control.py` (5,272 lines) and `app/handlers/*.py`.

### 6.1 Core / system

| Command | What it does | Handler |
|---|---|---|
| `/start` | Welcome + capabilities | `app/telegram_bot.py` |
| `/id` | Show current chat_id (auth diagnostics) | `app/telegram_bot.py` |
| `/health`, `/debug_health` | Backend + sub-service health (full JSON on `/debug_health`) | `telegram_bot.py` / smart control |
| `/diag` | 8-check self-diagnostic | smart control |
| `/smart_help` | Full capability list grouped by category | smart control |
| `/agents` | List 13 registered agents + health | smart control |
| `/mesh` | Inline panel: SIMPLE / AUTO / ALWAYS mesh mode | smart control |
| `/night_now` | Manually trigger night autonomy cycle | smart control |

### 6.2 Google Workspace

| Command | What it does |
|---|---|
| `/gmail` | Last 5 Gmail messages |
| `/calendar` | Next 3 days of calendar events |
| `/addevent <text>` | Create a calendar event from natural-language text |

### 6.3 Persona & LoRA (Block M.1)

| Command | What it does |
|---|---|
| `/create_persona` | Start a 3-step persona dialog → seed photos |
| `/cancel_persona` | Cancel the active dialog |
| `/train_lora <id>` | Train LoRA from a persona's seed photos (~$10, ~20 min) |
| `/lora_status [id]` | Training progress |
| `/list_loras` | All trained personas |
| `/cancel_lora [id]` | Cancel an in-flight LoRA training |
| `/persona_photo <id> <prompt>` | Single photo with the trained LoRA |
| `/persona_engine <id> [kling_v21\|wan22_fast]` | Set / show video engine |

### 6.4 Video (Block M.2)

| Command | What it does |
|---|---|
| `/persona_video <name> <prompt> [--fast\|--hq] [--seconds N] [--seed N]` | Generate video with the persona |
| `/persona_video_redo` | Regenerate last video with same prompt/seed |
| `/persona_redo <record_id>` | Redo any past generation |

### 6.5 Face-swap batch (Block M.2.5 — currently in-progress)

| Command | What it does |
|---|---|
| `/swapbatch_source` | Upload 1 source-face photo |
| `/swapbatch_batch` | Upload up to 10 target photos |
| `/swapbatch_go` | After cost report → run the swap |
| `/swapbatch_animate_yes` / `_no` | Animate the swapped photos into video, or skip |
| `/swapbatch_status` | Current batch state + cost estimate |
| `/swapbatch_cancel` | Cancel the active batch |

### 6.6 Me-Persona (personal AI avatar — Block M.2.2)

| Command | What it does |
|---|---|
| `/me_seed` | Start collecting ~10 seed photos of yourself |
| `/me_done` | Finalise, then prompt to train LoRA |
| `/me_swap_photo <prompt>` | Generate yourself in any context |
| `/me_swap_video <url>` | Swap your face into an existing video |
| `/me_as <role>` | Generate yourself in a character role |
| `/me_in <location>` | Generate yourself in a location |
| `/me_style <style>` | Generate yourself in an artistic style |

### 6.7 Analytics & history

| Command | What it does |
|---|---|
| `/costs` | Today's spending grouped by operation |
| `/history <persona_id> [limit]` | Recent generations for a persona |
| `/persona_batch <persona_id> <count> <prompt>` | Generate N photos in batch |

### 6.8 Restaurant / events / personal modes

| Command | What it does |
|---|---|
| `/menu_photo` | 4 food-photo styles |
| `/party_promo` | 8 themes, 9:16 vertical posters |
| `/invite_card` | Personalised event invitations |
| `/pro_food` | Food-photography prompt enhancement |
| `/enhance` | Photo enhancement via GFPGAN v1.4 |
| `/faceswap` | Legacy face-swap (superseded by `/swapbatch_*`) |
| `/lora_train` | Personal LoRA training (~$10) |
| `/smart_photo` | Enhance any prompt to professional photographer level |

### 6.9 Web / design / app builder

| Command | What it does |
|---|---|
| `/design` | Figma design brief generation |
| `/landing` | HTML + Tailwind CSS landing page |
| `/landing_brief` | 8-step landing brief interview (5 styles) |
| `/create_app` | bolt.diy app spec |
| `/simple_game` | HTML5 games (Snake, Tic-Tac-Toe, Memory, 2048) |

### 6.10 n8n & content

| Command | What it does |
|---|---|
| `/n8n list` | All workflows |
| `/n8n run <id>` | Trigger a workflow |
| `/n8n status <exec_id>` | Execution status |
| `/n8n enable <id>` / `disable <id>` | Toggle a workflow |
| `/gen [N] <prompt>` | Generate images (max 4) |
| `/job <id>` | Async job status |

### 6.11 Cowork bridge & operator

| Command | What it does |
|---|---|
| `/cowork status` | Cowork watcher state |
| `/cowork send <task>` | Send a task to Cowork (Claude Desktop) |
| `/mesh_control` | Mesh settings panel |
| `/remind <when> <text>` | One-off reminder |
| `/brief` | Operator brief |
| `/logs <slice>` | Tail logs |
| `/selfcheck` | Run self-diagnostic |
| `/errors` | Recent error report |
| `/improve` | Trigger self-improvement loop |

### 6.12 Inline callback prefixes

| Prefix | Used by |
|---|---|
| `mesh:mode:*`, `mesh:settings`, `mesh:last_plan`, `mesh:history`, `mesh:cfg:*` | Mesh control panel |
| `task:execute:*`, `task:cancel:*`, `task:simpler:*`, `task:deeper:*` | Confirmable tasks |
| `feedback:*` (👍/👎) | Feedback collector |
| `qa:more:*`, `qa:obsidian:*` | "More details" and "Save to Obsidian" |
| `capabilities`, `table_top_ai`, `health` | Inline buttons in `/start` reply |

### 6.13 Natural-language intents (no slash needed)

The intent classifier in `tools/jarvis_smart_telegram_control.py` recognises ~10 behavioural classes in Russian (and English):

- **Greeting** — `привет`, `привет как дела`, `hello`
- **Capabilities** — `что ты умеешь`, `какие у тебя возможности`, `what can you do`
- **Status / are-you-there** — `ты живой`, `бот работает`, `are you there`
- **Identity** — `кто ты`, `как тебя зовут`, `what are you`
- **Research** — `найди`, `поищи`, `расскажи про`
- **Tables** — `сделай таблицу`, `compare`
- **Generate** — `сделай фото`, `создай картинку`, `нарисуй`

Photo and voice messages without text are also handled (see [[#1.2 What it actually does today]]).

---

## 7. The Backend — Routers & Endpoints

> 43 routers, 321 endpoints. Full list lives in `ROUTERS_STATUS.md` and `ENDPOINTS_LIVE.md` (and the post-3.7 snapshot in `ENDPOINTS_LIVE_AFTER_3_7.md`). Below is the structural grouping.

### 7.1 Identity & reasoning

| Router | Purpose |
|---|---|
| `app/routers/jarvis_brain_v2.py` | **Primary brain.** `JARVIS_IDENTITY` system prompt + `BAD_IDENTITY_PATTERNS` regex guard. Handles `/brain` requests. |
| `app/routers/jarvis_brain_router.py` | Legacy brain endpoints |
| `app/routers/jarvis_brain_executor_router.py` | Brain → executor wiring |
| `app/routers/multi_ai_orchestrator_v1.py` | Multi-provider orchestration (Claude / OpenAI / Ollama fallback chain) |

### 7.2 Agent mesh (5 routers, 58 endpoints, `/api/agent-mesh/*`)

| Router | Endpoints | Purpose |
|---|---|---|
| `agent_mesh_router.py` | 22 | Core mesh: registry, learning, variants, lessons, recommendations |
| `agent_mesh_plus_router.py` | 15 | Extensions: lifecycle, knowledge ingest, communication policy, autonomy tick |
| `agent_mesh_stability_router.py` | 4 | Self-healing + service guard |
| `agent_mesh_real_exec_router.py` | 8 | n8n verify, webhook test, autonomy enable/disable |
| `agent_mesh_improvement_router.py` | 9 | Continuous improvement, proposals, journal |

(See `AGENT_MESH_PLAN.md` — "all 5 routers needed, no duplicates, deletion not recommended".)

### 7.3 Missions & memory

| Router | Purpose |
|---|---|
| `app/api/missions.py`, `app/api/endpoints/missions.py` | Mission CRUD |
| `app/api/mission_ai_bridge.py` | AI → mission glue |
| `app/api/mission_artifacts.py` | Artifact retrieval |
| `app/api/mission_graph.py` | Mission DAG |
| `app/api/mission_memory_bridge.py` | Mission → memory writes |
| `app/api/mission_run_memory.py` | Runtime memory |
| `app/api/memory.py`, `memory_layer.py` | Semantic memory store |
| `app/api/obsidian_bridge.py` | Obsidian vault writes |

### 7.4 Specialised agents & multistep

| Router | Purpose |
|---|---|
| `app/api/specialized_agents.py` | Dispatch to specialised agents (coder, researcher, reasoner) |
| `app/api/multi_step_execution.py` | Multi-step orchestrator |
| `app/routers/multistep.py` | Step planning |
| `app/api/execution_planner.py` | Plan compiler |
| `app/api/external_executor.py` | External tool dispatch |

### 7.5 n8n routers

| Router | Purpose |
|---|---|
| `app/routers/jarvis_n8n_bridge_router.py` | Bridge endpoints |
| `app/routers/jarvis_n8n_specialist_router.py` | Specialist agent endpoints |
| `app/routers/jarvis_n8n_super_agent_router.py` | Super-agent (workflow generation) |
| `app/routers/n8n_action_router_materializer_router.py`, `_v2_router.py` | Materialisers |

### 7.6 Content / creative

| Router | Purpose |
|---|---|
| `app/routers/jarvis_v5_content_factory.py` | Image + video generation pipeline |
| `app/routers/jarvis_v5_async_bridge.py` | Async job polling |
| `app/routers/replicate_image_router.py` | Replicate direct |
| `app/routers/video_factory.py` | Video factory |
| `app/routers/jarvis_internet_tools_router.py` | Research tools |
| `app/routers/jarvis_telegram_file_tools_router.py` | XLSX/CSV → Telegram |
| `app/routers/jarvis_file_tools_router.py` | File parsing |

### 7.7 Ops / operator

| Router | Purpose |
|---|---|
| `app/routers/operator_dashboard.py` | Dashboard JSON + HTML |
| `app/routers/jarvis_dashboard_router.py` | Alt dashboard |
| `app/routers/jarvis_operator_task_router.py` | Operator task center |
| `app/routers/jarvis_live_operator.py` | Live operator brain |
| `app/routers/jarvis_logs_router.py` | Log access |
| `app/routers/agent_control_plane.py` | Agent control |
| `app/routers/supervisor_automation_router.py` | Automation runtime |
| `app/routers/supervisor_pipeline_router.py` | Pipeline runtime |
| `app/routers/jarvis_unified_night_router.py` | Night autonomy |
| `app/routers/time_brain.py` | Time-aware brain |
| `app/routers/jarvis_ai_engineer_router.py` | AI engineer mode |
| `app/routers/claude_ecosystem.py` | Claude ecosystem ops |
| `app/routers/resume_recovery.py` | Resume after crash |

### 7.8 AI router (parallel code path)

`app/api/ai_router.py` is the newer multi-provider router (`AI_ROUTER_OPENAI_MODEL=gpt-4.1`, `AI_ROUTER_ANTHROPIC_MODEL=claude-sonnet-4-5`, `AI_ROUTER_OLLAMA_MODEL=llama3.1:latest`). Coexists with the older `multi_ai_orchestrator_v1.py`.

---

## 8. The Identity Core — Jarvis's Self-Definition

> [!important] If you read **one** file in this repo, read [`app/services/identity_core.py`](../app/services/identity_core.py).
> It is 200 lines that define everything Jarvis is and isn't.

### 8.1 The core declaration (paraphrased from `JARVIS_CORE_IDENTITY`)

> Ты — **Jarvis V3 Supervisor**. Ты НЕ Perplexity. Ты НЕ Luxify Assistant. Ты НЕ сторонний Jarvis из интернета. Ты НЕ должен рекламировать чужие продукты. Ты локальный оператор **Daniil**-а, работающий через backend, Telegram bridge, миссии, агенты, n8n, инструменты и execution layer.

### 8.2 The seven operating rules

1. Always respond as Jarvis V3 Supervisor.
2. If asked "are you there / what can you do / status?" — explain the real system status first.
3. Don't invent access to unavailable tools. Distinguish: available now / partially available / needs setup.
4. When the task needs action, pick a **mode**: `control`, `brain`, `engineer`, `research`, `mission`, `n8n`.
5. Don't say "I cannot perform actions" by default — check if a backend/tool/mission route exists first.
6. If a tool is unavailable — return an operator-ready plan or command.
7. Be concrete, useful and action-oriented.

### 8.3 The five roles

`supervisor` (default), `agent`, `coder`, `researcher`, `reasoner`. Each gets a suffix appended to the core identity (see `_ROLE_SUFFIX_RU` and `_ROLE_SUFFIX_EN`).

### 8.4 The provider hints

Provider-specific suffixes appended last:

- **Anthropic** → "ты работаешь как Claude Architect/Coding Agent внутри Jarvis"
- **OpenAI** → "быстрый reasoning/dialogue agent внутри Jarvis"
- **Ollama / Perplexity** → no hint

### 8.5 The output guard

`sanitize_response(text)` runs `BAD_IDENTITY_PATTERNS` regex over every LLM output. Any hit (`\bperplexity\b`, `\bluxify\b`, `tribute.tg`, `@lux_assistant_bot`, etc.) is replaced with `[FILTERED]` and a `was_sanitized=True` flag returned to the caller.

### 8.6 Why this matters

The Phase 4 audit (`IDENTITY_DISCOVERY.md`) found **10 different places** in the codebase that built system prompts independently — 5 RU, 5 EN, two with no Jarvis identity at all (`ai_provider.py:18`, `control_plane/adapters.py:218`). That fragmentation was the root cause of "why does Jarvis answer like Claude / Perplexity?" The unification into `identity_core.py` is the single highest-leverage change in the codebase.

---

## 9. Services Inventory — All ~150 Modules

> Status legend: ✅ production · 🧪 experimental · ❓ unclear / unused

### 9.1 Persona & Media (~35 modules)

#### `block_m1_persona/`
| Module | Purpose | Status |
|---|---|---|
| `lora_trainer.py` | LoRA training via Replicate `flux-dev-lora-trainer` | ✅ |
| `persona_creator.py` | FLUX Pro seed photo generation with async rate limiting | ✅ |
| `persona_dialog.py` | 3-step Telegram FSM dialog | ✅ |
| `photo_generator.py` | Single-photo generation from trained LoRA | ✅ |
| `prompt_builder.py` | Diverse seed-photo prompt generator (angles/lighting/emotions) | ✅ |

#### `block_m2_video/`
| Module | Purpose | Status |
|---|---|---|
| `video_generator.py` | Photo-to-video orchestrator (FLUX → Kling/Wan) | ✅ |
| `generation_history.py` | History + metadata | ✅ |
| `generation_lock.py` | Per-chat in-memory lock (Block M.2 Phase C) | ✅ |
| `video_storage.py` | Persistent storage | ✅ |
| `video_client_extras.py` | Engine dispatch + routing | ✅ |
| `engines/engine_protocol.py` | Engine `Protocol` | ✅ |
| `engines/replicate_engine.py` | Replicate Wan 2.5 I2V Fast | ✅ |
| `engines/runpod_comfy_engine.py` | RunPod ComfyUI Wan 2.2 i2v | 🟡 in-progress |
| `engines/router.py` | Router between Replicate/RunPod | ✅ |
| `runpod/runpod_client.py` | Pod lifecycle | ✅ |
| `runpod/runpod_config.py` | Config + secrets | ✅ |
| `runpod/runpod_guardian.py` | Health + auto-stop | ✅ |
| `runpod/comfyui_client.py` | Low-level ComfyUI HTTP | ✅ |

#### `block_m2_face_swap/`
| Module | Purpose | Status |
|---|---|---|
| `face_swap_engine.py` | ReActor on RunPod ComfyUI | 🟡 |
| `batch_orchestrator.py` | Batch dispatch with keep-pod-alive | 🟡 |
| `cost_estimator.py` | Env-overridable cost estimation | ✅ |
| `face_validator.py` | Local face detection (OpenCV) | ✅ |

#### `block_m22_fun/` (Me-Persona)
| Module | Purpose | Status |
|---|---|---|
| `me_persona.py` | Personal persona mode | 🧪 |
| `seed_collector.py` | Seed image collection | 🧪 |
| `video_face_swap.py` | Face swap into video | 🧪 |

#### `block_m23_polish/`
| Module | Purpose | Status |
|---|---|---|
| `analytics.py` | Cost + performance analytics | 🧪 |
| `batch_generator.py` | Parallel batch generation | 🧪 |
| `seed_cache.py` | Cache for seed images | 🧪 |

#### `block_m_common/`
| Module | Purpose | Status |
|---|---|---|
| `cost_tracker.py` | Daily cost tracking + limit enforcement | ✅ |
| `logging_setup.py` | Centralised logging | ✅ |
| `persona_storage.py` | Persistent persona metadata | ✅ |
| `replicate_video_client.py` | Replicate video API | ✅ |
| `video_queue.py` | Video job queue | ✅ |

#### Top-level media services
| Module | Purpose | Status |
|---|---|---|
| `face_swap.py` | Basic + polished swap + face enhancement | ✅ |
| `lora_manager.py` | LoRA training + status + generation | ✅ |
| `photo_studio.py` | Photo Studio orchestrator | ✅ |
| `smart_photo_router.py` | Auto-detect pipeline | ✅ |
| `comfyui_client.py` | ComfyUI HTTP client | ✅ |
| `video_factory_service.py` | ComfyUI workflow execution | ✅ |
| `replicate_image_gen.py` | FLUX 1.1 Pro images | ✅ |
| `replicate_models.py` | Model catalog + metadata | ✅ |
| `image_library.py` | Generated-image metadata store | ✅ |
| `restaurant_mode.py` | Restaurant pro mode (`/menu_photo`) | ✅ |
| `party_mode.py` | Party pro mode (`/party_promo`, `/invite_card`) | ✅ |
| `personal_mode.py` | `/me_as`, `/me_in`, `/me_style` | ✅ |

### 9.2 Brain & Orchestration (~14 modules)

| Module | Purpose | Status |
|---|---|---|
| `identity_core.py` | **Single source of truth for system prompts** | ✅ |
| `jarvis_brain_foundation.py` | Foundation brain + provider profiles | ✅ |
| `jarvis_brain_executor.py` | Brain → executor with error handling | ✅ |
| `jarvis_thinking_layer.py` | Reflection layer for hard reasoning | ✅ |
| `conversation_brain.py` | `/api/respond` reply generator (Ollama/OpenAI/RU fallback) | ✅ |
| `conversation_memory.py` | Persistent history + context | ✅ |
| `supervisor.py`, `supervisor_core.py` | Mission orchestration | ✅ |
| `supervisor_automation_runtime.py`, `supervisor_pipeline_runtime.py` | Pipeline runtimes | 🧪 |
| `planner.py`, `task_planner.py` | Plan decomposition | ✅ |
| `decision_log.py` | Records routing decisions | ✅ |
| `context_manager.py` | Cross-task context | ✅ |
| `semantic_memory.py` | SQLite-backed semantic memory + Obsidian | ✅ |
| `quick_answer.py` | Phase 23: Haiku-based fast factual answers | ✅ |
| `smart_router.py` | Phase 16: agent selection + plan building | ✅ |
| `result_synthesizer.py` | Phase 18: LLM synthesis of parallel results | ✅ |
| `parallel_executor.py` | Phase 31: ThreadPoolExecutor parallel exec | ✅ |
| `mission_engine.py`, `mission_runner.py`, `mission_store.py`, `mission_memory.py`, `mission_lock.py`, `mission_result_packager.py`, `mission_resume_store.py`, `mission_service.py` | Mission lifecycle | ✅ (one ❓ stub) |

### 9.3 n8n & Workflows (12 modules)

See [[#5.3 Automation — n8n (deep)]] above. Status: all ✅ production.

### 9.4 Landing & Figma (8 modules)

| Module | Purpose | Status |
|---|---|---|
| `landing_generator.py`, `landing_generator_v2.py` | HTML+Tailwind landing pages | ✅ |
| `landing_content_generator.py` | LLM-driven copy | ✅ |
| `landing_brief_session.py` | 8-step interview session | 🧪 |
| `figma_client.py` | Figma API client | ✅ |
| `figma_brief_generator.py` | Claude → design brief | 🧪 |
| `figma_queue.py` | Async design queue | 🧪 |
| `app_spec_generator.py` | bolt.diy spec | 🧪 |
| `game_generator.py` | HTML5 game generator | 🧪 |

### 9.5 Internet & Research (2 modules)

| Module | Purpose | Status |
|---|---|---|
| `internet_agent.py` | Perplexity-style research agent | ✅ |
| `jarvis_internet_tools.py` | Suite of internet tools | ✅ |

### 9.6 Scheduling, Time & Missions (12 modules)

| Module | Purpose | Status |
|---|---|---|
| `scheduler.py` | Phase 28: APScheduler-backed persistent scheduling | ✅ |
| `smart_schedule.py` | Block H5.6: contextual scheduling | ✅ |
| `time_brain.py` | Deadline-aware scheduling | ✅ |
| `mission_*.py` (×8) | Mission lifecycle (see 9.2) | ✅ |

### 9.7 Google & Workspace (2 modules)

`google_workspace_tools.py` ✅ and `spreadsheet_service.py` ✅.

### 9.8 Safety & Guards (6 modules)

| Module | Purpose | Status |
|---|---|---|
| `jarvis_truth_guard.py` | Validates outputs against facts/URLs | ✅ |
| `shell_guard.py` | Shell command validation | ✅ |
| `tool_safety.py` | Tool execution safety | ✅ |
| `policy.py`, `risk_policies.py` | Policy config + risk enforcement | ✅ |
| `jarvis_text_safety.py` | Text safety filtering | ✅ |

### 9.9 Self-Improvement & Autonomy (~14 modules — mostly 🧪)

| Module | Purpose | Status |
|---|---|---|
| `jarvis_advanced_mutation_lane.py` | Code mutation for self-improvement | 🧪 |
| `jarvis_autonomous_goal_generator.py` | Goal generation from context | 🧪 |
| `jarvis_autonomous_task_seeder.py` | Task seeding | 🧪 |
| `jarvis_unified_autonomous_loop.py` | Unified autonomous loop | 🧪 |
| `jarvis_unified_night_bridge.py` | Night mode bridge | 🧪 |
| `jarvis_code_improvement_lane.py` | Code analysis + suggestions | 🧪 |
| `jarvis_quality_hardening.py` | QA + hardening | 🧪 |
| `ultra_upgrade_engine.py` | Ultra-upgrade mechanism | 🧪 |
| `jarvis_safe_mutation_foundation.py` | Safe mutation base | 🧪 |
| `self_improvement.py` | Block H5.4 auto-learning loop | 🧪 |
| `jarvis_memory_learning_layer.py` | Learning layer for semantic memory | 🧪 |
| `jarvis_dependency_aware_patch_planner.py` | Patch planner | 🧪 |
| `jarvis_execution_verifier.py` | Execution correctness check | 🧪 |
| `jarvis_decision_risk_engine.py` | Decision risk scoring | 🧪 |

### 9.10 Operator / Dashboard / Mesh (6 modules)

| Module | Purpose | Status |
|---|---|---|
| `dashboard_service.py` | System-metrics aggregator | ✅ |
| `jarvis_operator_task_center.py` | Operator HITL tasks | ✅ |
| `jarvis_live_operator_brain.py` | Real-time operator control | ✅ |
| `jarvis_explainability_operator_control.py` | Explainability for operator | 🧪 |
| `mesh_settings.py` | Persistent mesh-mode prefs | ✅ |
| `agent_health_mesh.py` | Per-agent health mesh (Phase 17) | ✅ |

### 9.11 Telegram & Notifications (4 modules)

| Module | Purpose | Status |
|---|---|---|
| `notifications.py` | Unified sync+async Telegram alerts (`🔔 Jarvis` prefix) | ✅ |
| `jarvis_telegram_file_tools.py` | File upload / document handling | ✅ |
| `voice_input.py` | Phase 26: Whisper transcription | ✅ |
| `telegram_task_executor.py` | Telegram task exec | 🧪 |

### 9.12 AI providers & routing (`ai/` — 13 modules)

`provider_base.py`, `provider_anthropic.py`, `provider_openai.py`, `provider_ollama.py`, `ai_router_service.py`, `task_classifier.py`, `mission_ai_orchestrator.py`, `specialized_agent_service.py`, `specialized_prompt_builder.py`, `external_executor_service.py`, `agent_dispatcher.py`, `cloud_provider_policy.py`, `provider_routing_policy_service.py` — all ✅.

### 9.13 Memory subsystem (`memory/` — 6 modules)

`auto_memory_pipeline.py`, `memory_analysis_service.py`, `obsidian_bridge_service.py`, `semantic_memory_service.py`, `mission_auto_persist_helper.py`, `mission_run_memory_hook.py` — all ✅.

### 9.14 Ecosystem & integration (~12 modules)

| Module | Purpose | Status |
|---|---|---|
| `claude_ecosystem_execution_bridge.py` | Claude ecosystem exec bridge | 🧪 |
| `claude_ecosystem_orchestrator.py` | Orchestrator | 🧪 |
| `claude_ecosystem_registry.py` | Registry | 🧪 |
| `cowork_bridge.py` | Phase 19: filesystem bridge for Claude Desktop delegation | ✅ |
| `cowork_watcher.py` | Phase 19: cowork_outbox watcher | ✅ |
| `agent_adapters.py` | Protocol adapters | ✅ |
| `agent_registry.py` | Phase 15: single source of truth (13 agents) | ✅ |
| `mcp_adapter.py`, `mcp_server.py` | Phase 25: Jarvis as MCP server | ✅ |
| `multi_ai_orchestrator_v1.py` | Multi-AI orchestration | ✅ |
| `jarvis_agent_roles_coordination.py` | Multi-agent role coord | 🧪 |
| `jarvis_external_systems_readiness.py` | External readiness checks | 🧪 |

### 9.15 Infra & utilities (~26 modules)

| Module | Purpose | Status |
|---|---|---|
| `error_reporter.py` | Phase 30: global error capture + Telegram | ✅ |
| `structured_logger.py` | JSON-lines structured logging | ✅ |
| `retry_engine.py` | Retry + backoff | ✅ |
| `continuity.py` | Stale-run recovery on startup | ✅ |
| `active_run_store.py` | Active-run heartbeat | ✅ |
| `chat_history.py` | Conversation history persistence | ✅ |
| `approval_store.py` | Approval/rejection decisions | ✅ |
| `artifact_registry.py` | Central artifact registry | ✅ |
| `task_queue.py` | Priority queue with persistence | ✅ |
| `task_executor.py`, `task_service.py` | Task exec | ✅ / 🧪 |
| `worker_loop.py`, `worker_registry.py` | Worker lifecycle | ✅ |
| `executor_registry.py`, `executor.py` | Executor registry + async exec | ✅ |
| `tool_executor.py`, `tool_executor_runtime.py` | Tool exec | ❓ / ✅ |
| `tool_chain_executor.py`, `tool_chain_planner.py` | Tool chains | ✅ |
| `tool_registry.py`, `tool_router.py` | Tool registry + routing | ✅ |
| `tool_retry_executor.py` | Tool retries | ✅ |
| `provider_health.py`, `provider_resolver.py` | Provider health | ✅ |
| `llm_router.py`, `llm_client.py` | LLM routing | ✅ |
| `claude_helper.py` | One-shot Claude helper | ✅ |
| `backend_monitor.py` | Phase 21: backend reachability | ✅ |
| `system_watchdog.py` | Phase 35: watchdog + auto-restart | ✅ (⚠️ mojibake bug — see voice audit) |
| `self_healing.py` | Phase 36: disk + memory + backups | ✅ |
| `cross_service_coordinator.py` | Block H5.7 inter-service sync | 🧪 |
| `daily_recap.py` | Block H5.2 summary | 🧪 |
| `analytics.py` | Dashboard analytics | ✅ |
| `auto_content.py` | Block H5.3 auto content | 🧪 |
| `trend_analyzer.py` | Block H5.5 pattern detection | 🧪 |
| `text_normalizer.py`, `file_parsers.py` | Text + file utilities | ✅ |
| `night_workflows.py` | Block H5.1 night engine | ✅ |
| `vision.py` | Phase 27: Claude Vision | ✅ |
| `smart_prompts.py` | Prompt enhancement | 🧪 |
| `task_marketplace.py` | Agent task marketplace | 🧪 |
| `bolt_diy_health.py`, `bolt_queue.py` | bolt.diy integration | ❓ |
| `block_l_common.py` | Block L shared utilities | ❓ |
| `jarvis_v5_content_factory.py` | V5 content factory | 🧪 |
| `jarvis_ai_engineer.py` | AI engineer mode | 🧪 |

---

## 10. Evolution — Stages → Blocks → Phases

> A chronological narrative of how the codebase reached its current shape.

### 10.1 The big picture (March → May 2026)

1. **Stages 1–2 (early March)** — Foundation. Stable core: `app/main.py` (FastAPI), `telegram_bot.py` (polling), `ai_provider.py` (multi-LLM). Intent classification + specialist routing. No memory, no compound tasks, no autonomy. **Identity was scattered across 10 files.**
2. **Block C (late April)** — Agent Mesh foundation. 7 new services. `agent_registry`, `smart_router`, `agent_health_mesh`, `result_synthesizer`, skeleton of `cowork_bridge`. Phases 13–20 (15 subsystems) prototyped. Tests 300 → 439 (+139). The architecture shifted from single-agent to mesh.
3. **Block D1 (May 1–2)** — Real mesh execution. Cowork watcher real (filesystem polling <1s + 3s fallback). Inline `/mesh` keyboard. Backend reachability monitoring. Voice/Vision/MCP stubs wired. Tests 439 → 556 (+117). **This is when Jarvis became multi-agent capable in practice.**
4. **Block D2** — Planned (n8n deep, MCP server, real LLM agent calls, scheduled tasks). Partially shipped; the focus pivoted to Block H.
5. **Blocks H4 + H5 (May 2–3, ~16h mega session)** — Telegram photo studio (`/menu_photo`, `/party_promo`, `/invite_card`, `/faceswap`, `/lora_train`, `/me_as`, etc.) + Night autonomy engine (5 phases, dashboard at `/dashboard/autonomy`). Tests jumped to ~1,460. **Inflection point: Jarvis became autonomously creative.**
6. **Phase 4 (April 30, parallel to D1)** — Identity Core unification. 10 → 1 identity definition. `BAD_IDENTITY_PATTERNS` regex guard. 67 tests. Solved "why does Jarvis answer like Claude/Perplexity?".
7. **Phase 13** — File message routing fix (`classify_file_caption`, forward messages, media-group buffering).
8. **Block L** — Creative AI: Figma MCP bridge (`/design`), bolt.diy app builder (`/create_app`), smart photo prompts (`/smart_photo`), landing brief generator (`/landing_brief`).
9. **Block M.1 (Phase A)** — Persona + Video Replicate: `/create_persona`, `/persona_photo`, `/persona_video`, `EngineRouter`, `ReplicateEngine`, `RunpodComfyEngine` stub, `PersonaVideoHandler`, `video_history`, `PersonaStorage`.
10. **Block M.2 (Phases A → C)** — RunPod ComfyUI for video. Pod lifecycle (Phase 3.0), inventory script, Wan 2.2 i2v real pipeline (Phase B), bootstrap.sh installs custom_nodes + full requirements (B.5), engine robustness (resume + auto-start), `GenerationLock` per-chat (C), bot wiring with threaded dispatch + photo intercept (C). 🟡 in-progress.
11. **Block M.2.5 (current frontier, May 9–18)** — Face-swap ReActor: package skeleton + workflow → cost estimator → face validator → ReActor engine with batch keep-pod-alive → batch orchestrator + Telegram handler → bot wiring + pod bootstrap → `get_active_backend()` + loosen OpenCV detection. 🟡 in-progress, 7 commits.
12. **Phase E.1 (latest, May 19)** — Voice audit (discovery). Read-only. Found 16 user-facing subsystems, 25+ raw-exception leaks, mojibake in `system_watchdog.py`, addressing inconsistency. Recommended 2–4 days to implement voice rewriter. See `docs/jarvis_voice_audit.md` for the full audit.

### 10.2 Status table — every named layer

| Layer | Scope | Tests | Status | Notes |
|---|---|---|---|---|
| Stages 1–2 | Foundation | ~30 | ✅ done | Stable core |
| Block C | Agent Mesh foundation | +139 | ✅ done | Phases 13, 15–20 |
| Block D1 | Real mesh + cowork | +117 | ✅ done | Mesh execution real |
| Block D2 | n8n deep + MCP + LLM agents | (plan: +120) | 🟡 partial | LLM agent calls still stubs |
| Block E | Self-improvement (scheduled tasks, parallel) | (plan: +74) | 🔵 planned | Partial overlap with H5 |
| Block F | Production polish (Docker, analytics, mobile) | (plan: +120) | 🔵 planned | Not started |
| Blocks G–H3 | Consolidation / refactor | ? | 🟢 inferred | Thin in git |
| Block H4 | Telegram photo integration | +80 | ✅ done | Photo studio + face-swap + LoRA |
| Block H5 | Night autonomy | +78 | ✅ done | 5 phases + dashboard |
| Block H8 | Heartbeat / router / safety / actions | +59 | ✅ done | Stability hardening |
| Block H9 | Single-instance / identity / rate-limit | +44 | ✅ done | |
| Block I | Multi-user + integrations (Notion/Slack/Discord) | (plan) | 🔵 planned | Designed, not started |
| Block L | Creative AI (Figma/app/photo/landing) | ~100+ | ✅ done | Fully wired |
| Block M.1 | Persona + video Replicate | 19+ | ✅ done | |
| Block M.2 | RunPod ComfyUI (Wan 2.2 i2v) | 20+ | 🟡 in-progress | Phases A–C, Phase 3.0–3.2 |
| Block M.2.5 | Face-swap ReActor | 6+ | 🟡 in-progress (current) | |
| Phase 4 | Identity Core | 67 | ✅ done | 10 → 1 unification |
| Phase 13 | File message routing | 15+ | ✅ done | |
| Phase 23 | Smart question routing (Haiku) | included | ✅ done | |
| Phase 24 | n8n deep integration | included | 🟡 partial | |
| Phase 25 | MCP server | included | 🟡 partial | Adapter live, server stub |
| Phase 26 | Voice input (Whisper) | included | ✅ done | |
| Phase 27 | Vision (Claude Vision) | included | ✅ done | |
| Phase 28 | Persistent scheduler (APScheduler) | included | ✅ done | |
| Phase 30 | Error Reporter | included | ✅ done | |
| Phase 31 | Parallel multi-agent execution | included | ✅ done | |
| Phase 35 | System Watchdog | included | ✅ (⚠️ mojibake) | |
| Phase 36 | Self-Healing | included | ✅ done | |
| Phase 3.0 | RunPod pod inventory + lifecycle | included | ✅ done | |
| Phase 3.2 | RunPod GPU prep (H200/A100/H100/L40S) | included | ✅ done | |
| Phase 3.7 | (post-snapshot endpoint update) | included | ✅ done | |
| Phase E.1 | Voice audit (discovery only) | N/A | 🔍 done | Implementation pending |

### 10.3 Named systems

| Name | What it is | Status |
|---|---|---|
| **Brain V2** | `app/routers/jarvis_brain_v2.py` + `identity_core.py`. The primary identity + routing. | ✅ active |
| **Night autonomy** | `night_workflows.py`. 5-phase scheduled engine, runs 22:00 → 06:00. | ✅ active |
| **Agent Mesh** | `smart_router.py` + 5 mesh routers, 58 endpoints, prefix `/api/agent-mesh`. | ✅ active |
| **Operator panel** | `/mesh` inline keyboard + `/agents` + `/dashboard/autonomy`. | ✅ active |
| **Cowork bridge** | Filesystem polling bridge to Claude Desktop. Sub-1s detection + 3s fallback. | ✅ active |
| **Stage3 artifacts** | Historical: `jarvis_stage3_artifacts/`. 100+ subdirs from prior experiments, kept as graveyard. | ⚠️ historical, do not extend |
| **Phase 3.0–3.7** | RunPod-related infra phases — pod lifecycle, GPU prep, endpoint snapshot. Not "blocks" in the regular sense. | ✅ done |
| **Phase E.1** | Voice rewrite project. Audit done, implementation pending. | 🔍 in design |

---

## 11. Vision — Where This Is Heading

### 11.1 Voice profile (Phase E.1 recommendation)

> [!quote] From `docs/jarvis_voice_audit.md`
> **Working name:** *Jarvis V3 — formal, dry, capable.*

| Trait | Target |
|---|---|
| Address | "вы" + occasionally "Daniil" by name. Avoid "ты" entirely. |
| Self-reference | Я — first person, never third-person. Confident assertion of agency. |
| Tone | Calm. Deferential confidence. Dry wit reserved for failure modes. Never apologetic-grovelling, never enthusiastic-bouncy. |
| Emoji | Sparing. One per message max, only when functional: ✅ ⚠️ 🚨. No 🧠 ✨ 🎭 🛠 decorative. |
| Errors | "Не получилось — {short human explanation}. Что попробуем дальше: {action}." Never raw exception. Vary phrasing. |
| Success | "Готово." or "Сделано. {1-line concrete result}." Drop the `✅ Готово!` boilerplate. |
| Length | Short by default (2–3 sentences). Long allowed only for explicit reports. |
| Identifiers | Hide by default (`task_id`, `workflow_id`, file paths). Show on `/debug`. |
| Language | Russian everywhere; English only for technical labels. |
| Humour budget | One subtle observation per ~10 messages. Failure events only — never during success. |

### 11.2 Implementation plan (2–4 engineering days)

1. Inject voice into `identity_core.JARVIS_CORE_IDENTITY` (1 file, propagates to ~10 modules).
2. Fix the watchdog mojibake (`system_watchdog.py:115, 183, 187, 216–245`).
3. Add `voice_rewriter.humanize(text, kind=...)` skeleton + static rules.
4. Wire `humanize()` into `tools/jarvis_smart_telegram_control.send()`.
5. Replace `f"Ошибка: {exc}"` pattern with a `humanize_error(exc, context)` helper (25+ sites).
6. Wire `humanize()` into `persona_handler._safe_send`.
7. Add Haiku fallback path with caching for long-tail strings.
8. Harmonise `self_healing.py` from English to Russian voice.
9. Remove the hacky English→Russian substitution table in `app/response_formatter.py:46–55`.
10. Audit `tools/photo_studio_telegram.py` (1,117 lines — still un-audited).

### 11.3 Long-arc trajectory

> What Jarvis is becoming, beyond the next two weeks.

1. **Voice-first.** Today voice messages are transcribed to text. Next: Jarvis speaks back. Whisper out, ElevenLabs / Claude Voice in. A real conversation loop.
2. **Proactive, not reactive.** Today every action starts with a user message. The night autonomy engine is the prototype of the inverse: Jarvis decides to act, then tells the operator what it did. Block H5 has the scaffolding; the long-term goal is to extend that to daytime — "I noticed your Saturday party has no poster yet, want me to draft three?"
3. **Multi-modal by default.** Photos in (Claude Vision), photos out, video in (face-swap), video out (Wan 2.2). Audio in (Whisper), audio out (TBD). Eventually screen-share or live camera for restaurant ops ("how does this plating look?").
4. **One conversation, every business.** Restaurant ops, events business, personal AI persona, content factory, and "personal Tony Stark assistant" all live in the same chat with the same identity.
5. **Increasing autonomy with stronger guards.** As autonomy grows, so do `jarvis_truth_guard`, `risk_policies`, `safe_executor`, `shell_guard`, `approval_store`. The system is being built to *earn* more autonomy by demonstrating safety first.
6. **Memory that compounds.** Obsidian vault + semantic memory + decision log → Jarvis remembers what worked, what didn't, who Daniil is, what's happening in the businesses. Two years of memory should be more valuable than the LLM weights themselves.
7. **Lower-cost compute.** Today: Replicate is the easy path (per-call billing). Trajectory: self-hosted on RunPod (Wan 2.2 on H200 NVL at $0.50/h, then home-server when GPU is bought) to make compute fixed-cost.
8. **External ecosystem.** MCP server lets Claude Desktop drive Jarvis. n8n lets external triggers drive Jarvis. Future: Discord/Slack/website webhook → same brain.

### 11.4 The Stark-Jarvis test

A working name for the success criterion: a friend or investor opens the Telegram chat, asks a few things, and the experience should feel less like "a chatbot wired to a bunch of APIs" and more like "talking to an assistant that knows the business, the operator, and how to act." Voice (E.1), memory compounding, and proactive autonomy are the three open variables.

---

## 12. Tests & Maturity

### 12.1 Numbers

- **Test files:** 117
- **Test functions:** ~1,976 (across 110 files; 7 files appear to be helpers/fixtures)
- **Total lines:** ~26,265
- **Config:** `pytest.ini` defines `@pytest.mark.integration` marker (skipped by default unless `-m integration`)
- **No conftest.py** in `tests/` directory
- **No `@pytest.mark.xfail`** — no known-broken tests

### 12.2 Coverage by subsystem (top groups)

| Subsystem | Files | Tests | Maturity |
|---|---|---|---|
| Block M (persona / content) | 9 | 101 | ⭐ mature |
| Block H4 — Telegram photo studio | 1 | 85 | ⭐ mature |
| Block H5 — Night autonomy | 1 | 78 | ⭐ mature |
| Routing (3 smart routers) | 3 | 99+ | ⭐ mature |
| Scheduler + core exec | 1 | 51 | ⭐ mature |
| Photo Studio foundation | 1 | 51 | ⭐ mature |
| Telegram identity integration | 1 | 50 | ⭐ mature |
| Block H8 handlers (heartbeat, router, safety, actions) | 5 | 59 | ⭐ mature |
| Block H9 handlers (instance, identity, rate-limit) | 5 | 44 | stable |
| Figma / design system | 7 | 65 | stable |
| Face Swap (SwapBatch + Replicate) | 6 | 105 | stable |
| Landing generators | 3 | 42 | stable |
| Decision Log + wiring | 2 | 39 | stable |
| Integration tests (I1–I4) | 4 | 19 | minimal |
| Identity E2E | 2 | 39 | conditional (skipped if server not up) |
| n8n integration | 1 | 26 | minimal |
| RunPod integration | 4 | 9 | minimal (mostly mocked) |

### 12.3 What has NO test coverage (140+ critical modules)

- **AI/LLM core:** `llm_client`, `llm_router`, `claude_ecosystem_*`, `jarvis_thinking_layer`, `jarvis_ai_engineer`
- **Execution (~60 modules):** `task_executor`, `mission_*`, `supervisor_*`, `tool_executor_*`, `executor_registry`
- **Autonomy (15):** `jarvis_autonomous_*`, `jarvis_unified_*`, `jarvis_live_operator_brain`
- **n8n (8):** `n8n_client`, `n8n_bridge`, `n8n_workflow_materializer*`, `n8n_specialist`
- **Storage (10):** `chat_history`, `context_manager`, `semantic_memory`, `artifact_registry`
- **Infra (20+):** `provider_resolver`, `retry_engine`, `safe_executor`, `risk_policies`, `planner`

> [!warning] Roughly **65–70% of the service layer is untested**. The well-tested parts are the **user-facing** subsystems (Block M, H4, H5, routing, photo studio). The plumbing is mostly empirical.

---

## 13. Open Questions & Decisions Pending

> Most of these are blockers for finishing Phase E.1 (voice rewrite) or Phase 3 (repo cleanup).

1. **Address style.** "вы" vs "ты" vs name? Audit recommends "вы" + occasionally "Daniil". Needs confirmation.
2. **Emoji policy.** Audit recommends sparing + functional only. Confirm or override.
3. **English fallback language.** When LLM is unavailable, should hand-written replies stay Russian or switch to English?
4. **Identifier visibility.** Hide `task_id` / `workflow_id` / file paths by default, expose under `/debug`? Or keep visible to the operator?
5. **n8n super agent default text** (`"Hello from Jarvis n8n Super Agent"` line 268). Is this string parsed downstream, or safe to rewrite?
6. **YouTube subsystem.** Daniil mentions YouTube channel management with shorts creation/upload — **no YouTube API integration is in this repo**. Likely manual, or an out-of-tree n8n workflow. Needs confirmation. If it should be a Jarvis feature, the integration is greenfield.
7. **Watchdog alerts language.** Russian (with mojibake fixed) or English (technical-monitor convention)?
8. **Restaurant / party / landing marketing copy.** This output goes to *end customers*, not Daniil. Keep its energetic/emoji tone and only rewrite "Jarvis explains what he did"?
9. **Voice latency budget.** OK to add ~200–400 ms (cached Haiku call) per outbound message? Or strict zero-latency (static rules only)?
10. **`JARVIS_VOICE_BYPASS=1` env flag** for debugging — acceptable design?
11. **Phase 3 cleanup decisions.** Several memory-module duplicates, n8n router duplicates, and `app/main.py` bugs (lines 3, 7, 18, 221, 222) are pending decisions (see `PROGRESS.md`). Should the cleanup proceed before or after Phase E.1?
12. **`tools/photo_studio_telegram.py` (1,117 lines)** — not yet audited. Worth a dedicated read.

---

## 14. Operational Reality

### 14.1 Repository facts

- **Path:** `C:\jarvis` (Windows)
- **Python:** 3.11+ via `.venv/Scripts/python.exe`
- **Default FastAPI port:** 8010 (older docs/scripts mention 8015 — that's stale)
- **Working branch:** `phase-3.0-inventory-stop-reliability`
- **Main branch:** `main`
- **Git user:** Daniil Lapin
- **Git safety net:** Phase 1–2 cleanup checkpoint `d63d5e0` (everything is recoverable)

### 14.2 Top-level docs that are still load-bearing

| File | What it is |
|---|---|
| `RUN_JARVIS.md` | How to run; troubleshooting |
| `ROUTERS_STATUS.md` | 43 routers, 321 endpoints — startup snapshot |
| `ENDPOINTS_LIVE.md`, `ENDPOINTS_LIVE_AFTER_3_7.md` | Endpoint listings |
| `AGENT_MESH_PLAN.md` | Why all 5 mesh routers are needed |
| `IDENTITY_DISCOVERY.md` | The 10-places audit that birthed `identity_core.py` |
| `CAPABILITY_DISCOVERY.md` | Provider + tool + content-type inventory (slightly outdated) |
| `PROGRESS.md` | Repo cleanup status (Phases 1–2 done, 3 pending) |
| `AUDIT.md`, `PHASE3_ANALYSIS.md`, `DELETION_PLAN.md` | Cleanup planning artifacts |
| `docs/jarvis_voice_audit.md` | Phase E.1 voice audit — the most recent and most actionable doc |
| `docs/roadmap_v1_to_v2.md` | Original V1 → V2 vision |
| `docs/runpod_phase3.md` | RunPod Phase 3.0–3.2 detail |
| `BLOCK_D_PLAN.md`, `BLOCK_D2_PLAN.md`, `BLOCK_E_PLAN.md`, `BLOCK_F_PLAN.md`, `BLOCK_I_PLAN.md` | Block-level plans |
| `SESSION_SUMMARY_BLOCK_*.md` (×10) | Per-session retrospectives |
| `MEMORY_PLAN.md`, `N8N_PLAN.md` | Subsystem plans |
| `NIGHT_AUTONOMY_GUIDE.md`, `PHOTO_STUDIO_GUIDE.md`, `PHOTO_QUALITY_GUIDE.md` | User guides for major subsystems |
| `MCP_SERVER_SETUP.md` | MCP integration with Claude Desktop |
| `CLOUDFLARE_TUNNEL_SETUP.md`, `HOME_SERVER_SETUP.md`, `COWORK_BRIDGE_SETUP.md` | Infra setup |
| `MESH_CONTROL_GUIDE.md` | Operator guide |
| `CLAUDE_CODE_FIGMA_PROMPT.md` | Figma prompt template |

### 14.3 Things to NOT touch

- `jarvis_stage3_artifacts/` — historical, ~100+ subdirs. Read for context, don't extend.
- Any `backup_*` directory or `*.bak` / `*.before_*.py` file — Phase 1 cleanup left some for safety.
- `app/services/n8n_*` and the duplicate memory modules — marked for Phase 3 cleanup but blocked on decisions.
- Generated content under `data/`, `state/`, `artifacts/`, `logs/`, `panel_logs/` — runtime output.

### 14.4 Useful one-liners

```powershell
# Backend health
curl http://127.0.0.1:8010/health

# Run all tests (quiet)
.\.venv\Scripts\python.exe -m pytest tests/ -q

# Only integration tests
.\.venv\Scripts\python.exe -m pytest tests/ -m integration

# Live endpoint snapshot
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8015

# Pod inventory (RunPod)
.\.venv\Scripts\python.exe scripts/_pod_count.py
```

---

## 15. Tech Stack

> What the code actually depends on. Source: `requirements.txt` (no `pyproject.toml` / `setup.py` — pip + requirements only), `pytest.ini`, `.env*`, top-level scripts.

### 15.1 Runtime

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.11+ (`runpod/pytorch:2.4.0-py3.11-…` base image) |
| Process manager | PowerShell launcher | `start_jarvis.ps1` (Windows-first) |
| Web framework | **FastAPI** | 0.135.3 |
| ASGI server | **uvicorn** | 0.44.0 |
| Underlying stack | Starlette | 0.47.3 |
| Validation | **pydantic** + **pydantic-settings** | 2.13.0 / 2.13.1 |
| Async HTTP | **httpx** (primary) + **aiohttp** | 0.28.1 / 3.13.5 |
| Sync HTTP | requests | 2.33.1 |
| Scheduling | **APScheduler** | 3.11.2 |
| Filesystem watcher | watchdog + watchfiles | 6.0.0 / 1.1.1 |
| WebSockets | websockets | 16.0 |
| Crypto | cryptography | 46.0.7 |
| Date parsing | dateparser + python-dateutil + pytz | 1.4.0 / 2.9.0 / 2026.1 |

### 15.2 SDK clients (the "branded" deps)

| SDK | What for | Version |
|---|---|---|
| **anthropic** | Claude (Messages API + Vision) | 0.97.0 |
| **openai** | GPT + Whisper transcription | 2.33.0 |
| **replicate** | Image + video + LoRA + face-swap (Replicate-side) | 1.0.7 |
| **pyTelegramBotAPI** | Bot framework | 4.32.0 |
| **google-api-python-client** | Sheets, Calendar, Gmail, Drive | 2.194.0 |
| google-auth-oauthlib | OAuth flows for Google | 1.3.1 |
| google-auth-httplib2 | Google transport | 0.3.1 |
| google-api-core / googleapis-common-protos | Google base | 2.30.3 / 1.74.0 |

> [!note] Notably **absent** — services called via raw `httpx` instead of an SDK:
> - **Ollama** — raw HTTP to `/api/generate`
> - **Perplexity** — raw HTTP to `api.perplexity.ai/chat/completions`
> - **Tavily** — raw HTTP to `api.tavily.com/search`
> - **RunPod** — raw GraphQL to `api.runpod.io/graphql`
> - **ComfyUI** — raw HTTP to pod's `:8188`
> - **n8n** — raw HTTP to cloud / self-hosted REST + webhooks
> - **Figma** — raw HTTP to `api.figma.com/v1`
>
> Everything funnels through `httpx.AsyncClient` so retries / timeouts / proxy URLs (Phase B.3 fix) are uniform.

### 15.3 File parsing

| Format | Library | Version |
|---|---|---|
| Excel | openpyxl | 3.1.5 |
| PDF | pdfplumber + pdfminer.six + pypdfium2 | 0.11.9 / 20251230 / 5.7.1 |
| DOCX | python-docx | 1.2.0 |
| Images | Pillow | 12.2.0 |
| XML | lxml + defusedxml | 6.1.0 / 0.7.1 |

### 15.4 Testing

| Tool | Version |
|---|---|
| pytest | 9.0.3 |
| pytest-asyncio | 1.3.0 |
| pluggy | 1.6.0 |
| iniconfig | 2.3.0 |
| Config | `pytest.ini` defines `@pytest.mark.integration` (skipped by default — opt-in via `-m integration`) |

### 15.5 External infrastructure

| Service | How invoked |
|---|---|
| **RunPod** (GPU pods) | GraphQL API → pod boot → `bootstrap.sh` on Linux → ComfyUI on `:8188` |
| **n8n self-hosted** | Docker compose `n8n_docker_strong/docker-compose.yml` (n8n-main, n8n-worker, PostgreSQL, Redis Bull queue) |
| **n8n cloud** | `daniliyc.app.n8n.cloud` via REST + webhooks |
| **Ollama** (optional) | Local server `http://127.0.0.1:11434` (`llama3.1:latest` default) |
| **ComfyUI** (optional local) | `http://127.0.0.1:8188` |
| **Obsidian vault** | Filesystem only — Markdown writes to `C:\Users\Daniil Lapin\Documents\JarvisVault` |
| **Cloudflare Tunnel** | Remote access to home server / RunPod pods |
| **MCP** (Claude Desktop) | JSON-RPC 2.0 over stdio via `scripts/run_mcp_server.py` |

### 15.6 What's notably NOT in the stack

- No ORM. State is JSON / JSONL files in `state/` + a SQLite file for semantic memory.
- No Celery / RQ / Dramatiq. Async work uses `asyncio.create_task` + APScheduler + the filesystem-based cowork watcher.
- No Redis directly from Python — only used by n8n internally.
- No Prometheus / Grafana — observability is `structured_logger.py` → JSON lines + Telegram alerts via `notifications.py`.
- No CI configured in-repo (no `.github/workflows`, no `Jenkinsfile`). Tests run locally.
- No Dockerfile for Jarvis itself (only for the n8n stack).
- No Linux daemon / systemd unit — `start_jarvis.ps1` is the launcher and Windows is the target host.

---

## 16. Cost Structure

> All prices in USD. Source-of-truth: `app/services/replicate_models.py`, `app/services/block_m2_face_swap/cost_estimator.py`, `app/services/block_m_common/cost_tracker.py`, `.env.runpod.example`.

### 16.1 Per-action costs (single operation)

| Operation | Cost | Notes |
|---|---|---|
| **LoRA training** (`ostris/flux-dev-lora-trainer`) | **$10.00** | ~20 min wall-clock. Charged once per persona. |
| FLUX 1.1 Pro (still) | $0.04 | Default for objects / abstract. |
| FLUX 1.1 Pro Ultra (still) | $0.06 | Premium — people / landscapes. |
| FLUX Redux (img2img) | $0.025 | Style transfer / enhance. |
| FLUX LoRA generation | $0.03 | Once a LoRA exists. |
| PhotoMaker | $0.05 | Character consistency without LoRA. |
| Face swap (basic — `omniedgeio`) | $0.005 | Quick swap. |
| Face swap (polished — `codeplugtech`) | $0.01 | Higher quality. |
| GFPGAN face polish | $0.002 | Post-process. |
| **M.2.5 face-swap on RunPod (per photo)** | $0.02 | `SWAPBATCH_SWAP_USD_PER_PHOTO` (env-overridable). |
| **M.2.5 animate (per video)** | $0.27 | `SWAPBATCH_ANIMATE_USD_PER_VIDEO`. |
| M.2.5 cold start | $0.05 | `SWAPBATCH_COLD_START_USD` — billed once per batch. |
| Replicate Kling v2.1 (video) | per-second | Charged by Replicate per second of output. |
| Wan 2.5 I2V Fast (Replicate) | per-second | Same model — billed per second. |
| **Wan 2.2 i2v on RunPod ComfyUI** | per-GPU-minute | $0.50/h (H200 NVL) → $1.19/h (A100) → varies. Pod lifetime capped at 60 min. |
| Anthropic Claude (API) | per token | Sonnet 4.5 — input / output tokens per provider sheet. |
| OpenAI GPT-4.1 | per token | Same. |
| OpenAI Whisper | per minute | Voice transcription. |
| Claude Vision | per image | Per Anthropic Vision sheet. |
| Perplexity sonar-pro | per request | Research with citations. |
| Tavily search | per request | Free tier covers light use. |
| Ollama (local) | $0 | Local llama3.1, no API cost — only electricity. |

### 16.2 Daily budget guardrails

| Cap | Value | Where enforced |
|---|---|---|
| **Replicate / persona daily limit** | **$10.00 USD** | `DAILY_LIMIT_USD` in `app/services/block_m_common/cost_tracker.py:12`. Raises `DailyLimitExceeded` when reached. |
| **RunPod daily budget** | **$3.00 USD** | `RUNPOD_MAX_BUDGET_USD_PER_DAY` in `.env.runpod.example`. Enforced by `runpod_guardian.py` (auto-stops pods, sends Telegram alert). |
| **RunPod pod lifetime** | **60 min** | `RUNPOD_MAX_POD_LIFETIME_MIN`. Hard kill after 1 h regardless of activity. |
| **RunPod emergency stop** | enabled | `RUNPOD_EMERGENCY_STOP_ENABLED=true` |
| **RunPod health check interval** | 30 s | `RUNPOD_GUARDIAN_CHECK_INTERVAL_SEC` |

> [!warning] Two separate budgets — they don't add up automatically. Replicate spending is tracked in `state/personas/expenses.jsonl` (UTC days); RunPod spending is tracked by the guardian against the pod price book. If both run hot, the operator can spend ~$13/day before either ceiling trips.

### 16.3 Typical operation cost profiles

| Scenario | Approx total | Notes |
|---|---|---|
| One-time persona setup | ~$10.50 | LoRA training $10 + ~10 seed photos × $0.05 |
| Daily "make me 4 menu photos" | ~$0.16 | 4 × FLUX Pro $0.04 |
| Daily "make me 4 LoRA persona photos" | ~$0.12 | 4 × FLUX LoRA $0.03 |
| Event poster set (8 themes) | ~$0.48 | 8 × FLUX Pro Ultra $0.06 |
| Face-swap batch of 10 photos + animate (RunPod) | ~$3.10 | Cost report shown to user before run |
| 30 s Wan 2.5 I2V Fast video (Replicate) | ~$0.30–$0.60 | Depends on resolution |
| Full Wan 2.2 i2v on RunPod (60-min pod, A100) | ~$1.19 | Hard ceiling |
| Internet research (Perplexity sonar-pro) | ~$0.001–$0.005/query | Per request |
| Daily night-autonomy recap | ~$0.05–$0.20 | Mostly Claude Sonnet for summarisation |

### 16.4 Approximate monthly envelope (at current usage)

> Rough — not measured, derived from per-action prices × stated daily caps.

| Lane | Lower bound | Upper bound |
|---|---|---|
| Replicate (capped at $10/day) | ~$50 | **$300** (if maxed every day) |
| RunPod (capped at $3/day) | ~$10 | **$90** |
| Claude + OpenAI (LLM tokens) | ~$10 | ~$50 |
| Perplexity + Tavily | ~$5 | ~$20 |
| Google Workspace | $0 | $0 (Workspace plan — out of scope) |
| Telegram | $0 | $0 |
| **Total** | **~$75** | **~$460** |

### 16.5 Cost-optimisation opportunities

1. **Move recurring video gen from Replicate (Wan 2.5 Fast) → RunPod ComfyUI (Wan 2.2 i2v).** RunPod's hourly billing beats per-call once you cross ~10 videos/day. The infrastructure to do this is Block M.2 — *the cost-optimisation move IS one of the active workstreams.*
2. **Cache seed photos.** `block_m23_polish/seed_cache.py` exists (🧪) but isn't wired everywhere. Re-using seed photos across personas of similar archetype could cut $0.04 × N per training run.
3. **Default to Ollama for casual chat / status questions** — currently the OpenAI path is preferred for "chat / control / mission / research" intent. Routing simple greetings to local Ollama is free.
4. **Use Claude Haiku for `quick_answer`** (already done — Phase 23). Haiku is ~10× cheaper than Sonnet for factual one-shots.
5. **Batch face-swap before animating.** The M.2.5 flow already separates `swap` ($0.02/photo) from `animate` ($0.27/video) and asks the user to confirm before animating — the "animate yes / no" choice is the single biggest cost lever in the system.
6. **Tighten the RunPod GPU preference order.** Current order: RTX PRO 6000 Blackwell → L40S → H200 NVL ($0.50/h) → A100 ($1.19/h) → H100 → A40. H200 NVL at $0.50/h is the cheapest H-class card and should be preferred whenever supply allows. Already partially done (Phase 3.2 prep #4).
7. **Skip GFPGAN polish unless requested.** $0.002 × hundreds of generations adds up. The polish is opt-in but the default in some pipelines is "always polish."
8. **Lower `RUNPOD_MAX_POD_LIFETIME_MIN` for face-swap batches.** Face-swap doesn't need 60 min; ~15 min would be enough and would cap the worst-case runaway pod at ~$0.30 instead of ~$1.20.

### 16.6 Telegram chat lock (cost guardrail)

The bot only responds to `TELEGRAM_ALLOWED_CHAT_ID`. This is a cost guardrail as much as a security one — if the token leaks, an attacker can't drain the Replicate budget by spamming `/persona_photo`. See `tools/jarvis_smart_telegram_control.py` and the chat-id check in `app/telegram_bot.py`.

---

## 17. Stats & Metrics

> Snapshot 2026-05-19. Numbers derived from `find` + `wc` over the working tree (excluding `.venv/`, `.git/`, `__pycache__/`, `jarvis_stage3_artifacts/`).

### 17.1 Code

| Metric | Value |
|---|---|
| Total non-archive `.py` files | **488 in `app/`** + 117 in `tests/` + 72 in `tools/` + ~331 in `scripts/` (mixed `.py` + `.ps1`) |
| Total non-archive Python LOC | **~57,859 lines** |
| Service modules (`app/services/*.py` top-level) | **156** |
| Service modules (with subpackages: `block_m*/`, `ai/`, `memory/`, `execution/`) | **220** |
| Router files (`app/routers/`) | 35 |
| API router files (`app/api/`) | 37 |
| **Total router files** | **72** |
| Routers actually loaded at startup (per `ROUTERS_STATUS.md`) | **43** |
| HTTP endpoints (live) | **321** |
| Handlers (`app/handlers/`) | 3 (`persona_handler.py`, `persona_video_handler.py`, `face_swap_handler.py`) |
| Agent files (`app/agents/`) | 7 (architect, backend-fix, design-to-code, full-creator, night-mode-strategist, qa, base) |
| Top-level Markdown docs | 50+ |

### 17.2 Tests

| Metric | Value |
|---|---|
| Test files (`tests/test_*.py`) | **117** |
| Test functions (`def test_…`) | **~1,976** |
| `pytest.ini` markers | `integration` (skipped by default) |
| `conftest.py` files in `tests/` | 0 (none) |
| `@pytest.mark.xfail` | 0 (no known-broken tests intentionally skipped) |
| Conditional skips | identity_e2e.py — skips if backend not running |
| **Estimated coverage** | ~30–35% of service-layer modules have a matching test file. The well-tested zones are user-facing (Block M, H4, H5, routing, photo studio); plumbing is mostly empirical. See [[#12 Tests & Maturity]]. |

### 17.3 Git

| Metric | Value |
|---|---|
| Current branch | `phase-3.0-inventory-stop-reliability` |
| Main branch | `main` |
| HEAD | `529d558 docs: Phase E.1 voice audit (discovery)` |
| Recent focus | 9 commits on Block M.2.5 face-swap (2026-05-09 → 2026-05-18) |
| Phase 1 cleanup checkpoint | `d63d5e0` ("Initial state before consolidation") — full safety-net for rollbacks |
| Working-tree state at brief generation | 1 modified file (`brain_v2_state.json`) + ~25 untracked research JSONs + a few untracked debug scripts |

### 17.4 External assets / state

| Path | Size / count |
|---|---|
| `jarvis_stage3_artifacts/` | 100+ subdirectories, historical artifacts |
| `state/` | runtime state — personas, cowork inbox/outbox/archive, conversation memory, daily recaps, decisions, designs, figma queue, games, etc. |
| `data/block_m2_video/` | runtime video pipeline outputs |
| `artifacts/` | task / mission output artifacts |
| `logs/`, `panel_logs/` | runtime logs |
| `n8n_docker_strong/`, `n8n_local/` | Docker compose + local n8n state |
| Backup `.env` files at repo root | 5 (`.env.backup_*`, `.env.before_path_fix_*`, `.env.stage3`) — kept for safety |
| `backup_*` directories at repo root | 11 from the Phase 1–2 cleanup; safe to delete but kept for audit |
| Largest single source file | `tools/jarvis_smart_telegram_control.py` — **5,272 lines** (the "real" bot) |
| Second-largest | `tools/photo_studio_telegram.py` — **1,117 lines** (not yet voice-audited) |

### 17.5 "Tony Stark gap" (capability vs personality)

> Discussed in [[#11 Vision — Where This Is Heading]]. Quantified roughly:

| Axis | Score (1–5) | Comment |
|---|---|---|
| Capability surface | **5/5** | 60+ commands, 321 endpoints, multi-modal, autonomous loops, multi-provider LLM, GPU compute |
| Reliability | 3/5 | Watchdog + self-healing exist, but ~65–70% of plumbing untested |
| Voice / personality | **2/5** | Audit done (E.1), implementation pending. Today: clinical, terse, inconsistent addressing. |
| Memory continuity | 3/5 | Obsidian vault + SQLite semantic memory exist. Not yet promoted to identity-shaping. |
| Proactive behaviour | 3/5 | Night autonomy is the prototype. Daytime is still reactive. |
| Voice I/O | 1/5 | Whisper-in only. No TTS out. No wake word. |
| Vision (input) | 3/5 | Claude Vision wired for photos. No webcam / screen share. |
| Multi-device | 1/5 | Single Telegram chat on one machine. |

---

## 18. Known Issues & Tech Debt

> Compiled from `docs/jarvis_voice_audit.md`, `PROGRESS.md`, `AUDIT.md`, `PHASE3_ANALYSIS.md`, recent commit messages, and on-the-fly grep. File:line citations where available.

### 18.1 Critical (block user experience or make alerts unreadable)

1. **Mojibake in watchdog alerts.** `app/services/system_watchdog.py:115, 183, 187, 216, 220, 232, 242` — Russian strings stored as corrupted CP1251→UTF-8 bytes (`РџСЂРёС‡РёРЅР°` instead of `Причина`). Visible to operator *exactly when something is broken*. Phase E.1 voice audit recommends fixing during voice rewrite.
2. **Raw exception text leaked to user.** 25+ instances of `f"Ошибка: {exc}"` pattern in `tools/jarvis_smart_telegram_control.py` (lines 3455, 3592, 3634, 3739, 3772, 3829, 4230, 4246, 4262, 4278, 4294, 4310, 4326, 4377, 4393, 4409, 4425, 4441, 4457, 4473, 4489, 4505, 4564), plus `app/telegram_bot.py:187, 193` and `app/handlers/persona_handler.py:291, 342, 361, 411, 433, 451, 496, 565, 627, 693, 759, 809, 851`. User sees `KeyError: 'figma_url'` style messages.
3. **`NameError` bug in internet table builder.** `app/services/jarvis_telegram_file_tools.py:262` — `f"Jarvis internet table\nQuery: {query}\nRows: {len(rows)}"` references undefined `rows` (should be `smart_rows` or `rows_count`). Will raise at runtime, replacing the success message with a Python crash.
4. **PowerShell escape leaked into Python source.** `app/telegram_bot.py:320` — `f"Событие создано:\`n{result.get('html_link')}"` — literal backtick-n instead of `\n`. User sees `\n` as raw text.

### 18.2 Moderate (works, but inconsistent or surprising)

5. **Inconsistent Telegram addressing.** Same file mixes «ты» and «вы». `app/handlers/persona_handler.py:374` (ты — `Сначала сгенерируй`) sits 14 lines from `:388` (вы — `Ответьте "да" / "нет"`). No documented convention.
6. **`response_formatter.py:46–55`** — hand-coded English→Russian word substitution table runs on every reply. Hacky workaround for LLM language drift. Should be replaced by a proper voice prompt.
7. **English alerts in self-healing.** `app/services/self_healing.py:292, 299` — `"Disk low ({free_gb:.1f}GB free) — cleaned {cleaned} log files"`. Inconsistent with the rest of the (mostly Russian) operator surface.
8. **Internal identifiers leaked to the user.** `task_id`, `workflow_id`, `decision_id`, `chat_id`, file paths (`scheduled_tasks.json`), API endpoint names — visible in many command replies. Acceptable for power-user mode but should be hidden by default.
9. **Telegram bot duplication.** Two bot entry points coexist: `tools/jarvis_smart_telegram_control.py` (5,272 lines — the real one) and `app/telegram_bot.py` (the thin polling client). The thin client still points to port 8010 in some configs; legacy comments mention port 8015. Source of "Backend DOWN" confusion.

### 18.3 Bootstrap / setup

10. **`start_jarvis.ps1` reports success unreliably.** The launcher opens 2 windows but does not verify they came up healthy. Operator sometimes sees "started" but `/health` fails. Phase F item.
11. **ReActor bootstrap may be outdated** (Block M.2.5 in progress). Recent commits (`7e72146 get_active_backend() + loosen OpenCV detection`) suggest the bootstrap.sh path is still hardening. `inswapper_128.onnx` auto-download is the open item.
12. **`transformers` version compatibility with the pod's `torch`.** Phase 3.2 commit (`7d38935`) added `sqlalchemy` + import verification for ComfyUI v0.20+ to bootstrap.sh, but transformers/torch version pinning is still empirical.
13. **`.env.example` is incomplete.** Missing: `REPLICATE_API_TOKEN`, `RUNPOD_API_KEY`, `N8N_BASE_URL`, `N8N_API_KEY`, `FIGMA_API_KEY`, `INFLUENCER_*`, `JARVIS_OBSIDIAN_VAULT_PATH`, `MCP_*`. Newcomer would need to grep the codebase to find them.

### 18.4 `app/main.py` issues (from `AUDIT.md` — pending Phase 3.4)

14. **Line 3:** `from __future__ import annotations` is not the first statement → `SyntaxError` risk after refactors.
15. **Lines 7 + 18:** double import of `time_brain`.
16. **Line 221:** unprotected bare-import of `n8n_action_router_materializer_router`. If the import fails, the whole app fails to start. Should be inside a `try/except`.
17. **Line 222:** related — must be edited before `n8n_action_router_materializer_router.py` can be safely deleted in Phase 3.3.

### 18.5 Duplicates pending decisions (`PHASE3_ANALYSIS.md`)

18. **`app/main_local_n8n_bridge.py`** vs `app/n8n_stage_b_sidecar*.py` — duplicate main candidates.
19. **`app/api/memory_layer.py`** vs `app/models/memory_layer.py` — duplicate name.
20. **`app/services/semantic_memory.py`** vs `app/services/memory/semantic_memory_service.py` — duplicate functionality.
21. **`app/api/mission_run_memory.py`** vs `app/models/mission_run_memory.py` — duplicate name.
22. **n8n routers:** `n8n_bridge_router.py` vs `jarvis_n8n_bridge_router.py`; `n8n_workflow_materializer_router.py` vs v2; `jarvis_n8n_specialist_router.py` vs `jarvis_n8n_super_agent_router.py`.

### 18.6 Test gaps

23. **~140 service modules have no test file.** AI/LLM core, ~60 execution modules, 15 autonomy modules, 8 n8n modules, 10 storage modules, 20+ infra modules. See [[#12.3 What has NO test coverage]].
24. **No CI.** No `.github/workflows`, no Jenkinsfile. Tests run locally on Daniil's machine. If a refactor breaks something un-tested, it's caught at runtime.

### 18.7 Operational

25. **Repo size — backups.** 11 `backup_*` directories + 5 `.env.backup_*` + `*.before_*.py` snapshots still in the working tree (Phase 1 cleanup partially done). Adds ~10 MB to clones; harmless but cluttered.
26. **`jarvis_stage3_artifacts/`.** 100+ historical subdirectories. Kept intentionally as the decision graveyard but contributes ~hundreds of files to greps. `jarvis_stage3_artifacts/internet_tools/` is still being appended to at runtime (~25 untracked `research_*.json` files added since 2026-05-09).
27. **No GitHub remote / off-site backup verified.** `gh repo view` is not configured in this repo. Backup bundle exists at `C:\jarvis_backup_20260518_182401\jarvis_repo.bundle` (mentioned in scope) but no automated push. Single point of failure.
28. **Mixed line endings.** `.gitattributes` was added (commits `09f38a6`, `7d16511`) to force LF on `*.py` and `runpod/*.sh`, but historical files may still have CRLF.

### 18.8 Open from the voice audit (`docs/jarvis_voice_audit.md`)

29. **`tools/photo_studio_telegram.py` (1,117 lines)** — not yet audited. Likely has the same exception-leak / addressing inconsistencies as the other handlers.
30. **n8n super-agent generated text** — `app/services/jarvis_n8n_super_agent.py:268` default `"Hello from Jarvis n8n Super Agent"`. Unclear if downstream consumers parse on this exact string.
31. **Restaurant / party / landing marketing copy voice** — goes to end customers, not Daniil. Needs decision: keep current energetic / emoji tone, or apply voice rewriter only to "Jarvis explains what he did" messages?
32. **Watchdog alert language.** Russian (after mojibake fix) or English (monitoring-tool convention)?

### 18.9 Architectural debt

33. **Two parallel orchestrators.** `app/services/multi_ai_orchestrator_v1.py` (older, with `JARVIS_SYSTEM_PROMPT` / `CLAUDE_HINT` / `OPENAI_HINT`) coexists with `app/services/ai/ai_router_service.py` (newer, uses `AI_ROUTER_*` env vars and `claude-sonnet-4-5` / `gpt-4.1` / `llama3.1:latest`). Identity unification (Phase 4) bridged them, but they remain separate code paths.
34. **Two AI provider layers.** `app/ai_provider.py` (legacy SDK wrapper) vs `app/services/ai/provider_*.py` (newer per-provider classes). Phase 4 was supposed to migrate everything to `identity_core`; some legacy callers persist.
35. **Memory subsystem fragmentation.** `conversation_memory.py`, `semantic_memory.py`, `memory/semantic_memory_service.py`, `memory/auto_memory_pipeline.py`, `memory/obsidian_bridge_service.py`, `mission_memory.py`. Each was added for a specific use case; consolidation is an open project (`MEMORY_PLAN.md`).

> [!warning] The combined weight of items 14–22 + 33–35 is why **Phase 3 cleanup is on pause** — every "obvious" deletion needs upstream caller analysis first. This is also why a future "Tony Stark" Jarvis with stable memory and identity needs the consolidation done first.

---

## 19. Glossary

| Term | Meaning |
|---|---|
| **Block** | A multi-session unit of work (Block C, D1, D2, H4, H5, L, M.1, M.2, M.2.5…). Each block solves one major operator pain. |
| **Phase** | A finer-grained unit inside a Block (Phase A, B, C…) or a numbered cross-cutting initiative (Phase 4 = identity unification; Phase 25 = MCP server; Phase 30 = error reporter…). |
| **Stage** | The earliest layering (Stages 1–4). Largely subsumed by Blocks now. The `jarvis_stage3_artifacts/` directory is the residue. |
| **Brain V2** | `app/routers/jarvis_brain_v2.py` — the primary brain router + identity guard. |
| **Agent Mesh** | The 5-router, 58-endpoint system that runs multiple agents in parallel and synthesises results. |
| **Cowork bridge** | A filesystem polling mechanism that lets Jarvis hand a task to Claude Desktop (or another agent) and pick up the result. |
| **Night autonomy** | A 5-phase scheduled engine that runs during quiet hours and posts a daily recap. |
| **Mission** | A unit of work with persistence, memory, lock, resume, and result packaging. The core execution primitive. |
| **Persona** | A named AI character with seed photos + LoRA + history. Block M.1 / M.2 / M.2.5. |
| **Me-Persona** | A persona specifically of Daniil himself (`/me_seed`, `/me_done`, `/me_as`). Block M.2.2. |
| **Operator** | Daniil. The only authorised user (`TELEGRAM_ALLOWED_CHAT_ID`). |
| **Engineer mode** | One of the six modes in `identity_core` (control / brain / engineer / research / mission / n8n). |
| **MCP** | Model Context Protocol. Jarvis exposes itself as a server so Claude Desktop can call it. |

---

## 20. Cross-links to deep reads

- [[#8 The Identity Core — Jarvis's Self-Definition]] — start here for what Jarvis *is*
- [[#11 Vision — Where This Is Heading]] — for what it's *becoming*
- [[#3 Architecture at 30,000 ft]] — for the shape
- [[#6 Telegram Surface — The Operator's Cockpit]] — for the day-to-day capability
- [[#10 Evolution — Stages → Blocks → Phases]] — for how we got here
- [[#15 Tech Stack]] — for what to install / what's actually imported
- [[#16 Cost Structure]] — for what each operation costs and the daily caps
- [[#17 Stats & Metrics]] — for verified counts of code, tests, endpoints
- [[#18 Known Issues & Tech Debt]] — for the punch list of what's broken or owed

External (in repo):
- [`docs/jarvis_voice_audit.md`](jarvis_voice_audit.md) — the most actionable doc right now
- [`app/services/identity_core.py`](../app/services/identity_core.py) — 200 lines that define everything
- [`IDENTITY_DISCOVERY.md`](../IDENTITY_DISCOVERY.md) — the audit that birthed `identity_core`
- [`AGENT_MESH_PLAN.md`](../AGENT_MESH_PLAN.md) — mesh router rationale
- [`PROGRESS.md`](../PROGRESS.md) — repo cleanup status
- [`RUN_JARVIS.md`](../RUN_JARVIS.md) — how to actually run it

---

#jarvis-v3 #project-brief #vision #onboarding #memory #obsidian
#telegram-bot #fastapi #anthropic #openai #replicate #runpod #comfyui #n8n #figma #google-workspace #mcp
#block-m #block-h5 #phase-e1 #identity-core #agent-mesh #night-autonomy #cowork-bridge
#daniil-lapin #kyiv #restaurant #events
