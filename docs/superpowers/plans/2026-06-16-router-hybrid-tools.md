# Router Hybrid Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the unified LLM router (`app/services/unified/llm_router`) the bot's real text capabilities by adding Priority-1 tools (web research, table, image-gen, file Q&A) one at a time, then flip `JARVIS_ROUTER_ENABLED=1` only after all are tested.

**Architecture:** Each capability becomes a `Tool` (injected backend `*_fn`, async handler → `ToolResult`) following the existing `cost_stats.py` pattern. Tools are registered in `register_default_tools` and wired to real backends in `_build_router()`. The router stays **OFF** the entire time; the legacy `classify_message → run_intent` path keeps serving NL. Only the final stage enables the router, after a manual NL-routing checklist passes.

**Tech Stack:** Python 3.11, pytest, Anthropic SDK (Claude `sonnet-4-6` tool_use), existing FastAPI backend reached via the bot's `backend_post`/`backend_get`.

**Hard constraints (from the human):**
- One tool per stage, each its own commit, strict TDD (RED → GREEN → commit).
- `JARVIS_ROUTER_ENABLED=1` ONLY at the very end (Stage 7).
- NEVER `git add -A` — the repo has junk + `.env.backup` with secrets. Stage explicit files only.
- Do NOT touch dead-code dupes (`/cancel`, `/history`, `/lora_status`) or the old Ollama `app/services/llm_router.py` — backlog only (Stage 0).
- Honesty in the prompt: `brain`/`engineer` are ⚠️ (templated plan), `generate_persona_photo` is 🔴 stub — Jarvis must not claim them as fully working.
- Don't break existing tests (`tests/test_tools/*`, watchdog, bot integration).
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Verify HEAD (`git log -1`) right before any commit (parallel sessions commit in this repo).

**Reference facts (from the read-only audit):**
- Tool dataclass + `ToolResult.ok_text/photo/video/fail` + `ToolContext`: `app/services/unified/llm_router/tool_registry.py`.
- Builder pattern to mirror: `app/services/unified/llm_router/tools/cost_stats.py`.
- Test pattern to mirror: `tests/test_tools/test_cost_stats.py`.
- Registration: `app/services/unified/llm_router/tools/__init__.py:28` (`register_default_tools`).
- Backend wiring (singleton build): `tools/jarvis_smart_telegram_control.py:5633` (`_build_router`).
- System prompt: `app/services/unified/llm_router/router.py:33` (`_DEFAULT_SYSTEM_PROMPT`).
- Backend call shapes in `run_intent` (`tools/jarvis_smart_telegram_control.py`): research `:2270`, table `:2199`, image `:2354`.
- Router enable gate: `tools/jarvis_smart_telegram_control.py:5710` (`JARVIS_ROUTER_ENABLED`).

**Backend reality (set honest tool descriptions accordingly):**
- ✅ research (Perplexity), table (Tavily+Perplexity+LLM→XLSX), image (Replicate FLUX) — real, API-key gated.
- ⚠️ brain/engineer — templated plan + one embedded research call. NOT made into tools in this plan; described honestly in the prompt as command-based.
- 🔴 `generate_persona_photo` — registered but backend raises by design.

---

## File Structure

| File | Responsibility | Stages |
|---|---|---|
| `app/services/unified/llm_router/tools/web_research.py` | `web_research` tool builder | 1 |
| `app/services/unified/llm_router/tools/build_table.py` | `build_table` tool builder | 2 |
| `app/services/unified/llm_router/tools/generate_image.py` | `generate_image` tool builder | 3 |
| `app/services/unified/llm_router/tools/answer_about_file.py` | `answer_about_file` tool builder | 4 |
| `app/services/unified/llm_router/tools/__init__.py` | register each new tool (`*_fn` params) | 1–4 |
| `tools/jarvis_smart_telegram_control.py` | `_router_*_backend` adapters + `_build_router` wiring | 1–4 |
| `app/services/unified/llm_router/router.py` | honest `_DEFAULT_SYSTEM_PROMPT` | 5 |
| `tests/test_tools/test_web_research.py` / `test_build_table.py` / `test_generate_image.py` / `test_answer_about_file.py` | per-tool unit tests | 1–4 |
| `tests/test_tools/test_registry_completeness.py` | asserts the P1 tool set is registered | 6 |
| `tests/test_router_prompt.py` | asserts prompt honesty markers | 5 |
| `docs/BACKLOG_ROUTER.md` | deferred cleanup notes | 0 |
| `docs/superpowers/checklists/router-enable-checklist.md` | pre-enable NL smoke checklist | 6 |
| `.env` | `JARVIS_ROUTER_ENABLED=1` | 7 |

