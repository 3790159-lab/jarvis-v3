# PHASE3_ANALYSIS.md — Архитектурный анализ перед Фазой 3

Дата: 2026-04-29  
Создан для принятия решений об удалении/консолидации в Фазе 3

---

## 1. Четыре main.py — сравнение

### `app/main.py` — ОСНОВНОЙ (uvicorn app.main:app)

Зарегистрированные роутеры (по порядку в файле):

**Инлайн при старте:**
- `multi_ai_orchestrator_v1_router` — Multi-AI Orchestrator V1
- `jarvis_brain_v2_router` — Brain V2 identity/router override
- `jarvis_v5_content_factory_router`
- `jarvis_live_operator_router`
- `claude_ecosystem.router`
- `time_brain.router` ← дублируется дважды (строки 7 и 18 — известная проблема из AUDIT.md)

**Два hard-импорта без try/except:**
- `n8n_action_router_materializer_router` (строка 222) ⚠️
- `unified_night_router`, `operator_task_router`, `n8n_specialist_router`, `n8n_super_agent_router`, `jarvis_brain_router`, `jarvis_brain_executor_router` (строки 270–295) ⚠️

**Защищённые (в try/except) при startup:**
- `agent_mesh_router`, `agent_mesh_plus_router`, `agent_mesh_stability_router`, `agent_mesh_real_exec_router`, `agent_mesh_improvement_router`
- `n8n_bridge_router`, `supervisor_automation_router`, `supervisor_pipeline_router`
- `n8n_canvas_builder_router`, `n8n_workflow_materializer_router`
- `n8n_action_router_materializer_v2_router`, `jarvis_n8n_bridge_router`
- `operator_dashboard.router`
- `jarvis_v5_async_bridge_router`, `jarvis_internet_tools_router`, `jarvis_ai_engineer_router`, `jarvis_telegram_file_tools_router`

**Прямые endpoints в main.py:**
- `GET /health`
- `GET /api/ai/health`
- `GET /`

---

### `app/main_local_n8n_bridge.py` — 17 строк

Минимальный файл. Единственный роутер:
- `jarvis_n8n_bridge_router` (в try/except)

> Запускается через: `scripts/jarvis_restart_backend_local_n8n_bridge.ps1`  
> **Вывод:** вероятно устарел — v2 делает то же самое и имеет больше возможностей.

---

### `app/main_local_n8n_bridge_v2.py` — 44 строки

Роутер + дополнительные endpoints:
- `jarvis_n8n_bridge_router`
- `GET /api/jarvis/n8n/ping`
- `GET /api/jarvis/n8n/_routes` (интроспекция маршрутов)

> Запускается через: `scripts/jarvis_restart_backend_local_n8n_bridge_v2.ps1`  
> **Вывод:** актуальная версия bridge. v1 можно удалить.

---

### `app/main_stage_b_v2.py` — 21 строка

Единственный роутер:
- `n8n_action_router_materializer_v2_router` (в try/except)

> Запускается через: `scripts/jarvis_restart_backend_stage_b_v2.ps1`  
> **Вывод:** специализированный entrypoint только для v2 materializer.

---

### Рекомендация по main.py

| Файл | Решение | Обоснование |
|---|---|---|
| `app/main.py` | ✅ ОСТАВИТЬ | Единственный production entrypoint |
| `app/main_local_n8n_bridge.py` | ⭐ КАНДИДАТ НА УДАЛЕНИЕ | v2 делает то же самое + больше |
| `app/main_local_n8n_bridge_v2.py` | ✅ ОСТАВИТЬ | Актуальный bridge entrypoint |
| `app/main_stage_b_v2.py` | ❓ ОБСУДИТЬ | Нужен ли отдельный entrypoint для materializer-v2? |

---

## 2. Пять agent_mesh роутеров (app/api/)

Все используют один prefix: `/api/agent-mesh`

