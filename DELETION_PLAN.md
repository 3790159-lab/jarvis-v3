# DELETION_PLAN.md — Порядок чистки репозитория

Дата: 2026-04-29  
Safety net: git `d63d5e0` — "Initial state before consolidation"  
Восстановление: `git checkout .` — вернёт любой удалённый файл

---

## GIT SAFETY NET — ГОТОВ

```
d63d5e0 Initial state before consolidation
```

> Перед любым удалением убедись что коммит существует: `git log --oneline`  
> Восстановить всё: `git checkout .`  
> Восстановить файл: `git checkout HEAD -- path/to/file.py`

---

## ФАЗА 1 — Безопасное массовое удаление (паттерны)

Файлы в этой фазе — механические бэкапы без исключений.  
**Обсуждать нечего. Можно выполнять последовательно.**

---

### 1.1 — Все `.bak*` файлы (кроме `.env.bak*`)

| Метрика | Значение |
|---|---|
| Количество файлов | **1 872** |
| Суммарный размер | **~193 МБ** |
| Примеры | `app/api/cloud_control.py.bak_20260417_021317`, `app/main.py.20260422_184202.bak` |

```powershell
# Предпросмотр (что будет удалено):
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '\.bak' -and $_.Name -notmatch '^\.env\.bak' } | Measure-Object | Select Count

# Удаление:
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '\.bak' -and $_.Name -notmatch '^\.env\.bak' } | Remove-Item -Force
```

---

### 1.2 — Все `.before_*.py` файлы

| Метрика | Значение |
|---|---|
| Количество файлов | **32** (18 прямо в `app/`, 14 в backup-директориях) |
| Суммарный размер | **~241 КБ** |
| Примеры | `app/llm.before_hotfix.py`, `app/telegram_bot.before_google_manual_fix.py` |

> ⚠️ 18 из 32 файлов лежат прямо в `app/` — они же перечислены в Фазе 3 как архитектурные снапшоты.  
> Если хочешь сначала обсудить их — удали эту фазу только для `backup_*` директорий и перенеси app/ в Фазу 3.

```powershell
# Предпросмотр:
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '\.before_.*\.py$' } | Select Name, DirectoryName

# Удаление:
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '\.before_.*\.py$' } | Remove-Item -Force
```

---

### 1.3 — Все `*_old.py` файлы

| Метрика | Значение |
|---|---|
| Количество файлов | **2** |
| Суммарный размер | **~9 КБ** |
| Файлы | `app/telegram_bot_old.py` (5 KB, 2026-03-25) + 1 в backup-директории |

```powershell
# Предпросмотр:
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '_old\.py$' } | Select FullName

# Удаление:
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match '_old\.py$' } | Remove-Item -Force
```

---

### 1.4 — Все `.env.bak*` файлы

| Метрика | Значение |
|---|---|
| Количество файлов | **14** |
| Суммарный размер | **~25 КБ** |
| Примеры | `.env.bak`, `.env.bak_phase2_20260407_162046`, `.env.bak_best_20260407_160709` |

> Все 14 файлов — снапшоты `.env` из разных фаз настройки (2026-04-07 — 2026-04-17).  
> Активный `.env` НЕ затрагивается (он в `.gitignore` и не попадёт под паттерн).

```powershell
# Предпросмотр:
Get-ChildItem -File | Where-Object { $_.Name -match '^\.env\.bak' } | Select Name

# Удаление:
Get-ChildItem -File | Where-Object { $_.Name -match '^\.env\.bak' } | Remove-Item -Force
```

---

### Итог Фазы 1

| Группа | Файлов | Размер |
|---|---|---|
| `.bak*` (не .env) | 1 872 | ~193 МБ |
| `.before_*.py` | 32 | ~241 КБ |
| `*_old.py` | 2 | ~9 КБ |
| `.env.bak*` | 14 | ~25 КБ |
| **ИТОГО** | **1 920** | **~193.3 МБ** |

---

## ФАЗА 2 — Backup-директории верхнего уровня

15 директорий с полными/частичными копиями `app/`.  
Отсортированы от старейших к новейшим.

