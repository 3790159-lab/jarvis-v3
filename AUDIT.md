# AUDIT.md — Рекогносцировка перед чисткой

Дата: 2026-04-29  
Проект: supervisor_v1_5_smart_telegram  
Корень: `app/main.py` → FastAPI "Jarvis V3 Supervisor" v3.0.0

---

## 1. Количество .bak файлов

**Итого: 1 665**

Основные категории:
- `.env.bak*` — 14 файлов (поофазные снапшоты `.env.bak_phase*`, `.env.bak_best_*`, `.env.bak_ollama_*` и т.д.)
- Python `.py.bak_*` — 1 651 файл, разбросаны по `app/`, `app/routers/`, `app/api/`, `app/services/` и backup-директориям

Backup-директории верхнего уровня (содержат полные копии `app/`):
```
app_backup_before_supervisor_final/
app_backup_before_upgrade/
backup/
backup_fix_future_imports/
backup_fix_router_conflict_20260407_003324/
backup_full_repair_20260406_183741/
backup_goals_api_fix_20260406_203108/
backup_header_repair_20260406_183001/
backup_import_fix/
backup_respond_endpoint_20260406_195223/
backup_stage1_stable_core_20260406_232958/
backup_stage3_conversational_brain_20260407_014821/
backup_utf8_fix_20260406_175349/
hotfix_backups/
jarvis_stage3/
```

---

## 2. Дублирующие версии ключевых модулей

Все в `app/` рядом с активным файлом лежат именованные снапшоты вида `module.before_<patch>.py`.

| Модуль | Активный файл | Кол-во снапшотов | Снапшоты |
|---|---|---|---|
| `agent_registry` | `agent_registry.py` | 1 | `.before_hotfix` |
| `llm` | `llm.py` | 4 | `.before_dual_patch`, `.before_hotfix`, `.before_max_optimizer`, `.before_tool_layer` |
| `local_tools` | `local_tools.py` | 1 | `.before_hotfix` |
| `orchestrator` | `orchestrator.py` | 4 | `.before_dual_patch`, `.before_hotfix`, `.before_max_optimizer`, `.before_tool_layer` |
| `telegram_bot` | `telegram_bot.py` | 8 | `.before_dual_patch`, `.before_google_fix`, `.before_google_helpers_fix`, `.before_google_manual_fix`, `.before_hotfix`, `.before_max_optimizer`, `.before_nameerror_fix`, `.before_tool_layer` + отдельный `telegram_bot_old.py` |

**Итого:** 5 активных модулей + 19 снапшотов-двойников прямо в `app/`.

---

## 3. Файлы, которые я считаю entrypoint'ами

### Основной (производственный)
| Файл | Тип | Обоснование |
|---|---|---|
| `app/main.py` | FastAPI | Единственный файл, создающий `app = FastAPI(...)`. Это то, что должен запускать uvicorn (`uvicorn app.main:app`). |

### Альтернативные / экспериментальные
| Файл | Тип | Обоснование |
|---|---|---|
| `app/main_local_n8n_bridge.py` | FastAPI | Параллельная точка входа с n8n-бриджем |
| `app/main_local_n8n_bridge_v2.py` | FastAPI | Обновлённая версия n8n-бриджа |
| `app/main_stage_b_v2.py` | FastAPI | Stage B имплементация |

### Архивные (не использовать)
`backup*/main.py` — 13+ временных снапшотов основного `main.py` в backup-директориях.  
`jarvis_stage3/main.py` — отдельная стадия разработки.

---

## 4. Сломанные / проблемные импорты в main.py

### КРИТИЧНО — SyntaxError (или SyntaxWarning ≥ Python 3.10)

**Строка 3: `from __future__ import annotations` стоит НЕ первой**

```python
# main.py
1: from app.routers.multi_ai_orchestrator_v1 import router as multi_ai_orchestrator_v1_router
2: from app.routers.jarvis_brain_v2 import router as jarvis_brain_v2_router
3: from __future__ import annotations   # ← ЗДЕСЬ
```

По спецификации Python, `from __future__ import ...` обязан быть первым выражением в файле (после docstring и комментариев). Размещение после обычных импортов вызывает `SyntaxError: from __future__ imports must occur at the beginning of the file`.  
Косвенное подтверждение — в репозитории есть директория `backup_fix_future_imports/`, созданная именно для фиксации этой проблемы.

---

### СРЕДНЕ — Дублирующий импорт

**Строки 7 и 18: `time_brain` импортируется дважды**

```python
 7: from app.routers import time_brain   # первый раз
...
18: from app.routers import time_brain   # второй раз (лишний)
```

Технически не ломает запуск, но является признаком того, что файл редактировался "на живую" без контроля состояния.

---

### НИЗКО — Незащищённый bare-импорт посреди потока try-except блоков

**Строка 221: хард-импорт без try-except**

```python
220: # (конец try-except блока выше)
221: from app.routers.n8n_action_router_materializer_router import router as n8n_action_router_materializer_router
222: app.include_router(n8n_action_router_materializer_router)
```

Весь остальной код в этом разделе обёрнут в `try/except`. Этот импорт — нет. Файл физически существует, поэтому сейчас не падает, но при удалении или переименовании модуля рухнет весь старт приложения (в отличие от соседних роутеров, которые просто пропустятся с `[WARN]`).

Аналогичная ситуация (хард-импорт без guard) на строках: **269, 274, 279, 284, 289, 294** — это `jarvis_unified_night_router`, `jarvis_operator_task_router`, `jarvis_n8n_specialist_router`, `jarvis_n8n_super_agent_router`, `jarvis_brain_router`, `jarvis_brain_executor_router`. Все файлы существуют, но логика защиты непоследовательна.

---

## Итоговая картина

| Категория | Количество |
|---|---|
| .bak файлов | **1 665** |
| Backup-директорий верхнего уровня | **15** |
| Дублирующих снапшотов ключевых модулей (в `app/`) | **19** |
| Активных entrypoint'ов | **4** (1 основной + 3 альтернативных) |
| Критичных проблем импорта в main.py | **1** (`__future__` не на месте) |
| Некритичных проблем импорта | **2** (двойной `time_brain` + непоследовательная защита хард-импортов) |
