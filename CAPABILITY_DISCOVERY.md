# CAPABILITY_DISCOVERY.md — Phase 6.1

Date: 2026-04-30  
Status: Discovery complete, design pending

---

## AI Providers

| Provider | Status | Key env var | Configured? | Used for |
|---|---|---|---|---|
| **OpenAI** | AVAILABLE | `OPENAI_API_KEY` | ✅ yes | Dialogue, classification, fast reasoning |
| **Anthropic (Claude)** | AVAILABLE | `ANTHROPIC_API_KEY` | ✅ yes | Architecture, deep code, engineering |
| **Ollama** | PARTIAL | `OLLAMA_BASE_URL` | ✅ configured, requires local server | Local fallback (llama3.2:latest) |
| **Perplexity** | AVAILABLE | `PERPLEXITY_API_KEY` | ✅ yes | Internet research (sonar-pro) |
| **Tavily** | AVAILABLE | `TAVILY_API_KEY` | ✅ yes | Web search (structured results) |

### Provider routing logic (multi_ai_orchestrator_v1.py)
- `engineer / architect / n8n` intent → Anthropic preferred → OpenAI fallback
- `chat / control / mission / research` intent → OpenAI preferred → Anthropic fallback
- All → Ollama local fallback if no external keys

### AI Router (app/services/ai/) — separate code path
- `AI_ROUTER_OPENAI_MODEL=gpt-4.1`
- `AI_ROUTER_ANTHROPIC_MODEL=claude-sonnet-4-5`
- `AI_ROUTER_OLLAMA_MODEL=llama3.1:latest`

---

## Internet Tools

All registered in `app/services/jarvis_internet_tools.py::internet_health()`:

| Tool ID | Provider | What it does |
|---|---|---|
| `internet.search` | Tavily | Basic web search, up to N results with snippets |
| `internet.research` | Perplexity sonar-pro | Deep research with citations |
| `internet.find_pipelines` | Tavily + Perplexity | Pipeline discovery for a given topic |
| `internet.compare_services` | Perplexity | Scored service comparison (quality, cost, API, automation) |
| `internet.engineer_brief` | Tavily + Perplexity | Architecture + implementation plan for a task |

HTTP endpoint: `POST /api/jarvis/tools/internet/research`, `POST /api/jarvis/tools/internet/search`

---

## Content Types

| Type | Format | Provider | Status | Where |
|---|---|---|---|---|
| **Images** | JPEG/PNG (9:16) | InfluencerStudio API | AVAILABLE (key configured) | `jarvis_v5_content_factory.py` |
| **Video** | MP4 | Kling-3 via InfluencerStudio | AVAILABLE | `jarvis_v5_content_factory.py` |
| **Excel tables** | .xlsx | openpyxl | AVAILABLE | `jarvis_telegram_file_tools.py` |
| **CSV tables** | .csv | stdlib | AVAILABLE | `jarvis_telegram_file_tools.py` |
| **Markdown reports** | .md | LLM-generated | AVAILABLE | AI Engineer, research tools |
| **Obsidian notes** | .md vault | Local filesystem | AVAILABLE (vault path configured) | `obsidian_bridge_service.py` |
| **JSON artifacts** | .json | stdlib | AVAILABLE | Every tool (saved to `jarvis_stage3_artifacts/`) |

### Content delivery paths
- Images/Video → Google Drive (`drive` OAuth scope) + folder URL returned
- Tables (Excel/CSV) → Telegram Bot API (`sendDocument`) directly to chat
- Reports → local `artifacts/` + artifact_path in response
- Obsidian notes → `JARVIS_OBSIDIAN_VAULT_PATH=C:\Users\Daniil Lapin\Documents\JarvisVault`

---

## Tools (backend endpoints in active use by bot)