| # | Директория | Размер | Первый файл | Последний файл | Статус |
|---|---|---|---|---|---|
| 1 | `backup/` | 72 КБ | 2026-03-25 | 2026-03-31 | ⭐ СТАРЕЙШИЙ |
| 2 | `backup_utf8_fix_20260406_175349/` | 7 КБ | 2026-03-31 | 2026-04-06 | ⭐ |
| 3 | `backup_fix_future_imports/` | 7 КБ | 2026-04-06 | 2026-04-06 | — |
| 4 | `backup_full_repair_20260406_183741/` | 7 КБ | 2026-04-06 | 2026-04-06 | — |
| 5 | `backup_header_repair_20260406_183001/` | 7 КБ | 2026-04-06 | 2026-04-06 | — |
| 6 | `backup_import_fix/` | 7 КБ | 2026-04-06 | 2026-04-06 | — |
| 7 | `backup_respond_endpoint_20260406_195223/` | 0 КБ | 2026-04-06 | 2026-04-06 | ⚠️ ПУСТАЯ |
| 8 | `backup_goals_api_fix_20260406_203108/` | 13 КБ | 2026-04-06 | 2026-04-06 | — |
| 9 | `backup_stage1_stable_core_20260406_232958/` | 17 КБ | 2026-04-06 | 2026-04-06 | — |
| 10 | `backup_fix_router_conflict_20260407_003324/` | 5 КБ | 2026-04-06 | 2026-04-07 | — |
| 11 | `backup_stage3_conversational_brain_20260407_014821/` | 28 КБ | 2026-04-06 | 2026-04-07 | — |
| 12 | `app_backup_before_upgrade/` | 85 КБ | 2026-03-25 | 2026-04-15 | ⭐ |
| 13 | `app_backup_before_supervisor_final/` | 230 КБ | 2026-03-25 | 2026-04-15 | ⭐ НАИБОЛЬШИЙ |
| 14 | `hotfix_backups/` | 106 КБ | 2026-03-25 | 2026-04-15 | — |
| 15 | `jarvis_stage3/` | 35 КБ | 2026-03-31 | 2026-04-15 | — |

**Суммарно: ~627 КБ**

> Предложение: директории 1–11 (все апрель 6–7) можно удалить одним блоком.  
> Директории 12–15 содержат более ранние файлы (часть с датой 2026-03-25) — стоит проверить нет ли там единственных копий.

```powershell
# Удаление директорий 1–11 (patch-day от 6 апреля):
@(
    'backup',
    'backup_utf8_fix_20260406_175349',
    'backup_fix_future_imports',
    'backup_full_repair_20260406_183741',
    'backup_header_repair_20260406_183001',
    'backup_import_fix',
    'backup_respond_endpoint_20260406_195223',
    'backup_goals_api_fix_20260406_203108',
    'backup_stage1_stable_core_20260406_232958',
    'backup_fix_router_conflict_20260407_003324',
    'backup_stage3_conversational_brain_20260407_014821'
) | ForEach-Object { Remove-Item -Recurse -Force $_ }

# Директории 12–15 — отдельно, после проверки:
# Remove-Item -Recurse -Force app_backup_before_upgrade, app_backup_before_supervisor_final, hotfix_backups, jarvis_stage3
```

---

## ФАЗА 3 — Архитектурные решения (нужно обсуждение)

---

### 3.1 — Четыре main.py: кто реально запускается

**Вывод из анализа `.ps1` и `.bat` скриптов:**

| Файл | Статус | Доказательство |
|---|---|---|
| `app/main.py` | ✅ **ОСНОВНОЙ** | `backend_guardian.ps1`, `jarvis_backend_restart_hardened.ps1`, `jarvis_project_pipeline_v2.ps1` — все запускают `uvicorn app.main:app` |
| `app/main_local_n8n_bridge.py` | 🔶 АКТИВНЫЙ АЛЬТЕРНАТИВНЫЙ | `scripts/jarvis_restart_backend_local_n8n_bridge.ps1` → `uvicorn app.main_local_n8n_bridge:app` |
| `app/main_local_n8n_bridge_v2.py` | 🔶 АКТИВНЫЙ АЛЬТЕРНАТИВНЫЙ | `scripts/jarvis_restart_backend_local_n8n_bridge_v2.ps1` → `uvicorn app.main_local_n8n_bridge_v2:app` |
| `app/main_stage_b_v2.py` | 🔶 АКТИВНЫЙ АЛЬТЕРНАТИВНЫЙ | `scripts/jarvis_restart_backend_stage_b_v2.ps1` → `uvicorn app.main_stage_b_v2:app` |

