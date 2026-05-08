# MEMORY_PLAN.md — Анализ и план для memory модулей

Дата: 2026-04-30  
**Вывод: модули с одинаковыми именами — НЕ дубли (роутер vs модель).  
Реальный вопрос: нужна ли semantic_memory.py рядом с semantic_memory_service.py?**

---

## Полная таблица (20 модулей)

| Файл | Строк | Размер | Дата | Классы |
|---|---|---|---|---|
| `app/memory_state.py` | 106 | 3 КБ | 2026-03-28 | TraceRecord, ChatState, MemoryState |
| `app/api/memory.py` | 21 | 1 КБ | 2026-04-07 | — |
| `app/api/memory_layer.py` | 31 | 1 КБ | 2026-04-12 | — |
| `app/api/mission_memory_bridge.py` | 18 | 1 КБ | 2026-04-13 | — |
| `app/api/mission_run_memory.py` | 19 | 1 КБ | 2026-04-13 | — |
| `app/autonomy/memory_manager.py` | 182 | 7 КБ | 2026-04-08 | — |
| `app/control_plane/memory.py` | 61 | 3 КБ | 2026-04-18 | SmartMemoryManager |
| `app/control_plane/memory_mesh.py` | 92 | 3 КБ | 2026-04-18 | MemoryMeshVault |
| `app/control_plane/skill_memory.py` | 142 | 5 КБ | 2026-04-18 | SkillEvidence, SkillProfile, SkillMemoryStore |
| `app/models/auto_memory.py` | 40 | 1 КБ | 2026-04-13 | — |
| `app/models/memory_layer.py` | 44 | 1 КБ | 2026-04-12 | — |
| `app/models/mission_run_memory.py` | 21 | 1 КБ | 2026-04-13 | — |
| `app/services/conversation_memory.py` | 114 | 3 КБ | 2026-04-07 | ConversationMemory |
| `app/services/jarvis_memory_learning_layer.py` | 397 | 16 КБ | 2026-04-23 | MemoryEvent, ModuleInsight, LearningSnapshot, JarvisMemoryLearningLayer |
| `app/services/memory/auto_memory_pipeline.py` | 43 | 2 КБ | 2026-04-13 | — |
| `app/services/memory/memory_analysis_service.py` | 103 | 4 КБ | 2026-04-13 | — |
| `app/services/memory/mission_run_memory_hook.py` | 72 | 3 КБ | 2026-04-13 | — |
| `app/services/memory/semantic_memory_service.py` | 166 | 6 КБ | 2026-04-12 | — |
| `app/services/mission_memory.py` | 228 | 7 КБ | 2026-04-07 | — |
| `app/services/semantic_memory.py` | 199 | 6 КБ | 2026-04-14 | — |

---

## Карта зависимостей (кто кого импортирует)

### Внутренние зависимости между memory модулями

```
app/api/memory.py
  └─ imports: app.services.mission_memory (get_memory, memory_stats)

app/api/memory_layer.py
  ├─ imports: app.models.memory_layer
  └─ imports: app.services.memory.semantic_memory_service (SemanticMemoryService)

app/api/mission_memory_bridge.py
  ├─ imports: app.models.auto_memory
  └─ imports: app.services.memory.auto_memory_pipeline (AutoMemoryPipeline)

app/api/mission_run_memory.py
  ├─ imports: app.models.mission_run_memory
  └─ imports: app.services.memory.mission_run_memory_hook (MissionRunMemoryHook)

app/services/memory/auto_memory_pipeline.py
  ├─ imports: app.models.auto_memory
  ├─ imports: app.models.obsidian_bridge (AutoMissionMemoryRequest)
  └─ imports: app.services.memory.memory_analysis_service (MemoryAnalysisService)

app/services/memory/mission_run_memory_hook.py
  ├─ imports: app.models.auto_memory
  ├─ imports: app.models.mission_run_memory
  └─ imports: app.services.memory.auto_memory_pipeline (AutoMemoryPipeline)

app/services/memory/semantic_memory_service.py
  └─ imports: app.models.memory_layer
```

### Внешние потребители (live production код)

```
app/api/missions.py
  └─ imports: app.services.mission_memory (get_memory, memory_stats)

app/routers/agent_control_plane.py
  └─ imports: app.services.semantic_memory  ← НЕ semantic_memory_service!

app/services/context_manager.py
  └─ imports: app.services.mission_memory (build_resume_context, compress_memory, get_memory)

app/services/continuity.py
  └─ imports: app.services.mission_memory (append_memory)

app/services/conversation_brain.py
  └─ imports: app.services.conversation_memory (ConversationMemory)

app/services/dashboard_service.py
  └─ imports: app.services.mission_memory (memory_stats)

app/services/supervisor_core.py
  └─ imports: app.services.mission_memory (append_memory, get_memory, memory_stats)

app/services/worker_loop.py
  └─ imports: app.services.mission_memory (append_memory)
```