Common conventions for every tool stage:
- Injected backend fn is `Optional`; when `None`, the handler returns a graceful `ToolResult.fail(...)` (so the router degrades instead of raising, and unit tests must inject a fake).
- Handlers never raise — they catch and return `ToolResult.fail`.
- Backend dicts use the bot's convention: a failure carries an `_error` key.
- `_maybe_await` supports sync or async injected fns (copy from `cost_stats.py`).

---

## Stage 0: Backlog (docs only, no code)

**Files:**
- Create: `docs/BACKLOG_ROUTER.md`

- [ ] **Step 1: Write the backlog doc**

```markdown
# Router / Bot cleanup backlog (deferred — do NOT do during the hybrid-tools work)

## Dead-code duplicate command handlers (jarvis_smart_telegram_control.py)
- `/cancel`  — line ~4370 wins; `cmd_cancel` at ~4570 is unreachable.
- `/history` — line ~4326 wins; persona `handle_history` at ~4882 is unreachable.
- `/lora_status` — persona branch ~4633 wins; Photo Studio `_ps_cmd_map` version ~4958 is unreachable.
Action (later): remove the shadowed branches; add a regression test that each command resolves to exactly one handler.

## Old Ollama router (app/services/llm_router.py)
- Single-file `route_message` on llama3.2. NOT used by the Telegram bot.
- Importers: app/services/supervisor_core.py, app/services/dashboard_service.py.
Action (later): confirm those two callers, then either delete or move under a clearly-named legacy module. Out of scope for router-hybrid work.

## Priority-2 tools (after enable, separate plan)
- brain_plan (/brain) — ⚠️ templated; engineer_review (/engineer) — ⚠️ templated; create_reminder (/remind).
- Add as tools later if NL routing for them is desired; until then they remain command-only and are described honestly in the system prompt.
```

- [ ] **Step 2: Commit**

```bash
git log -1   # verify HEAD before committing
git add docs/BACKLOG_ROUTER.md
git commit -m "docs: record router/bot cleanup backlog (dead-code dupes, Ollama router, P2 tools)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 1: `web_research` tool

**Files:**
- Create: `app/services/unified/llm_router/tools/web_research.py`
- Create: `tests/test_tools/test_web_research.py`
- Modify: `app/services/unified/llm_router/tools/__init__.py`
- Modify: `tools/jarvis_smart_telegram_control.py` (`_router_research_backend` + `_build_router`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools/test_web_research.py
# -*- coding: utf-8 -*-
"""Unit tests for the web_research router tool (wraps /api/jarvis/tools/internet/research)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.web_research import build_web_research_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_query():
    tool = build_web_research_tool(research_fn=lambda q: {"answer": "x"})
    assert tool.name == "web_research"
    block = tool.to_anthropic()
    assert block["input_schema"]["type"] == "object"
    assert "query" in block["input_schema"]["properties"]
    assert block["input_schema"]["required"] == ["query"]


def test_forwards_query_and_returns_answer():
    seen = []

    def fake(q):
        seen.append(q)
        return {"answer": "FAL дешевле на батчах."}

    tool = build_web_research_tool(research_fn=fake)
    result = asyncio.run(tool.handler({"query": "сравни FAL и Replicate"}, _ctx()))
    assert seen == ["сравни FAL и Replicate"]
    assert not result.is_error
    assert result.text == "FAL дешевле на батчах."


def test_backend_error_becomes_tool_failure():
    tool = build_web_research_tool(research_fn=lambda q: {"_error": "PERPLEXITY_API_KEY is empty"})
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "PERPLEXITY" in result.error


def test_empty_query_fails_without_calling_backend():
    called = []
    tool = build_web_research_tool(research_fn=lambda q: called.append(q) or {"answer": "y"})
    result = asyncio.run(tool.handler({"query": "  "}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades_gracefully():
    tool = build_web_research_tool(research_fn=None)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(q):
        raise RuntimeError("network down")

    tool = build_web_research_tool(research_fn=boom)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "network down" in result.error


def test_async_backend_awaited():
    async def fake(q):
        return {"answer": "async ok"}

    tool = build_web_research_tool(research_fn=fake)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.text == "async ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools/test_web_research.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...tools.web_research'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/unified/llm_router/tools/web_research.py
# -*- coding: utf-8 -*-
"""``web_research`` tool — internet research/analysis (Perplexity), wraps /research.

The backend is injected (``research_fn(query) -> dict`` with an ``answer`` key, or
``_error`` on failure; sync or async). The bot wires it to ``backend_post`` against
``/api/jarvis/tools/internet/research``; unit tests inject a fake.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

ResearchFn = Callable[[str], Any]  # (query) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_web_research_tool(*, research_fn: Optional[ResearchFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult.fail("Пустой запрос для ресёрча.")
        if research_fn is None:
            return ToolResult.fail("Бэкенд ресёрча не подключён.")
        try:
            data = await _maybe_await(research_fn(query))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ресёрч не выполнен: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд ресёрча вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        answer = (data.get("answer") or "").strip()
        return ToolResult.ok_text(answer or "Пустой результат ресёрча.")

    return Tool(
        name="web_research",
        description=(
            "Найти и проанализировать актуальную информацию в интернете "
            "(поиск + анализ через Perplexity) и вернуть текстовый ответ. Когда "
            "использовать: пользователь просит изучить вопрос, найти информацию, "
            "сделать ресёрч, сравнить варианты, узнать что-то актуальное "
            "(например «сделай ресёрч по X», «найди инфу про Y», «сравни A и B»). "
            "Эквивалент /research."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос/вопрос на естественном языке.",
                }
            },
            "required": ["query"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools/test_web_research.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Wire registration**

In `app/services/unified/llm_router/tools/__init__.py`:
- Add import near the others: `from app.services.unified.llm_router.tools.web_research import build_web_research_tool`
- Add parameter to `register_default_tools` signature: `research_fn: Optional[Callable[..., Any]] = None,`
- Add registration line (before the closing `return registry`): `registry.register(build_web_research_tool(research_fn=research_fn))`
- Add `"build_web_research_tool"` to `__all__`.

In `tools/jarvis_smart_telegram_control.py`, add a backend adapter near the other `_router_*_backend` helpers (search for `_router_stats_backend`):

```python
def _router_research_backend(query: str) -> dict:
    """Adapter for the web_research tool — same endpoint as /research."""
    return backend_post("/api/jarvis/tools/internet/research", {"query": query}, timeout=240)
