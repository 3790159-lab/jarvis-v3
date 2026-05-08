# PROGRESS.md — Состояние чистки репозитория

Обновлён: 2026-04-29

---

## Git история

```
63a7144  Phase 2.15: Removed jarvis_stage3
40269a0  Phase 2.14: Removed hotfix_backups
4987b65  Phase 2.13: Removed backup_utf8_fix_20260406_175349
26b5c46  Phase 2.12: Removed backup_stage3_conversational_brain_20260407_014821
aec7d7b  Phase 2.11: Removed backup_stage1_stable_core_20260406_232958
2736a10  Phase 2.10: Removed backup_respond_endpoint_20260406_195223
e91b385  Phase 2.9:  Removed backup_import_fix
7bfe268  Phase 2.8:  Removed backup_header_repair_20260406_183001
91e58ec  Phase 2.7:  Removed backup_goals_api_fix_20260406_203108
5677cb8  Phase 2.6:  Removed backup_full_repair_20260406_183741
8d17349  Phase 2.5:  Removed backup_fix_router_conflict_20260407_003324
f8c45c5  Phase 2.4:  Removed backup_fix_future_imports
5b04f57  Phase 2.3:  Removed backup
688564c  Phase 2.2:  Removed app_backup_before_upgrade
a71a205  Phase 2.1:  Removed app_backup_before_supervisor_final
a3ac002  Phase 1.4:  Removed .bak files (1872 files, ~193 MB)
788630d  Phase 1.3:  Removed .before_*.py snapshots (32 files)
c651cb9  Phase 1.2:  Removed _old.py files (2 files)
0232cdf  Phase 1.1:  Removed .env backups (14 files)
d63d5e0  Initial state before consolidation       ← точка восстановления
```

---

## ✅ СДЕЛАНО

### Фаза 1 — Безопасное удаление по паттернам
- [x] 1.1 — 14 файлов `.env.bak*` (~25 КБ)
- [x] 1.2 — 2 файла `*_old.py` (~9 КБ)
- [x] 1.3 — 32 файла `.before_*.py` (~241 КБ)
- [x] 1.4 — 1 872 файла `.bak*` (~193 МБ)

**Итого Фаза 1: ~1 920 файлов, ~193.3 МБ**

### Фаза 2 — Backup-директории
- [x] 2.1  — `app_backup_before_supervisor_final/`
- [x] 2.2  — `app_backup_before_upgrade/`
- [x] 2.3  — `backup/`
- [x] 2.4  — `backup_fix_future_imports/`
- [x] 2.5  — `backup_fix_router_conflict_20260407_003324/`
- [x] 2.6  — `backup_full_repair_20260406_183741/`
- [x] 2.7  — `backup_goals_api_fix_20260406_203108/`
- [x] 2.8  — `backup_header_repair_20260406_183001/`
- [x] 2.9  — `backup_import_fix/`
- [x] 2.10 — `backup_respond_endpoint_20260406_195223/`
- [x] 2.11 — `backup_stage1_stable_core_20260406_232958/`
- [x] 2.12 — `backup_stage3_conversational_brain_20260407_014821/`
- [x] 2.13 — `backup_utf8_fix_20260406_175349/`
- [x] 2.14 — `hotfix_backups/`
- [x] 2.15 — `jarvis_stage3/`

**Итого Фаза 2: 15 директорий, ~627 КБ**

### Аналитика
- [x] `AUDIT.md` — первичная рекогносцировка
- [x] `DELETION_PLAN.md` — план трёх фаз
- [x] `PHASE3_ANALYSIS.md` — детальный анализ для Фазы 3

---

## ❌ НЕ СДЕЛАНО — Фаза 3

**Требует принятия решений. Читай `PHASE3_ANALYSIS.md`.**

### 3.1 — Дублирующие main.py (кандидаты на удаление)
- [ ] `app/main_local_n8n_bridge.py` — вероятно заменён v2
- [ ] `app/main_stage_b_v2.py` — обсудить нужность

### 3.2 — Дублирующие memory модули
- [ ] `app/api/memory_layer.py` vs `app/models/memory_layer.py` — дубль имён
- [ ] `app/services/semantic_memory.py` vs `app/services/memory/semantic_memory_service.py` — дубль функционала
- [ ] `app/api/mission_run_memory.py` vs `app/models/mission_run_memory.py` — дубль имён
- [ ] Группа A legacy (`mission_memory.py`, `conversation_memory.py`) — живые ли импорты?

### 3.3 — n8n роутеры (кандидаты на удаление)
- [ ] `n8n_bridge_router.py` — vs `jarvis_n8n_bridge_router`?
- [ ] `n8n_workflow_materializer_router.py` — vs v2?
- [ ] `n8n_action_router_materializer_router.py` — ⚠️ требует правки `app/main.py` строка 222 перед удалением
- [ ] `jarvis_n8n_specialist_router.py` vs `jarvis_n8n_super_agent_router.py` — дубли?

### 3.4 — Критические баги в app/main.py (из AUDIT.md)
- [ ] Строка 3: `from __future__ import annotations` стоит НЕ первой — SyntaxError
- [ ] Строки 7 и 18: двойной импорт `time_brain`
- [ ] Строка 221: незащищённый bare-импорт `n8n_action_router_materializer_router`

---

## Команда возобновления

При старте новой сессии скажи Claude:

```
Прочитай PROGRESS.md и PHASE3_ANALYSIS.md.
Мы на Фазе 3 — архитектурные решения.
Git safety net: d63d5e0 (git log --oneline покажет всю историю).
Начнём с анализа живых импортов для memory модулей.
```

---

## Восстановление если что-то пошло не так

```bash
# Восстановить всё до начала чистки:
git checkout d63d5e0

# Восстановить конкретный файл:
git checkout d63d5e0 -- app/main.py
```