**Вопрос для обсуждения:** `main_local_n8n_bridge.py` и `main_local_n8n_bridge_v2.py` — это v1 и v2 одной идеи. Если v2 стабильна — v1 можно удалить. Нужно подтверждение.

---

### 3.2 — 19 снапшотов модулей в `app/`

Все файлы лежат прямо рядом с активным кодом в `app/`.

| Файл | Размер | Дата | Модуль |
|---|---|---|---|
| `agent_registry.before_hotfix.py` | 1 КБ | 2026-03-25 | agent_registry |
| `llm.before_hotfix.py` | 2 КБ | 2026-03-25 | llm |
| `llm.before_dual_patch.py` | 2 КБ | 2026-03-25 | llm |
| `local_tools.before_hotfix.py` | 5 КБ | 2026-03-25 | local_tools |
| `orchestrator.before_hotfix.py` | 3 КБ | 2026-03-25 | orchestrator |
| `orchestrator.before_dual_patch.py` | 3 КБ | 2026-03-25 | orchestrator |
| `telegram_bot.before_dual_patch.py` | 5 КБ | 2026-03-26 | telegram_bot |
| `telegram_bot.before_hotfix.py` | 5 КБ | 2026-03-26 | telegram_bot |
| `llm.before_max_optimizer.py` | 11 КБ | 2026-03-26 | llm |
| `orchestrator.before_max_optimizer.py` | 13 КБ | 2026-03-26 | orchestrator |
| `telegram_bot.before_max_optimizer.py` | 6 КБ | 2026-03-26 | telegram_bot |
| `llm.before_tool_layer.py` | 22 КБ | 2026-03-27 | llm |
| `orchestrator.before_tool_layer.py` | 18 КБ | 2026-03-27 | orchestrator |
| `telegram_bot.before_tool_layer.py` | 7 КБ | 2026-03-27 | telegram_bot |
| `telegram_bot.before_google_fix.py` | 8 КБ | 2026-04-11 | telegram_bot |
| `telegram_bot.before_nameerror_fix.py` | 8 КБ | 2026-04-11 | telegram_bot |
| `telegram_bot.before_google_helpers_fix.py` | 8 КБ | 2026-04-11 | telegram_bot |
| `telegram_bot.before_google_manual_fix.py` | 10 КБ | 2026-04-11 | telegram_bot |
| `telegram_bot_old.py` | 5 КБ | 2026-03-25 | telegram_bot |

**Рекомендация:** все 19 безопасны для удаления — это снапшоты "до патча", которые git теперь хранит в коммите `d63d5e0`. Но решение за тобой.

---

### 3.3 — 5 agent_mesh роутеров (в `app/api/`)

| Файл | Размер | Дата | Примечание |
|---|---|---|---|
| `agent_mesh_router.py` | **14 КБ** | 2026-04-18 | Наибольший — скорее всего базовая версия |
| `agent_mesh_plus_router.py` | 7 КБ | 2026-04-18 | "+plus" надстройка |
| `agent_mesh_stability_router.py` | 3 КБ | 2026-04-18 | патч стабильности |
| `agent_mesh_real_exec_router.py` | 2 КБ | 2026-04-18 | реальное выполнение |
| `agent_mesh_improvement_router.py` | 3 КБ | 2026-04-19 | ⭐ ПОСЛЕДНИЙ по дате |

**Вопросы для обсуждения:**
1. Что реально зарегистрировано в `app/main.py`? (один или несколько роутеров из этих пяти)
2. `improvement_router` — это замена `router.py` или дополнение?
3. Нет ли дублирующихся endpoint'ов между роутерами?

---

### 3.4 — 8 n8n роутеров (в `app/routers/`)

| Файл | Размер | Дата | Примечание |
|---|---|---|---|
| `n8n_bridge_router.py` | 1 КБ | 2026-04-21 | v1 bridge |
| `n8n_canvas_builder_router.py` | 1 КБ | 2026-04-21 | builder |
| `n8n_workflow_materializer_router.py` | 1 КБ | 2026-04-21 | materializer v1 |
| `n8n_action_router_materializer_router.py` | 2 КБ | 2026-04-21 | action materializer v1 |
| `n8n_action_router_materializer_v2_router.py` | **3 КБ** | 2026-04-21 | action materializer v2 |
| `jarvis_n8n_bridge_router.py` | **3 КБ** | 2026-04-22 | jarvis bridge (новейший) |
| `jarvis_n8n_specialist_router.py` | 1 КБ | 2026-04-24 | ⭐ ПОСЛЕДНИЙ по дате |
| `jarvis_n8n_super_agent_router.py` | 1 КБ | 2026-04-24 | ⭐ ПОСЛЕДНИЙ по дате |