```

In `_build_router()` (`:5659`), add to the `register_default_tools(...)` call:
```python
            research_fn=_router_research_backend,
```

- [ ] **Step 6: Run the full tool suite (no regressions)**

Run: `python -m pytest tests/test_tools/ -q`
Expected: all PASS (existing + 7 new). Also `python -m py_compile tools/jarvis_smart_telegram_control.py` → no error.

- [ ] **Step 7: Commit**

```bash
git log -1   # verify HEAD
git add app/services/unified/llm_router/tools/web_research.py \
        tests/test_tools/test_web_research.py \
        app/services/unified/llm_router/tools/__init__.py \
        tools/jarvis_smart_telegram_control.py
git commit -m "feat(router): add web_research tool (wraps /research, off by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 2: `build_table` tool

**Files:**
- Create: `app/services/unified/llm_router/tools/build_table.py`
- Create: `tests/test_tools/test_build_table.py`
- Modify: `app/services/unified/llm_router/tools/__init__.py`
- Modify: `tools/jarvis_smart_telegram_control.py`

Backend shape (from `run_intent` `:2199`): `backend_post("/api/jarvis/telegram-tools/internet-table", {"query", "max_results":10, "send_to_telegram":True}, timeout=300)` → dict with `rows_count`, `table_path`, `telegram_send:{ok}`, or `_error`. The backend itself delivers the XLSX to Telegram, so the tool only reports the outcome as text.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools/test_build_table.py
# -*- coding: utf-8 -*-
"""Unit tests for the build_table router tool (wraps /telegram-tools/internet-table)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.build_table import build_table_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_query():
    tool = build_table_tool(table_fn=lambda q: {"rows_count": 1, "table_path": "t.xlsx"})
    assert tool.name == "build_table"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["query"]


def test_reports_rows_and_telegram_delivery():
    seen = []

    def fake(q):
        seen.append(q)
        return {"rows_count": 10, "table_path": "C:/x/top_ai.xlsx", "telegram_send": {"ok": True}}

    tool = build_table_tool(table_fn=fake)
    result = asyncio.run(tool.handler({"query": "топ AI сервисов"}, _ctx()))
    assert seen == ["топ AI сервисов"]
    assert not result.is_error
    assert "10" in result.text
    assert "Telegram" in result.text


def test_backend_error_becomes_failure():
    tool = build_table_tool(table_fn=lambda q: {"_error": "Tavily limit"})
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "Tavily" in result.error


def test_empty_query_fails_without_calling_backend():
    called = []
    tool = build_table_tool(table_fn=lambda q: called.append(q) or {})
    result = asyncio.run(tool.handler({"query": ""}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_table_tool(table_fn=None)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(q):
        raise RuntimeError("backend down")

    tool = build_table_tool(table_fn=boom)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "backend down" in result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools/test_build_table.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/unified/llm_router/tools/build_table.py
# -*- coding: utf-8 -*-
"""``build_table`` tool — research-backed comparison table → XLSX, wraps /internet-table.

