# ROUTERS_STATUS.md — Карта роутеров при старте

Дата: 2026-04-30  
Источник: `python -m uvicorn app.main:app --port 8015`  
Итог: **321 endpoint**, сервер здоров (`GET /health` → `{"status":"healthy"}`)

---

## LOADED — 43 роутера успешно подключены

### Прямые (до startup, до FastAPI app)
| Роутер | Тег |
|---|---|
| `app.routers.multi_ai_orchestrator_v1` | Jarvis Multi-AI Orchestrator V1 |
| `app.routers.jarvis_brain_v2` | Jarvis Brain V2 |
| `app.routers.jarvis_live_operator` | jarvis-live-operator |
| `app.routers.operator_dashboard` | operator-dashboard |
| `app.routers.time_brain` | time-brain |
| `app.routers.claude_ecosystem` | claude-ecosystem |

### Через startup (с try/except)
| Роутер | Тег |
|---|---|
| `app.api.ai_router` | ai-router |
| `app.api.goals_router` | goals |
| `app.api.endpoints.missions` | missions |
| `app.api.missions` | missions |
| `app.api.responses` | responses |
| `app.api.specialized_agents` | specialized-agents |
| `app.api.google_tools` | google |
| `app.api.artifacts` | artifacts |
| `app.api.dashboard` | untagged |
| `app.api.memory` | untagged |
| `app.api.memory_layer` | memory-layer |
| `app.api.mission_ai_bridge` | mission-ai |
| `app.api.mission_artifacts` | mission-artifacts |
| `app.api.mission_graph` | mission-graph |
| `app.api.mission_memory_bridge` | mission-memory |
| `app.api.mission_run_memory` | mission-run-memory |
| `app.api.multi_step_execution` | multi-step-execution |
| `app.api.obsidian_bridge` | obsidian-bridge |
| `app.api.policy` | untagged |
| `app.api.provider_routing_policy` | provider-routing |
| `app.api.runtime_bridge` | runtime-bridge |
| `app.api.external_executor` | external-executor |
| `app.api.worker` | untagged |
| `app.api.cloud_control` | cloud_control |
| `app.api.compat_legacy` | compat_legacy |
| `app.api.execution_planner` | execution_planner |
| `app.autonomy.router` | autonomy |
| `app.autonomy.mission_bridge` | autonomy-bridge |
| `app.routers.multistep` | multistep |
| `app.routers.resume_recovery` | resume_recovery |
| `app.routers.tools_runtime` | tools_runtime |
| `app.routers.tool_routing` | tool_routing |
| `app.routers.tool_chains` | tool_chains |
| `app.routers.artifacts_runtime` | artifacts_runtime |
| `app.routers.agent_control_plane` | agent_control_plane |
| `app.api.agent_mesh_router` | agent-mesh |
| `app.api.agent_mesh_plus_router` | agent-mesh-plus |
| `app.api.agent_mesh_stability_router` | agent-mesh-stability |
| `app.api.agent_mesh_real_exec_router` | agent-mesh-real-exec |
| `app.api.agent_mesh_improvement_router` | agent-mesh-improvement |
| `app.routers.n8n_bridge_router` | n8n_bridge |
| `app.routers.n8n_action_router_materializer_router` | n8n-materializer |
| `app.routers.n8n_action_router_materializer_v2_router` | n8n-materializer-v2 |

### Hard-импорты после startup (без try/except — исправлены в Phase 3.2)
| Роутер | Тег |
|---|---|
| `app.routers.jarvis_unified_night_router` | unified-night |
| `app.routers.jarvis_operator_task_router` | operator |
| `app.routers.jarvis_n8n_specialist_router` | n8n-specialist |
| `app.routers.jarvis_n8n_super_agent_router` | n8n-super-agent |
| `app.routers.jarvis_brain_router` | jarvis-brain |
| `app.routers.jarvis_brain_executor_router` | jarvis-brain-executor |

### Опциональные startup (try/except)
| Роутер | Статус |
|---|---|
| `app.routers.supervisor_automation_router` | ✅ загружен |
| `app.routers.supervisor_pipeline_router` | ✅ загружен |
| `app.routers.n8n_canvas_builder_router` | ✅ загружен |
| `app.routers.n8n_workflow_materializer_router` | ✅ загружен |
| `app.routers.jarvis_n8n_bridge_router` | ✅ загружен |
| `app.routers.jarvis_v5_async_bridge_router` | ✅ загружен |
| `app.routers.jarvis_internet_tools_router` | ✅ загружен |
| `app.routers.jarvis_ai_engineer_router` | ✅ загружен |
| `app.routers.jarvis_telegram_file_tools_router` | ✅ загружен |

---

## SKIPPED_CONFIG — 2 роутера (нет DATABASE_PATH)

| Роутер | Причина |
|---|---|
| `app.api.routes.router` | `DATABASE_PATH` не задан в env |
| `app.api.routes.missions.router` | `DATABASE_PATH` не задан в env |

> Эти роутеры подключатся автоматически когда `DATABASE_PATH` появится в `.env`.

---

## SKIPPED_DEPS — 2 роутера (отсутствует пакет)

| Роутер | Отсутствующий пакет | Функционал |
|---|---|---|
| `app.routers.jarvis_v5_content_factory` | `google-auth` / `google-api-python-client` | Google Drive / YouTube upload |
| `app.api.spreadsheets` | `openpyxl` | Excel-файлы |

> Установить: `pip install google-auth google-api-python-client openpyxl`  
> После установки оба роутера подключатся без изменений кода (try/except уже стоит).

---

## FAILED — 0

Настоящих ошибок при старте нет.

---

## Итог

| Категория | Кол-во |
|---|---|
| LOADED | 43 |
| SKIPPED_CONFIG | 2 |
| SKIPPED_DEPS | 2 |
| FAILED | 0 |
| **Live endpoints** | **321** |
