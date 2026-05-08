# IDENTITY_DISCOVERY.md — Phase 4.1

Date: 2026-04-30  
**Итого: 10 мест формирования identity. 2 без Jarvis-идентичности вообще. Язык: 5 RU / 5 EN.**

---

## Сводная таблица (все 10 мест)

| # | Файл | Строка | Константа / тип | Язык | Качество | Провайдер |
|---|---|---|---|---|---|---|
| 1 | `app/services/multi_ai_orchestrator_v1.py` | 11 | `JARVIS_SYSTEM_PROMPT` | RU | ⭐⭐⭐⭐⭐ | OpenAI + Anthropic |
| 2 | `app/routers/jarvis_brain_v2.py` | 18 | `JARVIS_IDENTITY` | RU | ⭐⭐⭐⭐⭐ | OpenAI-compatible |
| 3 | `app/services/llm_router.py` | 53 | `SYSTEM_PROMPT` | EN | ⭐⭐⭐ | Ollama |
| 4 | `app/services/conversation_brain.py` | ~186 | inline | RU | ⭐⭐ | OpenAI (direct POST) |
| 5 | `app/ai_provider.py` | 18 | inline fallback | RU | ⭐ 🔴 | OpenAI SDK |
| 6 | `app/services/ai/agent_dispatcher.py` | 61 | `build_system_prompt()` | EN | ⭐⭐⭐ | multi-provider |
| 7 | `app/services/ai/specialized_prompt_builder.py` | ~23 | inline в task prompt | EN | ⭐⭐ | specialized agents |
| 8 | `app/services/internet_agent.py` | 66 | inline в perplexity_research | EN | ⭐⭐⭐ | Perplexity |
| 9 | `app/services/jarvis_internet_tools.py` | 128 | inline fallback | EN | ⭐⭐⭐ | OpenAI (direct POST) |
| 10 | `app/control_plane/adapters.py` | 218 | inline | EN | ⭐ 🔴 | OpenAI + Anthropic |

---

## Детальный разбор каждого места

### 1. `multi_ai_orchestrator_v1.py` — ЛУЧШИЙ (эталон)
```python
JARVIS_SYSTEM_PROMPT = """
Ты — Jarvis V3 Supervisor.
Ты локальный AI-оператор Daniil-а.
Ты НЕ Perplexity. Ты НЕ Luxify Assistant. Ты НЕ сторонний Jarvis из интернета.
[7 правил включая routing по провайдерам]
"""
CLAUDE_HINT = "Ты работаешь как Claude Architect/Coding Agent внутри Jarvis..."
OPENAI_HINT = "Ты работаешь как быстрый reasoning/dialogue agent внутри Jarvis..."
```
**Провайдеры:** OpenAI (`call_openai`), Anthropic (`call_anthropic`), local fallback  
**Достоинства:** Русский, негативные правила ("не Perplexity/Luxify"), provider-specific hints  
**Проблема:** Определён только в этом файле, не переиспользован нигде

---

### 2. `jarvis_brain_v2.py` — ЛУЧШИЙ (+ regex guard)
```python
JARVIS_IDENTITY = """
Ты — Jarvis V3 Supervisor.
Ты НЕ Perplexity. Ты НЕ Luxify Assistant. Ты НЕ сторонний Jarvis из интернета.
Ты НЕ должен рекламировать чужие продукты.
Ты локальный оператор Daniil-а... [operator modes: control/brain/engineer/research/mission/n8n]
"""
BAD_IDENTITY_PATTERNS = [r"\bperplexity\b", r"\bluxify\b", r"куп(ить|айте)", ...]
```
**Провайдер:** OpenAI-compatible (LLM_BASE_URL)  
**Достоинства:** Единственное место с BAD_IDENTITY_PATTERNS regex guard на output  
**Проблема:** Guard применяется только в `jarvis_brain_v2`, остальные 9 мест — без фильтрации

---

### 3. `llm_router.py` — СРЕДНИЙ
```python
SYSTEM_PROMPT = """You are Jarvis V3 Supervisor.
Rules: 1. Prefer Russian if user writes Russian. 2-6. [практические правила]"""
```
**Провайдер:** Ollama (`call_ollama`)  
**Проблема:** Английский текст, нет негативных правил ("не Perplexity"), нет упоминания Daniil

---

### 4. `conversation_brain.py` — СЛАБЫЙ (inline)
```python
"Ты Jarvis V3 Supervisor Assistant. Отвечай по-русски, "
"естественно, полезно, связно и инженерно-практично."
```
**Провайдер:** OpenAI (прямой POST)  
**Проблема:** Урезанная версия без негативных правил, hardcoded inline

---

### 5. `ai_provider.py` — КРИТИЧЕСКИЙ 🔴
```python
instructions = system_prompt or (
    "Ты полезный русскоязычный AI-ассистент. "
    "Отвечай ясно, кратко и по делу. "
    "Если это уточнение к предыдущему вопросу, учитывай контекст."
)
```
**Провайдер:** OpenAI SDK  
**ПРОБЛЕМА:** Никакой Jarvis-идентичности! Дефолтный fallback — "полезный ассистент" = 
именно отсюда модель отвечает как generic Claude/ChatGPT, не как Jarvis.

---