The backend (Tavily+Perplexity+LLM) builds the file AND sends it to Telegram itself,
so this tool reports the outcome as text. ``table_fn(query) -> dict`` is injected.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

TableFn = Callable[[str], Any]  # (query) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_table_tool(*, table_fn: Optional[TableFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult.fail("Пустой запрос для таблицы.")
        if table_fn is None:
            return ToolResult.fail("Бэкенд таблиц не подключён.")
        try:
            data = await _maybe_await(table_fn(query))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Таблица не построена: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд таблиц вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        rows = data.get("rows_count")
        path = data.get("table_path") or "?"
        sent = (data.get("telegram_send") or {}).get("ok")
        text = f"Таблица готова. Строк: {rows}. Файл: {path}."
        text += " Отправлен в Telegram." if sent else " (файл создан; отправка могла не пройти)."
        return ToolResult.ok_text(text)

    return Tool(
        name="build_table",
        description=(
            "Построить сравнительную таблицу по теме из интернета (поиск Tavily + "
            "анализ Perplexity, результат — файл Excel/XLSX, который отправляется "
            "пользователю в Telegram). Когда использовать: пользователь просит "
            "таблицу, сравнение в виде таблицы, рейтинг/топ в табличном виде "
            "(например «сделай таблицу топ AI сервисов», «таблицу сравнения X и Y»). "
            "Эквивалент /table."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Тема таблицы на естественном языке.",
                }
            },
            "required": ["query"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools/test_build_table.py -v` → PASS.

- [ ] **Step 5: Wire registration**

`tools/__init__.py`: import `build_table_tool`, add `table_fn` param, `registry.register(build_table_tool(table_fn=table_fn))`, add to `__all__`.
`jarvis_smart_telegram_control.py` adapter:
```python
def _router_table_backend(query: str) -> dict:
    """Adapter for the build_table tool — same endpoint as /table."""
    return backend_post(
        "/api/jarvis/telegram-tools/internet-table",
        {"query": query, "max_results": 10, "send_to_telegram": True},
        timeout=300,
    )
```
`_build_router()`: add `table_fn=_router_table_backend,`.

- [ ] **Step 6: Full tool suite**

Run: `python -m pytest tests/test_tools/ -q` → all PASS. `python -m py_compile tools/jarvis_smart_telegram_control.py`.

- [ ] **Step 7: Commit**

```bash
git log -1
git add app/services/unified/llm_router/tools/build_table.py \
        tests/test_tools/test_build_table.py \
        app/services/unified/llm_router/tools/__init__.py \
        tools/jarvis_smart_telegram_control.py
git commit -m "feat(router): add build_table tool (wraps /table, off by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 3: `generate_image` tool

**Files:**
- Create: `app/services/unified/llm_router/tools/generate_image.py`
- Create: `tests/test_tools/test_generate_image.py`
- Modify: `app/services/unified/llm_router/tools/__init__.py`
- Modify: `tools/jarvis_smart_telegram_control.py`

Backend shape (`run_intent` `:2354`): `backend_post("/api/jarvis/image/generate", {"prompt","num_images","aspect_ratio","style"}, timeout=180)` → dict with `urls:[...]`, `provider`, or `_error`. Tool returns the first image as a photo `ToolResult` (one-shot NL). Multi-image stays a `/gen` command feature.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools/test_generate_image.py
# -*- coding: utf-8 -*-
"""Unit tests for the generate_image router tool (wraps /image/generate, Replicate FLUX)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.generate_image import build_generate_image_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_prompt():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"urls": ["u"]})
    assert tool.name == "generate_image"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["prompt"]


def test_returns_photo_with_first_url():
    seen = []

    def fake(prompt, num_images):
        seen.append((prompt, num_images))
        return {"urls": ["https://img/1.png", "https://img/2.png"], "provider": "replicate"}

    tool = build_generate_image_tool(image_fn=fake)
    result = asyncio.run(tool.handler({"prompt": "кот в очках"}, _ctx()))
    assert seen == [("кот в очках", 1)]
    assert result.kind == "photo"
    assert result.media == "https://img/1.png"


def test_num_images_clamped_to_4():
    seen = []
    tool = build_generate_image_tool(image_fn=lambda p, n: seen.append(n) or {"urls": ["u"]})
    asyncio.run(tool.handler({"prompt": "x", "num_images": 99}, _ctx()))
    assert seen == [4]


def test_empty_urls_is_failure():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"urls": []})
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error


def test_backend_error_becomes_failure():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"_error": "REPLICATE_API_KEY missing"})
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error
    assert "REPLICATE" in result.error


def test_empty_prompt_fails_without_backend():
    called = []
    tool = build_generate_image_tool(image_fn=lambda p, n: called.append(p) or {"urls": ["u"]})
    result = asyncio.run(tool.handler({"prompt": "   "}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_generate_image_tool(image_fn=None)
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(p, n):
        raise RuntimeError("provider down")

    tool = build_generate_image_tool(image_fn=boom)
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error
    assert "provider down" in result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools/test_generate_image.py -v` → FAIL (module missing).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/unified/llm_router/tools/generate_image.py
# -*- coding: utf-8 -*-
"""``generate_image`` tool — text-to-image (Replicate FLUX), wraps /image/generate.

Returns the first generated image as a photo ToolResult (one-shot NL). The
backend ``image_fn(prompt, num_images) -> dict`` (with ``urls`` or ``_error``)
is injected; the bot wires it to ``backend_post``.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

ImageFn = Callable[[str, int], Any]  # (prompt, num_images) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_generate_image_tool(*, image_fn: Optional[ImageFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return ToolResult.fail("Пустой prompt для генерации изображения.")
        try:
            num_images = int(params.get("num_images") or 1)
        except (TypeError, ValueError):
            num_images = 1
        num_images = max(1, min(num_images, 4))
        if image_fn is None:
            return ToolResult.fail("Бэкенд генерации изображений не подключён.")
        try:
            data = await _maybe_await(image_fn(prompt, num_images))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Генерация не выполнена: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд генерации вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        urls = data.get("urls") or []
        if not urls:
            return ToolResult.fail("Провайдер вернул пустой список изображений.")
        return ToolResult.photo(urls[0], caption=prompt[:80])

    return Tool(
        name="generate_image",
        description=(
            "Сгенерировать изображение по текстовому описанию (Replicate FLUX). "
            "Когда использовать: пользователь просит создать/нарисовать/"
            "сгенерировать картинку или фото по описанию (например «сгенери "
            "картинку кота в очках», «нарисуй закат над морем»). По умолчанию одно "
            "изображение. Эквивалент /gen."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Описание желаемого изображения.",
                },
                "num_images": {
                    "type": "integer",
                    "description": "Сколько изображений (1–4), по умолчанию 1.",
                },
            },
            "required": ["prompt"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools/test_generate_image.py -v` → PASS.

- [ ] **Step 5: Wire registration**

`tools/__init__.py`: import `build_generate_image_tool`, add `image_fn` param, `registry.register(build_generate_image_tool(image_fn=image_fn))`, add to `__all__`.
`jarvis_smart_telegram_control.py` adapter:
```python
def _router_image_backend(prompt: str, num_images: int) -> dict:
    """Adapter for the generate_image tool — same endpoint as /gen."""
    return backend_post(
        "/api/jarvis/image/generate",
        {"prompt": prompt, "num_images": num_images, "aspect_ratio": "9:16", "style": "realistic"},
        timeout=180,
    )
```
`_build_router()`: add `image_fn=_router_image_backend,`.

- [ ] **Step 6: Full tool suite**

Run: `python -m pytest tests/test_tools/ -q` → all PASS. `python -m py_compile tools/jarvis_smart_telegram_control.py`.

- [ ] **Step 7: Commit**

```bash
git log -1
git add app/services/unified/llm_router/tools/generate_image.py \
        tests/test_tools/test_generate_image.py \
        app/services/unified/llm_router/tools/__init__.py \
        tools/jarvis_smart_telegram_control.py
git commit -m "feat(router): add generate_image tool (wraps /gen, off by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 4: `answer_about_file` tool

**Files:**
- Create: `app/services/unified/llm_router/tools/answer_about_file.py`
- Create: `tests/test_tools/test_answer_about_file.py`
- Modify: `app/services/unified/llm_router/tools/__init__.py`
- Modify: `tools/jarvis_smart_telegram_control.py`

**Contract:** the tool is injected with `file_qa_fn(chat_id: str, question: str) -> dict` returning `{"answer": str}`, `{"_error": str}`, or `{"no_file": True}` when no file is in context. The tool maps `no_file` to a friendly text telling the user to upload a file first (NOT an error — the router should relay it).

> **Execution note (read before Step 3):** the bot-side adapter `_router_file_backend` must reuse the existing file-analysis logic. Before writing it, READ `_handle_file_intent` (`tools/jarvis_smart_telegram_control.py:5197`) and the `state["last_uploaded_file"]` shape (set at `:5143`). The adapter reads `load_state()["last_uploaded_file"]`; if absent → `{"no_file": True}`; else it runs the same Q&A the legacy path uses but RETURNS the text instead of calling `send(...)`. If `_handle_file_intent` can't be cleanly reused read-only, extract a small text-returning helper (e.g. `_file_qa_text(file_info, question) -> str`) and have BOTH the legacy handler and this adapter call it (DRY). Keep that extraction in this commit; do not change legacy behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools/test_answer_about_file.py
# -*- coding: utf-8 -*-
"""Unit tests for the answer_about_file router tool (Q&A over the last uploaded file)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.answer_about_file import build_answer_about_file_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="777")


def test_schema_requires_question():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"answer": "x"})
    assert tool.name == "answer_about_file"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["question"]


def test_forwards_chat_and_question_and_returns_answer():
    seen = []

    def fake(chat_id, question):
        seen.append((chat_id, question))
        return {"answer": "В договоре сумма 50000 руб."}

    tool = build_answer_about_file_tool(file_qa_fn=fake)
    result = asyncio.run(tool.handler({"question": "какая сумма?"}, _ctx()))
    assert seen == [("777", "какая сумма?")]
    assert not result.is_error
    assert "50000" in result.text


def test_no_file_is_friendly_text_not_error():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"no_file": True})
    result = asyncio.run(tool.handler({"question": "что тут?"}, _ctx()))
    assert not result.is_error
    assert "файл" in result.text.lower()


def test_backend_error_becomes_failure():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"_error": "parse failed"})
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error
    assert "parse failed" in result.error


def test_empty_question_fails_without_backend():
    called = []
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: called.append(q) or {"answer": "y"})
    result = asyncio.run(tool.handler({"question": ""}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_answer_about_file_tool(file_qa_fn=None)
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(c, q):
        raise RuntimeError("reader crash")

    tool = build_answer_about_file_tool(file_qa_fn=boom)
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error
    assert "reader crash" in result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools/test_answer_about_file.py -v` → FAIL (module missing).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/unified/llm_router/tools/answer_about_file.py
# -*- coding: utf-8 -*-
"""``answer_about_file`` tool — Q&A over the user's last uploaded file (PDF/DOCX/XLSX).

The bot keeps the most recently uploaded file in per-chat state; ``file_qa_fn(chat_id,
question) -> dict`` (with ``answer`` / ``_error`` / ``no_file``) is injected and reuses
the existing file-analysis path.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

FileQaFn = Callable[[str, str], Any]  # (chat_id, question) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_answer_about_file_tool(*, file_qa_fn: Optional[FileQaFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        question = (params.get("question") or "").strip()
        if not question:
            return ToolResult.fail("Пустой вопрос по файлу.")
        if file_qa_fn is None:
            return ToolResult.fail("Бэкенд анализа файлов не подключён.")
        try:
            data = await _maybe_await(file_qa_fn(context.chat_id, question))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Не удалось обработать файл: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд анализа файлов вернул неожиданный ответ.")
        if data.get("no_file"):
            return ToolResult.ok_text(
                "Сначала пришли файл (PDF/DOCX/XLSX), потом задай по нему вопрос."
            )
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        answer = (data.get("answer") or "").strip()
        return ToolResult.ok_text(answer or "По файлу ничего не нашлось.")

    return Tool(
        name="answer_about_file",
        description=(
            "Ответить на вопрос по последнему загруженному пользователем файлу "
            "(PDF/DOCX/XLSX): суммировать, извлечь данные (суммы, даты, "
            "контрагентов), ответить на вопрос по содержимому. Когда использовать: "
            "пользователь спрашивает про присланный файл (например «что в файле», "
            "«суммируй документ», «какая сумма в договоре»). Если файла нет, "
            "инструмент попросит сначала прислать файл."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Вопрос/инструкция по содержимому файла.",
                }
            },
            "required": ["question"],
        },
        handler=handler,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools/test_answer_about_file.py -v` → PASS.

- [ ] **Step 5: Wire registration + bot adapter (see Execution note above)**

`tools/__init__.py`: import `build_answer_about_file_tool`, add `file_qa_fn` param, `registry.register(build_answer_about_file_tool(file_qa_fn=file_qa_fn))`, add to `__all__`.
`jarvis_smart_telegram_control.py`: implement `_router_file_backend(chat_id, question) -> dict` reusing the file-analysis logic (return `{"no_file": True}` when `state["last_uploaded_file"]` is absent, else `{"answer": ...}`/`{"_error": ...}`). `_build_router()`: add `file_qa_fn=_router_file_backend,`.

- [ ] **Step 6: Full suite (incl. any legacy file tests) + compile**

Run: `python -m pytest tests/test_tools/ -q` and `python -m pytest -k "file" -q` → PASS. `python -m py_compile tools/jarvis_smart_telegram_control.py`.

- [ ] **Step 7: Commit**

```bash
git log -1
git add app/services/unified/llm_router/tools/answer_about_file.py \
        tests/test_tools/test_answer_about_file.py \
        app/services/unified/llm_router/tools/__init__.py \
        tools/jarvis_smart_telegram_control.py
git commit -m "feat(router): add answer_about_file tool (file Q&A, off by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 5: Honest system prompt

**Files:**
- Create: `tests/test_router_prompt.py`
- Modify: `app/services/unified/llm_router/router.py` (`_DEFAULT_SYSTEM_PROMPT`, `:33`)

Goal: Claude must know the 4 new tools, and must NOT over-claim. Add the new capabilities to the "ЧТО ТЫ РЕАЛЬНО УМЕЕШЬ" section; add a ⚠️ note that deep analysis/engineering (`/brain`, `/engineer`) is command-based with a templated plan; keep `generate_persona_photo` in the 🔴 stub section; add a short "направляй на команды" note for stateful flows (persona/LoRA/swapbatch/photo studio).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router_prompt.py
# -*- coding: utf-8 -*-
"""The router system prompt must advertise the P1 tools and stay honest about stubs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import _DEFAULT_SYSTEM_PROMPT as P


def test_prompt_mentions_new_p1_tools():
    for name in ("web_research", "build_table", "generate_image", "answer_about_file"):
        assert name in P, f"prompt missing tool {name}"


def test_prompt_is_honest_about_templated_brain_engineer():
    # must flag that deep analysis / engineering is command-based & templated
    assert "шаблон" in P.lower() or "/engineer" in P
    assert "/brain" in P


def test_prompt_keeps_persona_photo_as_stub():
    assert "generate_persona_photo" in P
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router_prompt.py -v`
Expected: FAIL on `test_prompt_mentions_new_p1_tools` (names absent).

- [ ] **Step 3: Edit the prompt (minimal)**

In `app/services/unified/llm_router/router.py`, inside `_DEFAULT_SYSTEM_PROMPT`, extend the "ЧТО ТЫ РЕАЛЬНО УМЕЕШЬ" list with these entries (keep existing swap/voice/stats items):

```
5. Интернет-ресёрч (web_research): найти и проанализировать актуальную информацию, сравнить варианты, ответить по свежим данным. Используй на запросы «найди», «изучи», «сделай ресёрч», «сравни».
6. Сравнительная таблица (build_table): построить таблицу-сравнение/рейтинг по теме из интернета и прислать файлом XLSX. Используй на «сделай таблицу», «таблицу топ …», «сравни … таблицей».
7. Генерация изображения (generate_image): создать картинку по текстовому описанию. Используй на «сгенери картинку», «нарисуй …». По умолчанию одна картинка.
8. Вопросы по загруженному файлу (answer_about_file): суммировать/извлечь данные/ответить по последнему присланному файлу (PDF/DOCX/XLSX). Если файла ещё нет — попроси прислать.
```

Add (or extend) the honesty section so it includes:

```
ЧЕСТНОСТЬ ПРО ВОЗМОЖНОСТИ:
- Глубокий анализ/планирование (/brain) и инженерный разбор (/engineer) пока работают ТОЛЬКО как команды и дают ШАБЛОННЫЙ план + краткий веб-ресёрч — не выдавай это за полноценный автономный анализ. Если пользователь просит именно это, предложи команду /brain или /engineer.
- generate_persona_photo (фото AI-персон по LoRA) — заглушка, бэкенд не подключён, не предлагай как рабочее.
- Многошаговые сценарии (персоны, обучение LoRA, пакетный swap, Photo Studio) запускаются командами — направляй пользователя на нужную команду, не пытайся выполнить их инструментом.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_router_prompt.py -v` → PASS.
Also: `python -m pytest tests/test_tools/ tests/test_router_prompt.py -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git log -1
git add app/services/unified/llm_router/router.py tests/test_router_prompt.py
git commit -m "feat(router): teach prompt the P1 tools; keep brain/engineer/persona honest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 6: Pre-enable checklist + registry completeness guard

**Files:**
- Create: `tests/test_tools/test_registry_completeness.py`
- Create: `docs/superpowers/checklists/router-enable-checklist.md`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools/test_registry_completeness.py
# -*- coding: utf-8 -*-
"""Guard: register_default_tools wires the full P1 tool set with valid schemas."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_default_tools(
        reg,
        research_fn=lambda q: {"answer": "x"},
        table_fn=lambda q: {"rows_count": 1, "table_path": "t"},
        image_fn=lambda p, n: {"urls": ["u"]},
        file_qa_fn=lambda c, q: {"answer": "x"},
    )
    return reg


