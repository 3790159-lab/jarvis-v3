# PHASE4_COMPLETION.md — Identity Core

Date: 2026-04-30  
Status: **COMPLETE**

---

## Проблема (до Phase 4)

Jarvis V3 Supervisor отвечал как generic AI-ассистент вместо себя.

Root cause: identity была определена в **10 разных местах** с разным качеством.
2 места не имели имени Jarvis вообще. 8 мест не имели негативных правил
("не Perplexity", "не Luxify"). Нет единого sanitize guard.

---

## Что стало после Phase 4

**Single source of truth:** `app/services/identity_core.py`
- Только stdlib (`re`, `typing`) — ноль зависимостей
- `get_system_prompt(role, lang, provider_hint)` — 5 ролей × 2 языка × 3 провайдера
- `BAD_IDENTITY_PATTERNS` — канонический список regex-паттернов
- `sanitize_response(text)` — фильтрация output

**8 файлов интегрированы** (Phase 4.4 + 4.5):

| Файл | До | После |
|---|---|---|
| `app/ai_provider.py` | `"Ты полезный русскоязычный AI-ассистент"` ← **root cause** | `get_system_prompt("supervisor")` |
| `app/services/llm_router.py` | `SYSTEM_PROMPT` inline EN, без negative rules | `get_system_prompt("supervisor", lang="en")` |
| `app/services/conversation_brain.py` | `"Ты Jarvis V3 Supervisor Assistant"` (урезан) | `get_system_prompt("supervisor")` |
| `app/services/internet_agent.py` | `"You are Jarvis research agent."` (1 строка) | `get_system_prompt("researcher", lang="en")` |
| `app/services/jarvis_internet_tools.py` | Inline fallback, EN, без owner | `get_system_prompt("researcher")` |
| `app/services/ai/agent_dispatcher.py` | 4 hardcoded inline строки | `role_map[task_type]` + `get_system_prompt` |
| `app/control_plane/adapters.py` | `"structured agent inside a local multi-agent supervisor"` — нет Jarvis | `f"structured {JARVIS_NAME} agent"` (JSON-контракт сохранён) |
| `app/services/ai/specialized_prompt_builder.py` | `"You are Jarvis X agent\n"` в user-части промпта (дубль) | Удалено — identity уже в system_prompt через agent_dispatcher |

**Сохранены как эталоны** (уже имели сильную identity):
- `app/services/multi_ai_orchestrator_v1.py` — `JARVIS_SYSTEM_PROMPT` (RU, с provider hints)
- `app/routers/jarvis_brain_v2.py` — `JARVIS_IDENTITY` + `BAD_IDENTITY_PATTERNS` guard

---

## Метрики

| Метрика | Значение |
|---|---|
| Тестов unit (test_identity_core.py) | 32 |
| Тестов E2E (test_identity_e2e.py) | 35 |
| **Итого тестов** | **67** |
| Коммитов Phase 4 | 12 |
| Файлов с identity изменено | 8 |
| Файлов удалено (Phase 3.7) | 3 |
| Новых файлов создано | `identity_core.py`, `tests/` (2 файла) |

---

## Проверка end-to-end (Phase 4.6)

```
POST /api/respond {"message": "ты тут?"}
→ ok: true, mode: "control"
→ answer содержит: "Jarvis V3 Supervisor" ✅
→ answer содержит: "V3" ✅
→ answer НЕ содержит: "Perplexity" (как чужая identity) ✅
→ answer НЕ содержит: "Claude", "GPT" ✅
```

```
GET /health
→ {"status":"healthy","service":"jarvis_v3_supervisor","env":"dev"} ✅
```

---

## Открытые вопросы для будущих фаз

### Phase 5: Capability Registry
**Проблема:** Jarvis иногда утверждает возможности которых нет, или отказывается
от возможностей которые есть.  
**Решение:** Создать `app/services/capability_registry.py` с явным списком
`AVAILABLE / PARTIAL / UNAVAILABLE` по каждой capabilities. `get_system_prompt`
должен инжектировать актуальный snapshot в system message.

### Phase 6: Honest Fallback
**Проблема:** Когда LLM provider недоступен (нет API key), Jarvis отдаёт
`"⚠️ LLM provider недоступен: HTTPError"` — не-информативно и не в стиле Jarvis.  
**Решение:** `_fallback_answer()` в `jarvis_brain_v2.py` должен быть более
детальным: объяснять что именно недоступно и предлагать offline-действия.

### Phase 7: Provider-aware identity
**Проблема:** `CLAUDE_HINT` и `OPENAI_HINT` в `multi_ai_orchestrator_v1.py`
по-прежнему hardcoded, не используют `identity_core`.  
**Решение:** Обновить `multi_ai_orchestrator_v1.py` — передавать
`provider_hint="anthropic"/"openai"` в `get_system_prompt()` вместо своих hints.

### Phase 8: BAD_IDENTITY_PATTERNS coverage
**Наблюдение:** `jarvis_brain_v2._sanitize_answer()` использует свою копию
guard-а, а `identity_core.sanitize_response()` — отдельную. Нужна унификация:
`jarvis_brain_v2.py` должен импортировать `sanitize_response` из `identity_core`.  
**Текущее состояние:** Оба guard работают правильно, но из двух мест.

---

## Структура файлов после Phase 4

```
app/services/
├── identity_core.py          ← NEW: single source of truth
├── ai_provider.py            ← UPDATED
├── llm_router.py             ← UPDATED
├── conversation_brain.py     ← UPDATED
├── internet_agent.py         ← UPDATED
├── jarvis_internet_tools.py  ← UPDATED
└── ai/
    ├── agent_dispatcher.py       ← UPDATED
    └── specialized_prompt_builder.py  ← UPDATED (removed duplication)

app/control_plane/
└── adapters.py               ← UPDATED (JARVIS_NAME injected)

tests/
├── test_identity_core.py     ← NEW: 32 unit tests
└── test_identity_e2e.py      ← NEW: 35 E2E tests
```