| Файл | Размер | Дата | Endpoints |
|---|---|---|---|
| `agent_mesh_router.py` | 14 КБ | 2026-04-18 | `/health`, `/learning/*` (11 endpoints), `/adaptive-policy`, `/strategy/active` |
| `agent_mesh_plus_router.py` | 7 КБ | 2026-04-18 | `/lifecycle/*`, `/knowledge/*` (8 endpoints), `/service-traces`, `/autonomy/*` |
| `agent_mesh_stability_router.py` | 3 КБ | 2026-04-18 | `/service-guard/health`, `/self-healing/*`, `/service-resolution/example` |
| `agent_mesh_real_exec_router.py` | 2 КБ | 2026-04-18 | `/n8n/*` (4 endpoints), `/traces/rebuild`, `/autonomy/enable|disable`, `/real-exec/health` |
| `agent_mesh_improvement_router.py` | 3 КБ | 2026-04-19 | `/improvement/*` (7 endpoints), `/claude-probe/health` |

**Ключевые наблюдения:**
1. Все 5 зарегистрированы в `app/main.py` в startup (защищены try/except)
2. Один prefix `/api/agent-mesh` — endpoints не конфликтуют (разные пути)
3. `improvement_router` (19 апреля) — единственный с более поздней датой
4. Разбивка логичная: `router` = обучение, `plus` = знания/жизненный цикл, `stability` = самоисцеление, `real_exec` = реальное выполнение, `improvement` = улучшения

**Рекомендация:** все 5 активны и нужны. Вопрос консолидации — опциональный рефактор, не удаление.

---

## 3. Восемь n8n роутеров (app/routers/)

| Файл | Размер | Дата | Prefix | Endpoints |
|---|---|---|---|---|
| `n8n_bridge_router.py` | 1 КБ | 2026-04-21 | `/api/n8n` | `GET /health`, `POST /dispatch` |
| `n8n_canvas_builder_router.py` | 1 КБ | 2026-04-21 | `/api/supervisor/canvas` | `GET /health`, `POST /build` |
| `n8n_workflow_materializer_router.py` | 1 КБ | 2026-04-21 | `/api/supervisor/materializer` | `GET /health`, `POST /run` |
| `n8n_action_router_materializer_router.py` | 2 КБ | 2026-04-21 | `/api/n8n/materializer` | `GET /health|debug-state`, `POST /dry-run-payload|execute|publish-workflow|publish-and-probe` |
| `n8n_action_router_materializer_v2_router.py` | 3 КБ | 2026-04-21 | `/api/n8n/materializer-v2` | `GET /health|debug-state`, `POST /dry-run-payload|create-only|publish-workflow|publish-and-probe|execute` |
| `jarvis_n8n_bridge_router.py` | 3 КБ | 2026-04-22 | `/api/jarvis/n8n` | `GET /health|config|public-api-check|workflows`, `POST /workflows/create-webhook|activate|probe|smoke` |
| `jarvis_n8n_specialist_router.py` | 1 КБ | 2026-04-24 | `/api/n8n-specialist` | `GET /health|latest`, `POST /run` |
| `jarvis_n8n_super_agent_router.py` | 1 КБ | 2026-04-24 | `/api/n8n-super-agent` | `GET /health|latest`, `POST /run` |

**Ключевые наблюдения:**

1. **materializer v1 vs v2** — оба на разных prefix (`/materializer` vs `/materializer-v2`), v2 добавляет `POST /create-only`. `n8n_action_router_materializer_router.py` — незащищённый bare-импорт в main.py (⚠️ строка 222). Нельзя удалить без правки main.py.

2. **n8n_bridge vs jarvis_n8n_bridge** — разные prefix и цели:
   - `n8n_bridge_router`: простой dispatch (`POST /api/n8n/dispatch`)
   - `jarvis_n8n_bridge_router`: полный API работы с workflow (create, activate, probe)

3. **specialist vs super_agent** — идентичная структура (3 endpoints каждый), одинаковая дата. Возможные дубли с разными именами агентов.

4. **canvas_builder и workflow_materializer** — минимальные (2 endpoints каждый), похожи на обёртки.