def test_p1_tools_registered():
    names = _registry().names()
    for expected in ("web_research", "build_table", "generate_image", "answer_about_file"):
        assert expected in names


def test_all_tools_have_valid_anthropic_schema():
    for tool in _registry().all():
        block = tool.to_anthropic()
        assert block["name"]
        assert block["description"]
        assert block["input_schema"]["type"] == "object"
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `python -m pytest tests/test_tools/test_registry_completeness.py -v`
Expected: PASS if Stages 1–4 are complete (this test is the completeness *guard*; if any P1 tool/param is missing it FAILS — run it to confirm it currently passes given Stages 1–4 landed).

- [ ] **Step 3: Write the enable checklist doc**

```markdown
# Router enable checklist (run BEFORE setting JARVIS_ROUTER_ENABLED=1)

Prereqs: backend up (uvicorn :8010), ANTHROPIC_API_KEY set, API keys present
(PERPLEXITY_API_KEY, TAVILY_API_KEY, REPLICATE_API_KEY).

## Automated
- [ ] `python -m pytest tests/test_tools/ tests/test_router_prompt.py -q` → all green.
- [ ] `python -m py_compile tools/jarvis_smart_telegram_control.py` → ok.

## Live NL smoke (temporary: set JARVIS_ROUTER_ENABLED=1 in a TEST shell only)
Send each message to the bot and confirm the RIGHT tool fires (watch jarvis_bot.log
for the tool name) and the answer is real:
- [ ] "сделай ресёрч по ценам на GPU аренду" → web_research → text answer.
- [ ] "сделай таблицу топ 5 AI видеогенераторов" → build_table → XLSX delivered.
- [ ] "сгенери картинку: кот-астронавт" → generate_image → photo.
- [ ] upload a PDF, then "что в этом файле?" → answer_about_file → summary.
- [ ] "сколько я потратил?" → get_user_stats.
- [ ] face-swap flow still works (swap_batch_* tools) + a готовое-видео swap.
- [ ] voice note in → transcribed; "ответь голосом" → reply_with_voice.
- [ ] negative: "привет, как дела" → plain text, NO tool, no hallucinated capability.
- [ ] honesty: "сделай глубокий инженерный анализ X" → offers /engineer, does NOT claim a full autonomous analysis.
- [ ] fallback: stop the backend, send "сделай ресёрч …" → graceful error, bot still alive.

## Rollback
- Set JARVIS_ROUTER_ENABLED=0 (or remove it) and restart the bot → legacy path resumes.
```