> ⚠️ `n8n_action_router_materializer_router.py` (строка 221 `main.py`) — единственный незащищённый bare-импорт без try/except. Если этот роутер будет удалён — упадёт весь старт приложения.

**Вопросы для обсуждения:**
1. `n8n_bridge_router.py` vs `jarvis_n8n_bridge_router.py` — это v1 и v2 одного и того же?
2. Нужны ли одновременно оба materializer'а (v1 и v2)?
3. Нужны ли `specialist_router` и `super_agent_router` — или это ранние эксперименты?

---

### 3.5 — 20+ memory модулей

Сгруппированы по подсистемам. `__pycache__` исключён.

**Группа A — Legacy/Core (старейшие, март–апрель)**

| Файл | Размер | Дата |
|---|---|---|
| `app/memory_state.py` | 3 КБ | 2026-03-28 |
| `app/api/memory.py` | 1 КБ | 2026-04-07 |
| `app/services/conversation_memory.py` | 3 КБ | 2026-04-07 |
| `app/services/mission_memory.py` | 7 КБ | 2026-04-07 |

**Группа B — Layer/Model (типы данных)**

| Файл | Размер | Дата |
|---|---|---|
| `app/api/memory_layer.py` | 1 КБ | 2026-04-12 |
| `app/models/memory_layer.py` | 1 КБ | 2026-04-12 |
| `app/api/mission_memory_bridge.py` | 1 КБ | 2026-04-13 |
| `app/api/mission_run_memory.py` | 1 КБ | 2026-04-13 |
| `app/models/auto_memory.py` | 1 КБ | 2026-04-13 |
| `app/models/mission_run_memory.py` | 1 КБ | 2026-04-13 |

> ⚠️ `memory_layer.py` существует в двух местах: `app/api/` и `app/models/`. Аналогично `mission_run_memory.py`.

**Группа C — Сервисы памяти (специализированные)**

| Файл | Размер | Дата |
|---|---|---|
| `app/services/semantic_memory.py` | 6 КБ | 2026-04-14 |
| `app/services/jarvis_memory_learning_layer.py` | **16 КБ** | 2026-04-23 |
| `app/services/memory/auto_memory_pipeline.py` | 2 КБ | 2026-04-13 |
| `app/services/memory/memory_analysis_service.py` | 4 КБ | 2026-04-13 |
| `app/services/memory/mission_run_memory_hook.py` | 3 КБ | 2026-04-13 |
| `app/services/memory/semantic_memory_service.py` | 6 КБ | 2026-04-12 |

> ⚠️ `semantic_memory.py` (app/services/) vs `semantic_memory_service.py` (app/services/memory/) — возможный дубль.

**Группа D — Autonomy & Control Plane**

| Файл | Размер | Дата |
|---|---|---|
| `app/autonomy/memory_manager.py` | 7 КБ | 2026-04-08 |
| `app/control_plane/memory.py` | 3 КБ | 2026-04-18 |
| `app/control_plane/memory_mesh.py` | 3 КБ | 2026-04-18 |
| `app/control_plane/skill_memory.py` | 5 КБ | 2026-04-18 |

**Вопросы для обсуждения:**
1. Группа A vs Группа C — есть ли живые зависимости от legacy модулей?
2. Дублирующиеся имена (`memory_layer`, `mission_run_memory`, `semantic_memory`) — нужны ли оба?
3. `jarvis_memory_learning_layer.py` (16 КБ, самый новый) — это финальная реализация или тоже эксперимент?

---

## Рекомендуемый порядок выполнения

```
Фаза 1.4 (.env.bak*)         ← 30 секунд, нулевой риск
Фаза 1.3 (*_old.py)          ← 30 секунд, нулевой риск
Фаза 1.1 (.bak*)              ← 2 минуты, нулевой риск, -193 МБ
Фаза 2 (11 старых backup/)   ← обсудить, потом удалить
Фаза 1.2 (.before_*.py)      ← после согласования Фазы 3.2
Фаза 3 (архитектура)          ← требует анализа кода
```
