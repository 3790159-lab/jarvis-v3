# BLOCK_D2_PLAN.md — n8n Deep + MCP + Voice/Vision + Full Mesh

Date: 2026-05-01  
Status: planning  
Prerequisite: Block D1 complete (556 tests, 5 commits)

---

## ЧАСТЬ 1: n8n Deep Integration (Phase 23)

### 1.A n8n Deep Client

```python
# app/services/n8n_deep_client.py
list_workflows()           → [{"id", "name", "active"}]
run_workflow(id, params)   → {"execution_id"}
get_execution(exec_id)     → {"status", "data"}
wait_for_execution(id, t)  → result dict
toggle_workflow(id, active)→ ok bool
```

### 1.B /n8n команды

```
/n8n list               — список активных workflows
/n8n run <name_or_id>   — запустить workflow
/n8n status <exec_id>   — статус выполнения
/n8n enable/disable <id>
```

### 1.C Webhook callback → Telegram

```
POST /api/jarvis/webhooks/n8n-callback
Body: {"execution_id": "...", "status": "success", "result": {...}}
```

Jarvis отправляет уведомление в Telegram при завершении workflow.

### 1.D Smart Router integration

"автоматизируй" / "workflow" / "n8n" → select n8n_workflow agent  
→ n8n_deep_client.run_workflow()

### Tests (20+)

---

## ЧАСТЬ 2: MCP Server (Phase 24)

### 2.A Jarvis как MCP Server

```python
# app/mcp_server.py
from mcp import Server, Tool

server = Server("jarvis")

@server.tool("internet_research")
def research(query: str) -> str: ...

@server.tool("create_table")
def create_table(query: str) -> str: ...

@server.tool("analyze_file")
def analyze_file(file_path: str) -> str: ...
```

### 2.B Claude Desktop config

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["path/to/app/mcp_server.py"],
      "env": {"JARVIS_BACKEND": "http://127.0.0.1:8010"}
    }
  }
}
```

### 2.C Exposed tools

| Tool | Description |
|------|-------------|
| internet_research | Web search + Perplexity |
| create_excel_table | Smart table |
| analyze_document | PDF/DOCX analysis |
| send_telegram | Send message to user |
| n8n_trigger | Trigger n8n workflow |

### Tests (15+)

---

## ЧАСТЬ 3: Full Mesh Execution (Phase 25)

### 3.A Real LLM agent calls

Currently `_call_agent` returns stub for LLM agents. In Phase 25:
- `claude_coder` → direct Anthropic API call
- `openai_reasoner` → direct OpenAI API call
- `perplexity_researcher` → direct Perplexity API call

### 3.B Streaming progress via edit_message

```
[1/3] ⏳ Perplexity ищет...
[1/3] ✅ Perplexity: 5 источников (8.2s)
[2/3] ⏳ Smart Table формирует...
```

### 3.C Result routing

- file_path → send_document to Telegram
- drive_url → send as link
- text → send as message

### Tests (15+)

---

## ЧАСТЬ 4: Voice/Vision Real (Phase 26)

### 4.A Whisper integration

```python
import openai
def transcribe_voice(audio_path: str) -> str:
    with open(audio_path, "rb") as f:
        return openai.audio.transcriptions.create(
            model="whisper-1", file=f
        ).text
```

### 4.B Claude Vision

```python
def analyze_image(image_path: str, prompt: str = "") -> str:
    # Claude claude-sonnet-4-6 vision API
    ...
```

Photos без caption → analyze_image (Phase 13.5)

### Tests (10+)

---

## ЧАСТЬ 5: Scheduled Tasks (Phase 27)

### 5.A /remind

```
/remind через 2ч сделать X
/remind в 18:00 позвонить Y
/remind завтра утром отчёт
```

Хранится в `state/reminders.json`.
Background thread проверяет каждую минуту.

### 5.B Daily summary (автоматически)

Каждый день в 23:59 → Jarvis отправляет:
- Статистика дня
- Топ 3 intent
- Summary → Obsidian vault

### Tests (10+)

---

## Dependencies for Block D2

```
mcp>=1.0.0               # Model Context Protocol
anthropic>=0.40.0        # Direct Claude API
openai>=1.0.0            # Direct OpenAI + Whisper
```

---

## Estimated Time

| Phase | Hours |
|-------|-------|
| 23: n8n deep | 1.5h |
| 24: MCP Server | 1.5h |
| 25: Full mesh exec | 1h |
| 26: Voice/Vision real | 1h |
| 27: Scheduled tasks | 1h |
| **Total** | **~6h** |

---

## Success Criteria

- [ ] `/n8n run daily_report` → executes → Telegram result
- [ ] Claude Desktop "найди что-то" → uses Jarvis MCP → answer
- [ ] Compound task executes with real LLM calls (no stubs)
- [ ] Voice message → transcribed → processed
- [ ] `/remind через 1ч` → delivered on time
- [ ] 650+ total tests
