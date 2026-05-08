# N8N_PLAN.md — Анализ и план для n8n роутеров

Дата: 2026-04-30  
**Вывод: 3 кандидата на удаление, 1 требует правки main.py перед удалением.**

---

## Таблица файлов

| Файл | Строк | Размер | Дата | Prefix | Endpoints |
|---|---|---|---|---|---|
| `n8n_bridge_router.py` | 48 | 1.5 КБ | 2026-04-21 | `/api/n8n` | 2 |
| `n8n_canvas_builder_router.py` | 48 | 1.5 КБ | 2026-04-21 | `/api/supervisor/canvas` | 2 |
| `n8n_workflow_materializer_router.py` | 45 | 1.5 КБ | 2026-04-21 | `/api/supervisor/materializer` | 2 |
| `n8n_action_router_materializer_router.py` | 82 | 2.4 КБ | 2026-04-21 | `/api/n8n/materializer` | 6 |
| `n8n_action_router_materializer_v2_router.py` | 97 | 2.9 КБ | 2026-04-21 | `/api/n8n/materializer-v2` | 7 |
| `jarvis_n8n_bridge_router.py` | 89 | 2.6 КБ | 2026-04-22 | `/api/jarvis/n8n` | 8 |
| `jarvis_n8n_specialist_router.py` | 58 | 1.5 КБ | 2026-04-24 | `/api/n8n-specialist` | 3 |
| `jarvis_n8n_super_agent_router.py` | 56 | 1.4 КБ | 2026-04-24 | `/api/n8n-super-agent` | 3 |
| **ИТОГО** | **523** | **15.3 КБ** | | | **33** |

---

## Endpoints по файлам

### n8n_bridge_router.py
```
GET  /api/n8n/health
POST /api/n8n/dispatch
```

### n8n_canvas_builder_router.py
```
GET  /api/supervisor/canvas/health
POST /api/supervisor/canvas/build
```

### n8n_workflow_materializer_router.py
```
GET  /api/supervisor/materializer/health
POST /api/supervisor/materializer/run
```

### n8n_action_router_materializer_router.py (v1)
```
GET  /api/n8n/materializer/health
GET  /api/n8n/materializer/debug-state
POST /api/n8n/materializer/dry-run-payload
POST /api/n8n/materializer/execute
POST /api/n8n/materializer/publish-workflow
POST /api/n8n/materializer/publish-and-probe
```

### n8n_action_router_materializer_v2_router.py (v2)
```
GET  /api/n8n/materializer-v2/health
GET  /api/n8n/materializer-v2/debug-state
POST /api/n8n/materializer-v2/dry-run-payload
POST /api/n8n/materializer-v2/create-only      ← только в v2
POST /api/n8n/materializer-v2/publish-workflow
POST /api/n8n/materializer-v2/publish-and-probe
POST /api/n8n/materializer-v2/execute
```

### jarvis_n8n_bridge_router.py
```
GET  /api/jarvis/n8n/health
GET  /api/jarvis/n8n/config
GET  /api/jarvis/n8n/public-api-check
GET  /api/jarvis/n8n/workflows
POST /api/jarvis/n8n/workflows/create-webhook
POST /api/jarvis/n8n/workflows/activate/{workflow_id}
POST /api/jarvis/n8n/probe/{webhook_path}
POST /api/jarvis/n8n/smoke/local-webhook
```

### jarvis_n8n_specialist_router.py
```
GET  /api/n8n-specialist/health
GET  /api/n8n-specialist/latest
POST /api/n8n-specialist/run
```

### jarvis_n8n_super_agent_router.py
```
GET  /api/n8n-super-agent/health
GET  /api/n8n-super-agent/latest
POST /api/n8n-super-agent/run
```

---

## Анализ: уникальные vs дубли

### Дубли по смыслу (не по пути — пути разные)

