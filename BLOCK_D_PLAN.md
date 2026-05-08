# BLOCK_D_PLAN.md — Cowork Bridge + n8n Deep + MCP Adapter

Date: 2026-05-01  
Status: planning  

---

## Overview

Block D делает Jarvis по-настоящему multi-agent:
- Cowork (Claude Desktop) обрабатывает сложные файловые задачи
- n8n получает full bidirectional integration
- Jarvis становится MCP server для Claude Desktop

---

## ЧАСТЬ 1: Full Cowork Integration (Phase 21)

### 1.A Watchdog мониторинг

```python
# pip install watchdog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class CoworkOutboxWatcher(FileSystemEventHandler):
    def on_created(self, event):
        if event.src_path.endswith(".json"):
            task_id = Path(event.src_path).stem
            result = get_task_result(task_id)
            # notify Jarvis → deliver to user
```

Запускается как daemon thread при старте бота.
Реакция на новый файл в outbox < 1 секунды.

### 1.B /cowork команда в боте

```
/cowork организуй файлы в ~/Downloads по датам
/cowork создай expense report из PDF квитанций
/cowork что я делал сегодня (читает ~/Documents)
```

Routing:
- Если `cowork_file_agent.available = True` → send_task_to_cowork
- Если нет → честная ошибка + инструкция по настройке

### 1.C Auto-routing

В Smart Router, если required_capabilities включают:
- `file_organization`, `expense_reports`, `doc_creation` → cowork_file_agent
- Если cowork недоступен → fallback к file_processor

### 1.D Task lifecycle с UX

```
📤 Отправляю задачу в Cowork...
⏳ Cowork работает... (polling every 3s)
✅ Готово! [result]
```

Timeout 5 минут. Если timeout → "⚠️ Cowork не ответил. Проверь Claude Desktop."

### 1.E Watchdog polling (fallback без watchdog)

Если watchdog не установлен — polling каждые 3 секунды в отдельном thread.

### Tests (20+)
- Watchdog fires on new outbox file
- /cowork routes to cowork_file_agent
- timeout behavior
- fallback when cowork unavailable

---

## ЧАСТЬ 2: n8n Deep Integration (Phase 22)

### 2.A n8n Client расширение

```python
# app/services/n8n_deep_client.py
list_workflows() → [{"id", "name", "active"}]
run_workflow(workflow_id, params) → {"execution_id"}
get_execution_status(exec_id) → {"status", "data"}
wait_for_execution(exec_id, timeout) → result
```

### 2.B /n8n команды

```
/n8n list — список workflows
/n8n run <name_or_id> — запуск
/n8n status <exec_id> — статус выполнения
/n8n enable <id> / disable <id>
```

### 2.C Webhook callbacks → Telegram

n8n workflow завершился → POST Jarvis /api/jarvis/webhooks/n8n-callback →
Jarvis отправляет уведомление в Telegram.

Endpoint:
```
POST /api/jarvis/webhooks/n8n-callback
Body: {"execution_id": "...", "status": "success", "result": {...}}
```

### 2.D Smart Router integration

analyze_task → если contains "n8n"/"workflow"/"автоматизация" →
Smart Router выбирает n8n_workflow agent → 
execute_plan запускает workflow.

### Tests (15+)

---

## ЧАСТЬ 3: MCP Adapter (Phase 23)

### Что такое MCP

Model Context Protocol — стандарт Anthropic для подключения инструментов к LLM.
Claude Desktop + Jarvis как MCP Server = Claude видит все возможности Jarvis как tools.

### 3.A Jarvis MCP Server

```python
# app/mcp_server.py
from mcp import Server, Tool

server = Server("jarvis")

@server.tool("internet_research")
def research(query: str) -> str:
    """Search the internet and return answer with sources."""
    ...

@server.tool("create_table")
def create_table(query: str) -> str:
    """Create Excel table from internet data."""
    ...

@server.tool("analyze_file")
def analyze_file(file_path: str) -> str:
    """Analyze document and return summary."""
    ...
```

### 3.B Claude Desktop config

```json
// claude_desktop_config.json
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

### 3.C Exposed tools

| Tool | Description |
|---|---|
| internet_research | Web search + Perplexity |
| create_excel_table | Smart table with LLM columns |
| analyze_document | PDF/DOCX/XLSX analysis |
| generate_image | InfluencerStudio image gen |
| obsidian_write | Write to Obsidian vault |
| n8n_trigger | Trigger n8n workflow |
| send_telegram | Send message to Telegram |

### Tests (10+)

---

## ЧАСТЬ 4: Full Mesh Execution (Phase 24)

### 4.A Real agent calling

Currently execute_plan stubs non-HTTP agents. In Phase 24:
- llm agents → direct API calls (anthropic/openai SDK)
- n8n_workflow → n8n_deep_client.run_workflow()
- obsidian_writer → obsidian_bridge_service
- cowork_file_agent → cowork_bridge.submit_and_wait()

### 4.B Streaming progress

Telegram edit_message → live update:
```
[1/3] ⏳ Perplexity ищет...
[1/3] ✅ Perplexity: 5 источников (8.2s)
[2/3] ⏳ Smart Table формирует...
[2/3] ✅ Smart Table: 12 строк в xlsx (15.1s)
[3/3] ⏳ Google Drive загружает...
[3/3] ✅ Google Drive: https://... (3.2s)
✨ Готово!
```

### 4.C Result routing

Если plan вернул file_path → send_document to Telegram  
Если drive_url → send as link  
Если text → send as message  
Если job_id → poll + notify when done  

---

## ЧАСТЬ 5: Scheduled Tasks (Phase 25)

### 5.A /remind команда

```
/remind через 2 часа сделать X
/remind в 18:00 позвонить Y
/remind завтра утром отчёт
```

Хранится в `state/reminders.json`.
Background thread проверяет каждую минуту.
Доставка через Telegram.

### 5.B Daily summary

Каждый день в 23:59 → Jarvis отправляет:
- Статистика дня (из structured_logger)
- Топ 3 intent за день
- Summary → Obsidian vault

### 5.C Weekly mesh report

Каждое воскресенье → Telegram:
- Кол-во запросов за неделю
- Самые используемые агенты
- Avg latency
- Предложения по оптимизации

---

## Testing Strategy Block D

### Unit tests (per phase)
- Mock filesystem watchers
- Mock n8n API
- Mock MCP transport
- Mock real LLM calls

### Integration tests
- Cowork inbox → outbox lifecycle (with tmp_path)
- n8n webhook callback flow
- MCP tool registration

### Manual acceptance tests (user-led)
1. Send PDF to Telegram → "просмотри" → get analysis
2. "/cowork организуй Downloads" → Claude Desktop acts → Telegram result
3. "найди топ AI и сделай таблицу" → mesh: research + table
4. "/n8n run daily_report" → workflow executes → Telegram notification
5. Ask Claude Desktop "найди что-то" → uses Jarvis internet_research tool

---

## Dependencies to install for Block D

```
watchdog>=4.0.0          # Filesystem events
mcp>=1.0.0               # Model Context Protocol
anthropic>=0.40.0        # Direct Claude API calls
openai>=1.0.0            # Direct OpenAI API calls
```

---

## Estimated time Block D

| Phase | Hours |
|---|---|
| 21: Cowork full | 1.5h |
| 22: n8n deep | 1.5h |
| 23: MCP adapter | 1.5h |
| 24: Full mesh exec | 1h |
| 25: Scheduled tasks | 1h |
| **Total** | **~6.5h** |