- [ ] **Step 4: Commit**

```bash
git log -1
git add tests/test_tools/test_registry_completeness.py \
        docs/superpowers/checklists/router-enable-checklist.md
git commit -m "test(router): P1 registry completeness guard + enable checklist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Stage 7: Enable the router (final, reversible)

**Files:**
- Modify: `.env` (single line — config, not code)

**Pre-req:** Stage 6 checklist fully green (automated + live smoke). Do NOT proceed otherwise.

- [ ] **Step 1: Run the full checklist live** (Stage 6 doc) in a test shell with `JARVIS_ROUTER_ENABLED=1` exported temporarily. Fix any mis-routing by tuning tool descriptions / prompt (each fix = its own TDD commit in the relevant stage) — do NOT hand-edit committed tool code without a test.

- [ ] **Step 2: Persist the flag**

Append to `.env` (do NOT `git add .env` — it is git-ignored and holds secrets):
```
JARVIS_ROUTER_ENABLED=1
```

- [ ] **Step 3: Restart bot + backend** and re-run the live smoke top-to-bottom once more on the real process.

- [ ] **Step 4: No commit** — `.env` is not tracked. Record the go-live in the session notes / memory instead. If anything regresses, set `JARVIS_ROUTER_ENABLED=0` and restart (instant rollback to legacy).

---

## Self-Review notes
- **Spec coverage:** one tool per stage ✅ (1–4); enable only at end ✅ (7); pre-enable checklist ✅ (6); honesty in prompt ✅ (5); dead-code/Ollama backlog-only ✅ (0); no `git add -A` ✅ (explicit `git add` per commit); don't break existing tests ✅ (Step 6 full-suite gate each stage).
- **Type consistency:** every tool builder is `build_<x>_tool(*, <x>_fn=None)`; handler signature `async (params, context) -> ToolResult`; backend dicts use `_error`; `register_default_tools` gains one `*_fn` param per stage; `_build_router` gains one matching kwarg per stage. Names used in tests match the builders.
- **Known caveat:** Stage 4's bot-side adapter (`_router_file_backend`) requires reading `_handle_file_intent` at execution time; the tool + its tests are fully concrete, the adapter is specified by contract (Execution note) to avoid fabricating internals not yet read.
- **Out of scope (backlog):** brain/engineer/reminder tools, dead-code dupes, Ollama `llm_router.py`.
```
