# BLOCK_E_PLAN.md — Self-Improvement + Advanced Orchestration

Date: 2026-05-01  
Status: planning  
Prerequisite: Block D2 complete (676 tests, 9 commits total)

---

## ЧАСТЬ 1: Scheduled Tasks (Phase 28) ~1.5h

### 1.A /remind command

```
/remind через 2ч сделать X
/remind в 18:00 позвонить Y
/remind завтра утром отчёт
```

- Parser: natural language → datetime (ru + en)
- Storage: `state/reminders.json`
- Background thread: checks every 30s, delivers via Telegram send()
- `/reminders` — list active reminders
- `/cancel <id>` — cancel specific reminder

### 1.B Daily Summary (auto)

At 23:59 each day, Jarvis sends:
- Stats: N messages processed, top 3 intents, N errors
- Summary text → appended to Obsidian vault (`state/daily_notes/`)
- Optional: n8n workflow trigger for digest email

### Tests (15+)

---

## ЧАСТЬ 2: Self-Improvement Loop (Phase 29) ~2h

### 2.A Error logging + pattern detection

```python
# state/error_log.json — auto-populated by bot on exception
{"timestamp": "...", "intent": "research", "query": "...", "error": "...", "user_feedback": null}
```

When user says "это неправильно" / "not correct" / "ошибка":
- Record negative feedback to error_log
- Trigger self-analysis after 3+ negative feedbacks

### 2.B Self-analysis via Claude

```python
def analyze_failures(n_recent: int = 10) -> str:
    """Send recent failures to Claude and get improvement suggestions."""
    # Returns: {"issue": "...", "fix": "...", "prompt_patch": "..."}
```

### 2.C Auto-patch routing rules

If Claude suggests a new keyword → add to `simple_triggers` or `research_triggers`
Changes go to `state/routing_patches.json`, loaded on next restart

### Tests (10+)

---

## ЧАСТЬ 3: Parallel Multi-Agent Execution (Phase 30) ~2h

### 3.A Real LLM agent calls

Currently `_call_agent` returns stub for LLM agents. Phase 30:
- `claude_coder` → direct Anthropic API call (claude-sonnet-4-6)
- `openai_reasoner` → direct OpenAI API call (gpt-4o)
- `perplexity_researcher` → Perplexity PPLX API

### 3.B Parallel execution

```python
import concurrent.futures

def execute_plan_parallel(plan: ExecutionPlan) -> list:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_call_agent, step.agent_id, step.input_query): step 
                   for step in plan.steps}
        results = []
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results
```

### 3.C Streaming progress

Edit single message in Telegram as each agent completes:
```
[1/3] ⏳ Perplexity ищет...
[1/3] ✅ Perplexity: 5 источников (8.2s)
[2/3] ⏳ Claude Coder формирует код...
```

### Tests (15+)

---

## ЧАСТЬ 4: Universal Service Bridge (Phase 31) ~1.5h

### 4.A Generic REST API adapter

```yaml
# state/service_configs/weather_api.yaml
name: weather
base_url: https://api.openweathermap.org/data/2.5
auth: {type: api_key, header: appid, env: WEATHER_API_KEY}
endpoints:
  current: {path: /weather, params: [q, units]}
  forecast: {path: /forecast, params: [q, cnt, units]}
```

### 4.B Bot integration

`/service weather current Moscow` → loads config → builds request → returns result

Auto-discovery: `state/service_configs/*.yaml` → available as Telegram commands

### Tests (10+)

---

## ЧАСТЬ 5: Dynamic Agent Spawning (Phase 32) ~1h

### 5.A Agent creation from description

When user says "создай агента для работы с Notion":
- Claude generates agent config (endpoint, description, cost_per_use)
- Config saved to `state/agent_configs/notion_agent.json`
- Auto-registered in agent_registry
- Available immediately in smart_router

### 5.B Agent templates

Pre-built templates:
- HTTP REST agent (webhook URL + auth)
- n8n workflow agent (workflow name)
- Python script agent (path to script)

### Tests (10+)

---

## Dependencies for Block E

```
# Already available (no new deps needed):
# - anthropic (Phase 23/27)
# - openai (Phase 26)
# - concurrent.futures (stdlib)
# - yaml parsing: pyyaml (check if installed)
```

---

## Estimated Time

| Phase | Hours |
|-------|-------|
| 28: Scheduled tasks | 1.5h |
| 29: Self-Improvement | 2h |
| 30: Parallel agents | 2h |
| 31: Service bridge | 1.5h |
| 32: Dynamic spawning | 1h |
| **Total** | **~8h** |

---

## Success Criteria

- [ ] `/remind через 1ч` → delivered on time
- [ ] Daily summary auto-sent at 23:59
- [ ] 3 consecutive failures → auto-analysis via Claude → routing patch
- [ ] Compound task runs Perplexity + Claude in parallel (< 15s instead of 30s)
- [ ] `/service weather current Moscow` → real weather data
- [ ] `/create agent notion` → Notion agent available in 30s
- [ ] 730+ total tests