**Рекомендации:**
| Файл | Решение |
|---|---|
| `n8n_bridge_router.py` | ❓ Нужен ли если есть `jarvis_n8n_bridge_router`? |
| `n8n_canvas_builder_router.py` | ❓ Обсудить — может быть часть materializer pipeline |
| `n8n_workflow_materializer_router.py` | ❓ Обсудить — возможно заменён v2 |
| `n8n_action_router_materializer_router.py` | ⚠️ НЕЛЬЗЯ удалить без правки main.py строка 222 |
| `n8n_action_router_materializer_v2_router.py` | ✅ Актуальный, оставить |
| `jarvis_n8n_bridge_router.py` | ✅ Полный API, оставить |
| `jarvis_n8n_specialist_router.py` | ❓ Сравнить с super_agent — возможный дубль |
| `jarvis_n8n_super_agent_router.py` | ❓ Сравнить с specialist — возможный дубль |

---

## 4. Двадцать memory модулей — группировка по функционалу

### Группа A — Legacy Core (старейшие, не рефакторились)
| Файл | Размер | Дата | Назначение |
|---|---|---|---|
| `app/memory_state.py` | 3 КБ | 2026-03-28 | Глобальное состояние памяти (dict/json) |
| `app/api/memory.py` | 1 КБ | 2026-04-07 | Базовый API endpoint памяти |
| `app/services/conversation_memory.py` | 3 КБ | 2026-04-07 | История диалогов |
| `app/services/mission_memory.py` | 7 КБ | 2026-04-07 | Память миссий (старая версия) |

### Группа B — Data Models / Schemas
| Файл | Размер | Дата | Назначение |
|---|---|---|---|
| `app/api/memory_layer.py` | 1 КБ | 2026-04-12 | Pydantic-схемы слоя памяти |
| `app/models/memory_layer.py` | 1 КБ | 2026-04-12 | ⚠️ Дубль имени — models vs api |
| `app/api/mission_run_memory.py` | 1 КБ | 2026-04-13 | API для run-памяти миссий |
| `app/api/mission_memory_bridge.py` | 1 КБ | 2026-04-13 | Bridge между mission и memory API |
| `app/models/auto_memory.py` | 1 КБ | 2026-04-13 | Модель автоматической памяти |
| `app/models/mission_run_memory.py` | 1 КБ | 2026-04-13 | ⚠️ Дубль имени — models vs api |

### Группа C — Специализированные сервисы
| Файл | Размер | Дата | Назначение |
|---|---|---|---|
| `app/services/semantic_memory.py` | 6 КБ | 2026-04-14 | Семантический поиск/хранение |
| `app/services/jarvis_memory_learning_layer.py` | 16 КБ | 2026-04-23 | ⭐ Самый новый и крупный — обучение из памяти |
| `app/services/memory/auto_memory_pipeline.py` | 2 КБ | 2026-04-13 | Pipeline автосохранения |
| `app/services/memory/memory_analysis_service.py` | 4 КБ | 2026-04-13 | Анализ паттернов в памяти |
| `app/services/memory/mission_run_memory_hook.py` | 3 КБ | 2026-04-13 | Hook для сохранения run-результатов |
| `app/services/memory/semantic_memory_service.py` | 6 КБ | 2026-04-12 | ⚠️ Возможный дубль с `semantic_memory.py` |

### Группа D — Autonomy & Control Plane
| Файл | Размер | Дата | Назначение |
|---|---|---|---|
| `app/autonomy/memory_manager.py` | 7 КБ | 2026-04-08 | Менеджер памяти для автономных агентов |
| `app/control_plane/memory.py` | 3 КБ | 2026-04-18 | Control plane — базовая память |
| `app/control_plane/memory_mesh.py` | 3 КБ | 2026-04-18 | Mesh-сеть памяти между агентами |
| `app/control_plane/skill_memory.py` | 5 КБ | 2026-04-18 | Память навыков агентов |

### Ключевые вопросы для Фазы 3

1. **Дубли имён** (`memory_layer`, `mission_run_memory` в `app/api/` и `app/models/`) — это разные файлы с разным содержимым или случайные копии?
2. **`semantic_memory.py` vs `semantic_memory_service.py`** — что из них используется в import'ах?
3. **Группа A** (`mission_memory.py`, `conversation_memory.py`) — есть ли живые импорты или заменены группой C?
4. **`jarvis_memory_learning_layer.py`** (16 КБ, самый новый) — это финальная архитектура памяти или очередной эксперимент?