| Bot intent | Endpoint called | Function |
|---|---|---|
| `research` | `POST /api/jarvis/tools/internet/research` | Perplexity research |
| `table` | `POST /api/jarvis/telegram-tools/internet-table` | Excel/CSV table → Telegram file |
| `brain` | `POST /api/jarvis/brain/plan` | Think + internet decision |
| `engineer` | `POST /api/jarvis/ai-engineer/review` | Architecture review + safe plan |
| `generate` | `POST /api/jarvis/v5/content-factory/submit` | Image + video generation |
| `job` | `GET /api/jarvis/v5/content-factory/jobs/{id}` | Job status |
| `health` | `GET /health` + 5 sub-checks | System health check |
| `identity` | _local, no backend call_ | Phase 5 Identity Guard |
| `capabilities` | _local, no backend call_ | Static capabilities text |

---

## Integrations

| Integration | Status | Config | Notes |
|---|---|---|---|
| **Telegram Bot** (smart control) | AVAILABLE | `TELEGRAM_BOT_TOKEN` + `ALLOWED_CHAT_ID` | `tools/jarvis_smart_telegram_control.py` — the active bot |
| **Telegram Bot** (legacy) | PARTIAL | `TELEGRAM_BACKEND_URL=:8010` | `app/telegram_bot.py` — points to port 8010, not 8015 |
| **Google Drive** | AVAILABLE | `google_oauth_token_drive.json` | For content factory uploads |
| **Google Sheets** | PARTIAL | OAuth + service account configured | `spreadsheet_service.py`, requires openpyxl + googleapis |
| **Google Workspace** | PARTIAL | All JSON keys present | Calendar, Gmail, Drive, Docs — configured but not all tested |
| **Obsidian Vault** | AVAILABLE | `JARVIS_OBSIDIAN_VAULT_PATH` set | Mission memory → Markdown notes |
| **n8n Cloud** | AVAILABLE | `N8N_BASE_URL=daniliyc.app.n8n.cloud` + `N8N_API_KEY` | Webhook trigger, improvements log |
| **n8n Self-hosted** | PARTIAL | `JARVIS_N8N_SELF_HOST_BASE_URL=:5678` | Local n8n server (requires running) |

---

## Identified Root Causes of Phase 5 Regressions

### Bug 1: "С какими AI агентами работаешь?" → identity_answer

Cause: `"работаешь"` is in `IDENTITY_TRIGGERS` — too broad.  
"работаешь" appears in presence checks ("ты работаешь сейчас?") but also in capability questions ("с какими агентами работаешь?", "как это работаешь?").

Fix required: Replace `"работаешь"` with `"ты работаешь"` in IDENTITY_TRIGGERS.

### Bug 2: "Какой контент ты создаёшь?" → Perplexity research

Cause: Not caught by any early-exit trigger. Falls to `raw.endswith("?")` → intent=research → Perplexity.  
The question is about capabilities, not about external facts.

Fix required: Add capability-question triggers to catch "какой контент", "что создаёшь", "что генерируешь", "с какими агентами", etc.

### Bug 3: identity_answer + capabilities_text duplicate

Cause: Both list what Jarvis can do. identity_answer() was designed to say "who I am", but in practice became a capabilities list.

Fix: `identity_answer()` → name/version/owner only, plus "напиши 'что ты умеешь?' для деталей".  
`capabilities_text()` → replace static text with data pulled from CAPABILITY_REGISTRY.

---

## Files that define capabilities (current state)

```
tools/jarvis_smart_telegram_control.py
└── capabilities_text()          ← hardcoded static text, not from registry
└── IDENTITY_TRIGGERS            ← Phase 5, has "работаешь" false positive
└── identity_answer()            ← too detailed, duplicates capabilities

app/services/jarvis_internet_tools.py
└── internet_health()            ← authoritative list of internet tool IDs

app/services/jarvis_external_systems_readiness.py
└── build_default_registry()     ← ExternalCapability dataclasses
                                    (telegram, n8n, google_workspace, http, filesystem)

app/services/jarvis_v5_content_factory.py
└── health()                     ← lists: modes, api key presence, drive token

app/routers/jarvis_brain_router.py
└── health()                     ← lists: brain.think, brain.plan, brain.internet_decision

app/routers/jarvis_ai_engineer_router.py
└── health()                     ← lists: ai_engineer.review, ai_engineer.recommend_provider
```
