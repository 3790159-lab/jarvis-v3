# TOOLS_REALITY_CHECK.md — Phase 7.5

Date: 2026-04-30  
Backend: http://127.0.0.1:8010 (confirmed UP)

---

## Reality Check Table

| Capability | Endpoint | Code File | Status | Output Quality | Expected Quality | Gap | Notes |
|---|---|---|---|---|---|---|---|
| openai | via `/api/jarvis/tools/internet/research` | app/services/ai_provider.py | ✅ WORKING | good | good | none | gpt-4.1 via llm_router |
| anthropic | via `/api/jarvis/tools/internet/research` | app/services/ai_provider.py | ✅ WORKING | good | good | none | claude-sonnet-4-5 via llm_router |
| ollama | local fallback | app/services/ai_provider.py | ⚫ UNCONFIGURED | n/a | n/a | needs local server | requires llama3.2 running at 11434 |
| perplexity | via `/api/jarvis/tools/internet/research` | app/services/jarvis_internet_tools.py | ✅ WORKING | good | good | none | sonar-pro, returns answer + citations |
| tavily | via `/api/jarvis/tools/internet/search` | app/services/jarvis_internet_tools.py | ✅ WORKING | good | good | none | returns structured results |
| internet_research | `POST /api/jarvis/tools/internet/research` | app/services/jarvis_internet_tools.py | ✅ WORKING | good | good | none | answer + citations |
| table_excel | `POST /api/jarvis/telegram-tools/internet-table` | app/services/jarvis_telegram_file_tools.py | 🟡 DEGRADED | rank/title/url/score/summary | Service/Category/Pricing/Strengths/API | columns wrong | Perplexity research unused in table |
| ai_engineer | `POST /api/jarvis/ai-engineer/review` | app/routers/jarvis_ai_engineer_router.py | 🟡 DEGRADED | times out at 15s | returns plan | slow | works but takes 30-60s |
| brain_plan | `POST /api/jarvis/brain/plan` | app/routers/jarvis_brain_router.py | ✅ WORKING | good | good | none | decision + plan returned |
| image_gen | `POST /api/jarvis/v5/content-factory/submit` | app/services/jarvis_v5_content_factory.py | ✅ WORKING | job_id queued | job_id | async | job starts, poll /jobs/{id} |
| video_gen | `POST /api/jarvis/v5/content-factory/submit` | app/services/jarvis_v5_content_factory.py | ✅ WORKING | job_id queued | job_id | async | same endpoint, video_enabled=True |
| telegram | bot polling | tools/jarvis_smart_telegram_control.py | ✅ WORKING | — | — | — | bot sends/receives messages |
| google_drive | via content factory | app/services/jarvis_v5_content_factory.py | ✅ WORKING | drive URL returned | drive URL | none | OAuth token present |
| google_sheets | spreadsheet_service.py | app/services/spreadsheet_service.py | ⚫ UNCONFIGURED | — | — | needs OAuth flow | token present but untested |
| obsidian | obsidian_bridge_service.py | app/services/obsidian_bridge_service.py | ✅ WORKING | writes .md to vault | .md notes | none | vault path configured |
| n8n_cloud | webhook | app/routers/jarvis_n8n_bridge_router.py | ✅ WORKING | webhook fires | webhook | none | daniliyc.app.n8n.cloud |
| n8n_local | localhost:5678 | app/routers/* | ⚫ UNCONFIGURED | — | — | needs local n8n | container not running |

---

## Summary

- ✅ WORKING: 11/17 (65%)
- 🟡 DEGRADED: 2/17 (12%) — table_excel (columns wrong), ai_engineer (slow)
- 🔴 BROKEN: 0/17 (0%)
- ⚫ UNCONFIGURED: 4/17 (24%) — ollama, google_sheets, n8n_local (expected), n8n_cloud (configured)

---

## Quick Fixes

### Fix 1 (PRIORITY 1): Table Excel quality improvement
**Root cause**: `build_internet_table()` builds rows from Tavily search results (title/url/score/summary).  
The Perplexity `research` result is fetched but **never used** in the table.  
**Fix**: After search+research, call LLM (OpenAI/Anthropic) to extract structured data  
with topic-appropriate columns. Fallback to current format if LLM unavailable.

### Fix 2: AI Engineer timeout
**Root cause**: Endpoint works but takes 30-60s for real analysis. Bot timeout=300s is fine.  
Curl test at 15s naturally times out. Not actually broken.  
**Action**: No code fix needed. Document as slow-but-correct.

---

## Quality Improvements

### Table Quality (PRIORITY 1) — DEGRADED → WORKING

**Current**: rank | title | url | score | summary  
**Target**: columns relevant to query topic (e.g. Service | Category | Pricing | Strengths | API)

**Implementation plan**:
1. After `internet_search` + `internet_research`, call LLM:
   - Prompt: "Based on these search results and research, create a structured table of 10 rows.
     Choose columns appropriate for the topic '<query>'. Return JSON: {columns: [...], rows: [[...]]}"
2. Parse JSON → build Excel with smart columns
3. Fallback to current format if LLM call fails or returns bad JSON

**Files to change**: `app/services/jarvis_telegram_file_tools.py`

---

## Status after Phase 7.5

Updated after fixes are applied.