### 6. `agent_dispatcher.py` — СРЕДНИЙ (4 роли)
```python
TaskType.GENERAL:   "You are Jarvis AI general agent. Answer clearly..."
TaskType.REASONING: "You are Jarvis reasoning agent. Focus on structured analysis..."
TaskType.CODING:    "You are Jarvis coding agent. Focus on implementation..."
TaskType.RESEARCH:  "You are Jarvis research agent. Focus on factual accuracy..."
```
**Провайдер:** multi-provider (через ai_router_service)  
**Проблема:** Английский, минимальные (1-2 предложения), нет контекста владельца/системы

---

### 7. `specialized_prompt_builder.py` — СЛАБЫЙ (inline в task)
```python
"You are Jarvis coding agent.\n"
"Respond in a concise implementation-focused format.\n..."
# identity встроен в task prompt, не как отдельный system message
```
**Провайдер:** specialized agents  
**Проблема:** Identity вставлен в user-часть промпта, не в system message

---

### 8. `internet_agent.py` — СРЕДНИЙ (Perplexity)
```python
{"role": "system", "content": "You are Jarvis research agent. Find practical, 
current, implementation-oriented advice. Return concise recommendations."}
```
**Провайдер:** Perplexity API  
**Проблема:** Английский, минимальный, Perplexity всё равно отвечает в своём стиле

---

### 9. `jarvis_internet_tools.py` — СРЕДНИЙ (fallback)
```python
system_prompt = "You are Jarvis Internet Research Agent. ..."
# только при отсутствии system_prompt в запросе
```
**Провайдер:** OpenAI (прямой POST)  
**Проблема:** Английский, только в fallback ветке

---

### 10. `control_plane/adapters.py` — КРИТИЧЕСКИЙ 🔴
```python
system = (
    "You are a structured agent inside a local multi-agent supervisor. "
    "Return ONLY valid JSON with keys: ..."
)
```
**Провайдер:** OpenAI + Anthropic  
**ПРОБЛЕМА:** Нет имени Jarvis вообще. Agent отвечает как anonymous "structured agent".

---

## Ключевые расхождения

| Проблема | Затронутые файлы |
|---|---|
| **Нет Jarvis identity вообще** | `ai_provider.py` (дефолт), `adapters.py` |
| **Нет негативных правил** (не Perplexity/Luxify) | #3, #4, #6, #7, #8, #9, #10 |
| **Нет BAD_IDENTITY regex guard на output** | все кроме `jarvis_brain_v2.py` |
| **Английский вместо русского** | #3, #6, #7, #8, #9, #10 |
| **Нет упоминания владельца** (Daniil) | #3, #4, #5, #6, #7, #8, #9, #10 |
| **Identity hardcoded inline** (не переиспользуется) | #4, #5, #7, #8, #9, #10 |

---

## Провайдеры по coverage

| Провайдер | Охват identity | Качество |
|---|---|---|
| OpenAI (через orchestrator) | ✅ JARVIS_SYSTEM_PROMPT | ⭐⭐⭐⭐⭐ |
| Anthropic (через orchestrator) | ✅ JARVIS_SYSTEM_PROMPT + CLAUDE_HINT | ⭐⭐⭐⭐⭐ |
| OpenAI (через ai_provider.py) | 🔴 generic "ассистент" | ⭐ |
| OpenAI (через conversation_brain) | ⚠️ урезанный | ⭐⭐ |
| OpenAI (через adapters.py) | 🔴 нет Jarvis | ⭐ |
| Ollama | ⚠️ английский, неполный | ⭐⭐⭐ |
| Perplexity | ⚠️ английский, минимальный | ⭐⭐⭐ |

---

## Root cause проблемы "отвечает как Claude/Perplexity"

1. **`ai_provider.py`** — основной OpenAI клиент имеет дефолт "полезный ассистент"
   без Jarvis identity. Это входная точка для многих путей.
2. Нет единого `BAD_IDENTITY_PATTERNS` guard — если модель "протекает" в чужую identity,
   нет ни одного фильтра кроме `jarvis_brain_v2.py`.
3. 8 из 10 мест не имеют негативных правил ("не Perplexity").

---

## Список файлов для унификации (приоритет)

| Приоритет | Файл | Действие |
|---|---|---|
| 🔴 P0 | `app/ai_provider.py` | Заменить дефолт на Jarvis identity |
| 🔴 P0 | `app/control_plane/adapters.py` | Добавить Jarvis context к structured agent |
| 🟡 P1 | `app/services/llm_router.py` | Обновить SYSTEM_PROMPT → identity_core |
| 🟡 P1 | `app/services/conversation_brain.py` | Заменить inline → identity_core |
| 🟡 P1 | `app/services/ai/agent_dispatcher.py` | Обновить build_system_prompt → identity_core |
| 🟢 P2 | `app/services/ai/specialized_prompt_builder.py` | Перенести identity в system (не user) |
| 🟢 P2 | `app/services/internet_agent.py` | Обновить → identity_core |
| 🟢 P2 | `app/services/jarvis_internet_tools.py` | Обновить fallback → identity_core |

**Сохранить как есть (эталоны):**
- `app/services/multi_ai_orchestrator_v1.py` — эталонный контент
- `app/routers/jarvis_brain_v2.py` — эталон + единственный BAD_IDENTITY guard