| Роутер 1 | Роутер 2 | Вердикт |
|---|---|---|
| `n8n_bridge_router` (`POST /api/n8n/dispatch`) | `jarvis_n8n_bridge_router` (полный API) | dispatch = упрощённая обёртка; bridge — полный API |
| `n8n_workflow_materializer_router` (`POST /supervisor/materializer/run`) | `n8n_action_router_materializer_v2_router` | старая обёртка vs полноценный v2 |
| `n8n_action_router_materializer_router` (v1) | `n8n_action_router_materializer_v2_router` (v2) | v2 добавляет `POST /create-only`; оба активны |
| `jarvis_n8n_specialist_router` | `jarvis_n8n_super_agent_router` | **НЕ дубли** — разные сервисы, super_agent оборачивает specialist |

### Уникальные endpoints (есть только в одном файле)
- `POST /api/n8n/dispatch` — только в `n8n_bridge_router`
- `POST /api/supervisor/canvas/build` — только в `n8n_canvas_builder_router`
- `POST /api/n8n/materializer-v2/create-only` — только в v2
- `GET /api/jarvis/n8n/config`, `/public-api-check`, `/workflows`, webhooks — только в `jarvis_n8n_bridge_router`
- `GET /api/n8n-super-agent/latest`, `POST /run` — только в super_agent

---

## Рекомендация

### Кандидаты на удаление

| Файл | Решение | Обоснование |
|---|---|---|
| `n8n_bridge_router.py` | ⭐ УДАЛИТЬ | `POST /api/n8n/dispatch` — упрощённая обёртка. `jarvis_n8n_bridge_router` делает всё то же и больше. Нужно проверить нет ли клиентов вызывающих `/api/n8n/dispatch` напрямую |
| `n8n_workflow_materializer_router.py` | ⭐ УДАЛИТЬ | Старая обёртка (`/api/supervisor/materializer/run`). v2 materializer делает то же самое и больше. Нужно убедиться что нет скриптов вызывающих этот endpoint |
| `n8n_canvas_builder_router.py` | ❓ ОБСУДИТЬ | `POST /api/supervisor/canvas/build` — непонятно связан ли с materializer или отдельная логика. Нужно проверить `app/services/n8n_canvas_builder.py` если существует |

### Оставить

| Файл | Решение | Обоснование |
|---|---|---|
| `n8n_action_router_materializer_router.py` | ⚠️ ОСТАВИТЬ (пока) | Hard-import в main.py строка 222 (уже обёрнут в try/except после Phase 3.2). Но если `canvas_builder` и `workflow_materializer` уберём — этот v1 тоже можно убрать после проверки |
| `n8n_action_router_materializer_v2_router.py` | ✅ ОСТАВИТЬ | Активная, v2 с `create-only`. Это production materializer |
| `jarvis_n8n_bridge_router.py` | ✅ ОСТАВИТЬ | Полный API управления workflow — создание, активация, тест |
| `jarvis_n8n_specialist_router.py` | ✅ ОСТАВИТЬ | Другой сервис, другая задача |
| `jarvis_n8n_super_agent_router.py` | ✅ ОСТАВИТЬ | Оборачивает specialist с расширенной логикой |

---

## Порядок удаления (предлагаемый, после твоего одобрения)

1. **Шаг 1**: Проверить нет ли вызовов `/api/n8n/dispatch` и `/api/supervisor/materializer/run` в scripts/
2. **Шаг 2**: Удалить `n8n_bridge_router.py` + `n8n_workflow_materializer_router.py` → удалить из main.py
3. **Шаг 3**: Решить по `n8n_canvas_builder_router.py` — изучить сервис
4. **Шаг 4** (опционально): Если v1 materializer не нужен — убрать с исправлением main.py

---

> **Итого потенциально можно убрать 2–3 файла (33–48 строк кода).  
> Главный риск: `/api/n8n/dispatch` может использоваться скриптами или Telegram-ботом напрямую.**
