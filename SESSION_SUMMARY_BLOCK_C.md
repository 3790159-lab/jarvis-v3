# SESSION_SUMMARY_BLOCK_C.md — Block C: Agent Mesh Foundation

Date: 2026-05-01  
Duration: ~3 часа  
Tests: 300 → 439 (+139)  
Commits: 7

---

## Phase 13.fix — Critical File Message Fix

### Что было сломано
- Бот получал файл с caption "просмотри что это за файлы" → отвечал research'ем
- Root cause: `classify_message()` не знала что мы в контексте файла
- "расскажи про" в caption матчилась research_triggers (строка 563)
- file_triggers были слишком строгими (только "суммируй файл", "кратко про")

### Как починили
1. **Новая функция `classify_file_caption(caption)`** — file-aware router
   - "просмотри", "посмотри", "взгляни", "расскажи про", "что это" → summarize_file
   - "извлеки", "вытащи" → extract_from_file
   - "найди в", "есть ли в" → ask_about_file
   - "бухгалтер", "накладная" → accounting
   - DEFAULT для любого непустого caption → summarize_file (разумный default)

2. **`_handle_file_message` использует `classify_file_caption`** вместо `classify_message`

3. **Forward message support** — пересланные файлы обрабатываются как обычные

4. **media_group buffering** — пачка из нескольких файлов обрабатывается группой (2s window)

5. **Расширены file_triggers** в `classify_message` — "расскажи про файл", "проанализируй файл"

### Phase 13 теперь работает в реальности
✅ Файл + caption "просмотри" → summarize_file  
✅ Файл + caption "извлеки данные" → extract_from_file  
✅ Пересланное сообщение с файлом → обработан  
✅ media_group из 2 файлов → оба обработаны  

---

## Phase 15 — Agent Registry

**Файл**: `app/services/agent_registry.py`

### 13 агентов в реестре:
| Агент | Тип | Capabilities |
|---|---|---|
| internet_research | api_tool | web_search, research, fact_checking |
| smart_table | api_tool | excel, comparison_tables, data_aggregation |
| claude_coder | llm | code_generation, architecture_design, refactoring |
| openai_reasoner | llm | reasoning, classification, summarization |
| perplexity_researcher | llm_tool | web_research_with_sources, current_events |
| file_processor | api_tool | pdf_parsing, docx_parsing, xlsx_parsing, ocr |
| ai_engineer | api_tool | architecture_review, risk_analysis, ai_planning |
| image_generator | api_tool | image_generation |
| video_generator | api_tool | video_generation |
| n8n_workflow | external_workflow | scheduled_automation, service_integration |
| obsidian_writer | local_tool | note_creation, knowledge_base |
| google_drive | api_tool | file_upload, cloud_storage |
| cowork_file_agent | external_app | file_organization, desktop_automation (⏸ Block D) |

### Helpers:
- `get_agent(id)` → config
- `list_agents_by_capability(cap)` → agent IDs
- `estimate_cost(plan)` → "free"/"low"/"medium"/"high"
- `check_all_agents_health()` → dict

### Migration:
- `CAPABILITY_REGISTRY` legacy view генерируется из AGENTS (backwards compat)

---

## Phase 16 — Smart Router

**Файл**: `app/services/smart_router.py`

### Flow:
```
query → analyze_task() → requirements
requirements → select_agents() → agents[]
agents → build_execution_plan() → ExecutionPlan
plan → execute_plan() → results
results → synthesize_results() → final_text
```

### Compound detection:
- "найди X и сделай таблицу" → compound_task
- "исследуй, а потом оформи" → compound_task
- Multi-verb patterns: (найди + оформи) → compound

### Bot integration:
- intent="compound_task" + mesh_enabled=True → `_handle_mesh_task`
- `/agents` — live agent health status
- `/mesh on/off/debug/history` — Smart Router control

### Telegram UX:
```
🧠 Анализирую задачу...
📋 План (2 шага):
  [1] интернет-исследование
  [2] таблицы Excel/CSV
🚀 Выполняю...
✅ [1/2] интернет-исследование: готово (8.2s)
✅ [2/2] таблицы Excel/CSV: готово (12.1s)
✨ Готово!
[synthesized result]
```

---

## Phase 17 — Provider Health Mesh

**Файл**: `app/services/agent_health_mesh.py`

- Per-agent health checks (HTTP ping / API key / webhook)
- `check_all_agents()` → health dict for всех 13 агентов
- `get_healthy_agent_for_capability(cap)` → first healthy agent
- `select_agents_with_fallback(caps)` → map cap → best healthy agent
- `agents_status_text()` → grouped /agents output
- Background daemon (60s interval)

---

## Phase 18 — Result Synthesizer

**Файл**: `app/services/result_synthesizer.py`

- `synthesize_results(query, agent_results)`:
  - Single result → pass through (with sources/file hints)
  - Multi results → LLM synthesis via backend
  - Fallback: structured concatenation с заголовками
  - Unrelated results → честное "агенты вернули несвязанные результаты"
- `format_for_telegram(content)` → 4000-char limit
- `format_agent_error(agent_id, error)` → labeled error

---

## Phase 19 prep — Cowork Bridge Skeleton

**Файл**: `app/services/cowork_bridge.py`

### Filesystem bridge:
```
state/cowork_inbox/   ← Jarvis пишет задачи
state/cowork_outbox/  ← Cowork пишет результаты
state/cowork_archive/ ← Обработанные
```

### API:
- `make_task(instruction, context, deadline_sec)` → task dict
- `send_task_to_cowork(task)` → task_id
- `get_task_result(task_id)` → result или None
- `poll_cowork_results()` → все готовые результаты
- `mark_result_processed(task_id)` → move to archive
- `cancel_task(task_id)` → remove from inbox
- `inject_fake_result(task_id, text)` → для тестирования
- `submit_and_wait(instruction, timeout)` → blocking helper

**COWORK_BRIDGE_SETUP.md** — полная документация для пользователя с system prompt template.

---

## Phase 20 — Agent Mesh UI

- `/mesh on/off/debug/history` — полный контроль Smart Router
- `/agents` — группированный статус (LLMs/Tools/Content/Integrations/External)
- `/status` — расширен: mesh_enabled + кол-во mesh executions
- `/help` — обновлён: секции Agent Mesh + Files
- `mesh_history` в state — последние 20 executions

---

## Статистика сессии

| Метрика | Значение |
|---|---|
| Тестов до | 300 |
| Тестов после | 439 |
| Добавлено тестов | +139 |
| Коммитов | 7 |
| Новых файлов | 8 |
| Phase 13 работает? | ✅ ДА |

---

## Roadmap Block D

### Priority 1: Cowork integration (полная)
- Watchdog мониторинг inbox/outbox через `watchdog` library
- `/cowork <задача>` команда в боте
- Auto-routing сложных файловых задач через cowork_file_agent
- Progress updates пока Cowork работает

### Priority 2: n8n Deep Integration
- `/n8n list` — список активных workflows
- `/n8n run <name>` — запуск workflow
- Webhook callbacks обратно в Telegram
- Smart Router integration

### Priority 3: MCP Adapter
- Model Context Protocol adapter для Jarvis агентов
- Claude Desktop видит Jarvis как MCP server
- Tools: search, table, file_analysis

### Priority 4: Full mesh execution
- execute_plan реально вызывает каждый агент через их endpoint
- Result synthesis использует real LLM (not research endpoint fallback)
- Streaming progress updates в Telegram

### Priority 5: Scheduled tasks
- "/remind me in 2h" → cron job в state
- Daily summary → Obsidian
- Weekly stats → Telegram
