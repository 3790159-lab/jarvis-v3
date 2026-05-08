# AGENT_MESH_PLAN.md — Анализ и план для agent_mesh роутеров

Дата: 2026-04-30  
**Вывод: все 5 роутеров нужны, дублей нет. Удалять нечего.**

---

## Таблица файлов

| Файл | Строк | Размер | Дата | Endpoints |
|---|---|---|---|---|
| `agent_mesh_router.py` | 426 | 14 КБ | 2026-04-18 | 22 |
| `agent_mesh_plus_router.py` | 224 | 7 КБ | 2026-04-18 | 15 |
| `agent_mesh_stability_router.py` | 77 | 2.6 КБ | 2026-04-18 | 4 |
| `agent_mesh_real_exec_router.py` | 57 | 1.7 КБ | 2026-04-18 | 8 |
| `agent_mesh_improvement_router.py` | 90 | 2.6 КБ | 2026-04-19 | 9 |
| **ИТОГО** | **874** | **28.6 КБ** | | **58** |

Все зарегистрированы в `app/main.py` через startup (try/except).  
Все используют один prefix: `/api/agent-mesh`.

---

## Endpoints по файлам

### agent_mesh_router.py — ЯДРО (22 endpoints)
```
GET  /api/agent-mesh/health
GET  /api/agent-mesh/adaptive-policy
GET  /api/agent-mesh/auth/health
GET  /api/agent-mesh/connector-policy/{agent_id}/{capability}
POST /api/agent-mesh/runtime/connectors/execute
POST /api/agent-mesh/runtime/integration-task/execute
GET  /api/agent-mesh/connectors/log
GET  /api/agent-mesh/strategy/active
GET  /api/agent-mesh/missions/{mission_id}
POST /api/agent-mesh/demo-run-v10
GET  /api/agent-mesh/learning/health
POST /api/agent-mesh/learning/rebuild
GET  /api/agent-mesh/learning/variants
POST /api/agent-mesh/learning/variants/propose
POST /api/agent-mesh/learning/variants/approve/{variant_id}
POST /api/agent-mesh/learning/variants/apply/{variant_id}
GET  /api/agent-mesh/learning/experiments/runs
POST /api/agent-mesh/learning/experiments/run
GET  /api/agent-mesh/learning/recommendation
GET  /api/agent-mesh/learning/skills
GET  /api/agent-mesh/learning/lessons
POST /api/agent-mesh/learning/guidance/preview
```

### agent_mesh_plus_router.py — РАСШИРЕНИЯ (15 endpoints)
```
GET  /api/agent-mesh/lifecycle/health
POST /api/agent-mesh/lifecycle/cleanup
POST /api/agent-mesh/knowledge/ingest
GET  /api/agent-mesh/knowledge/library
GET  /api/agent-mesh/knowledge/domains
GET  /api/agent-mesh/knowledge/review-queue
POST /api/agent-mesh/knowledge/review/approve/{review_id}
POST /api/agent-mesh/knowledge/review/reject/{review_id}
GET  /api/agent-mesh/knowledge/negative-rules
GET  /api/agent-mesh/service-traces
GET  /api/agent-mesh/service-telemetry
GET  /api/agent-mesh/communication-policy
GET  /api/agent-mesh/autonomy/health
POST /api/agent-mesh/autonomy/tick
GET  /api/agent-mesh/autonomy/history
```

### agent_mesh_stability_router.py — САМОИСЦЕЛЕНИЕ (4 endpoints)
```
GET  /api/agent-mesh/service-guard/health
GET  /api/agent-mesh/self-healing/health
POST /api/agent-mesh/self-healing/tick
GET  /api/agent-mesh/service-resolution/example
```

### agent_mesh_real_exec_router.py — РЕАЛЬНОЕ ВЫПОЛНЕНИЕ (8 endpoints)
```
GET  /api/agent-mesh/n8n/health
POST /api/agent-mesh/n8n/verify
POST /api/agent-mesh/n8n/test-webhook
POST /api/agent-mesh/n8n/promote-live
POST /api/agent-mesh/traces/rebuild
POST /api/agent-mesh/autonomy/enable
POST /api/agent-mesh/autonomy/disable
GET  /api/agent-mesh/real-exec/health
```

### agent_mesh_improvement_router.py — УЛУЧШЕНИЯ (9 endpoints)
```
GET  /api/agent-mesh/improvement/health
GET  /api/agent-mesh/improvement/proposals
POST /api/agent-mesh/improvement/tick
GET  /api/agent-mesh/improvement/registry
POST /api/agent-mesh/improvement/continuous/start
POST /api/agent-mesh/improvement/continuous/stop
GET  /api/agent-mesh/improvement/continuous/status
GET  /api/agent-mesh/improvement/journal
GET  /api/agent-mesh/claude-probe/health
```

---

## Анализ дублей

### Нет конфликтующих путей
Все 58 endpoints имеют уникальные пути. Нет пересечений между файлами.

### Смысловая близость (не конфликт)
- `agent_mesh_plus_router`: `GET /autonomy/health`, `POST /autonomy/tick`, `GET /autonomy/history`
- `agent_mesh_real_exec_router`: `POST /autonomy/enable`, `POST /autonomy/disable`

Это разные операции (`health/tick/history` vs `enable/disable`), не дубли.

---

## Рекомендация

| Роутер | Решение | Обоснование |
|---|---|---|
| `agent_mesh_router.py` | ✅ ОСТАВИТЬ — база | 426 строк, ядро системы обучения и коннекторов |
| `agent_mesh_plus_router.py` | ✅ ОСТАВИТЬ | Знания, жизненный цикл, мониторинг — отдельная ответственность |
| `agent_mesh_stability_router.py` | ✅ ОСТАВИТЬ (или объединить) | 77 строк, можно влить в router.py если мешает |
| `agent_mesh_real_exec_router.py` | ✅ ОСТАВИТЬ (или объединить) | 57 строк, можно влить в router.py если мешает |
| `agent_mesh_improvement_router.py` | ✅ ОСТАВИТЬ — самый новый | Создан позже остальных (2026-04-19), активная функция |

### Опциональная консолидация (не обязательна)
Если хочется сократить количество файлов без потери функционала — можно объединить `stability_router` (77L) и `real_exec_router` (57L) в `agent_mesh_router.py`. Итоговый размер: ~560L, управляемо.

**Но это рефактор, не удаление. Риск нулевой только при наличии тестов.**

---

## Итог

> **Удалять нечего.** Все 5 роутеров активны, endpoints не дублируются, разделение по ответственностям логично. Максимум — опциональная консолидация stability + real_exec в основной файл.