---

## Разбор "одинаковых имён"

### memory_layer — НЕ дубль
| Файл | Тип | Содержимое |
|---|---|---|
| `app/models/memory_layer.py` | Pydantic модели | Схемы данных (MemoryLayerWrite и т.д.) |
| `app/api/memory_layer.py` | FastAPI роутер | `GET /api/memory-layer/list`, `POST /api/memory-layer/write`, `POST /api/memory-layer/mission-summary` |

Роутер импортирует модели. Это нормальная архитектура — model/route разделение.

### mission_run_memory — НЕ дубль
| Файл | Тип | Содержимое |
|---|---|---|
| `app/models/mission_run_memory.py` | Pydantic модели | MissionRunMemoryRequest, MissionRunMemoryResponse |
| `app/api/mission_run_memory.py` | FastAPI роутер | `POST /api/mission-run-memory/write` |

Аналогично — роутер импортирует модели.

---

## ✅ ЗАКРЫТО (Phase 3.8): semantic_memory.py vs semantic_memory_service.py

**Вывод: INTENTIONAL SEPARATION — не трогать. Оба файла нужны.**

| Критерий | `semantic_memory.py` | `semantic_memory_service.py` |
|---|---|---|
| Backend | SQLite DB (`semantic_memory.db`) | Filesystem (Markdown .md файлы) |
| Interface | Module-level functions | Класс `SemanticMemoryService` |
| Input | Примитивы (str, list) | Pydantic schemas |
| Search | ✅ Full-text SQL LIKE | ❌ нет |
| Назначение | Agent mesh: хранение событий runtime | Memory layer API: запись Obsidian-style заметок |
| Потребители | `agent_control_plane.py` → `ingest_packaged_mission_result` | `memory_layer.py` router + `obsidian_bridge_service.py` |

Имя похожее — задачи ортогональные. Объединение сломало бы оба потребителя.

---

## Рекомендации по группам

### Группа A — Legacy Core (март–апрель 2026)
| Файл | Решение |
|---|---|
| `app/memory_state.py` | ✅ ОСТАВИТЬ — TraceRecord/ChatState/MemoryState нигде не импортированы явно, но могут использоваться через общий state. Требует grep |
| `app/api/memory.py` | ✅ ОСТАВИТЬ — активный роутер, но 2 endpoints неtagged (`GET /memory/stats`, `GET /memory/{id}`). Это legacy URL без `/api/` prefix — нужны ли они? |
| `app/services/conversation_memory.py` | ✅ ОСТАВИТЬ — используется `conversation_brain.py` |
| `app/services/mission_memory.py` | ✅ ОСТАВИТЬ — **критически используется 6 файлами** |

### Группа B — Layer/Model (Pydantic schemas)
Все файлы нужны как схемы данных. Удалять нельзя — сломает роутеры.

### Группа C — Специализированные сервисы
| Файл | Решение |
|---|---|
| `app/services/semantic_memory.py` | ❓ Сравнить с `semantic_memory_service.py` — возможное объединение |
| `app/services/memory/semantic_memory_service.py` | ✅ Более новый (в `memory/` субпакете) |
| `app/services/jarvis_memory_learning_layer.py` | ✅ ОСТАВИТЬ — 397 строк, самый новый (2026-04-23), 4 класса |
| `app/services/memory/auto_memory_pipeline.py` | ✅ нужен — используется мостом |
| `app/services/memory/memory_analysis_service.py` | ✅ нужен — используется pipeline |
| `app/services/memory/mission_run_memory_hook.py` | ✅ нужен — используется роутером |

### Группа D — Autonomy & Control Plane
Все три файла в `app/control_plane/` — отдельные классы без дублирования. Оставить.  
`app/autonomy/memory_manager.py` — 182 строки, нет явных импортов снаружи. Нужно проверить.

---

## Следующий шаг

**Одно действие которое имеет смысл сделать немедленно:**

```bash
# Проверить имеет ли semantic_memory.py и semantic_memory_service.py одинаковый интерфейс:
grep -n "^def \|^class \|^async def " app/services/semantic_memory.py
grep -n "^def \|^class \|^async def " app/services/memory/semantic_memory_service.py
```

Если интерфейс совпадает — объединить в один файл и обновить импорт в `agent_control_plane.py`.  
Если разный — оба нужны.

---

> **Итого: из 20 модулей ни один не является явным мусором.  
> Единственный кандидат на объединение — `semantic_memory.py` vs `semantic_memory_service.py`.  
> Все остальные либо критически используются, либо являются необходимыми схемами данных.**
