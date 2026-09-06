"""Phase 16: Smart Router — analyzes tasks, selects agents, builds and executes plans.

Flow:
  analyze_task(query) → requirements dict
  select_agents(requirements) → ordered list of agent IDs
  build_execution_plan(query, agents) → ExecutionPlan
  execute_plan(plan, send_progress) → final result dict
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.agent_registry import AGENTS, get_agent, list_agents_by_capability
from app.services.internal_api_client import backend_headers


class MeshExecutionError(RuntimeError):
    """Raised by ``execute_plan`` when EVERY agent step failed at the transport
    layer (e.g. all 401 under the enforce auth middleware, or the backend is
    down). Making total failure loud lets ``_handle_mesh_task`` fall back to the
    standard planner instead of silently synthesising a "done" plan out of
    all-errored steps."""

# ---------------------------------------------------------------------------
# Compound-task detection patterns
# ---------------------------------------------------------------------------

_COMPOUND_CONJUNCTIONS = [
    " и ", " а потом ", " затем ", " после этого ", " и ещё ", " и еще ",
    " плюс ", " также ", " а также ", " потом ",
    " and then ", " and also ", " plus ", " as well as ",
]

_COMPOUND_MULTI_VERBS = [
    ("найди", "сделай"), ("найди", "создай"), ("найди", "оформи"),
    ("поищи", "составь"), ("исследуй", "оформи"), ("researched", "create"),
    ("найди", "построй"), ("собери", "оформи"),
]


def is_compound_task(query: str) -> bool:
    """Return True if the query contains multiple different actions (compound)."""
    q = query.lower()
    for conj in _COMPOUND_CONJUNCTIONS:
        if conj in q:
            # Check that parts before/after conjunction are semantically different
            parts = q.split(conj, 1)
            if len(parts) == 2 and len(parts[0].strip()) > 3 and len(parts[1].strip()) > 3:
                return True
    # Check multi-verb patterns
    for v1, v2 in _COMPOUND_MULTI_VERBS:
        if v1 in q and v2 in q:
            return True
    return False


# ---------------------------------------------------------------------------
# Task analysis
# ---------------------------------------------------------------------------

_CAPABILITY_KEYWORDS: Dict[str, List[str]] = {
    "web_search":            ["найди", "поищи", "поиск", "из интернета", "search", "find"],
    "research":              ["исследуй", "расскажи про", "что такое", "кто такой", "research"],
    "excel":                 ["таблиц", "excel", "xlsx", "csv", "сравни", "оформи в таблицу"],
    "comparison_tables":     ["сравни", "сравнительн", "compare"],
    "code_generation":       ["код", "написать", "скрипт", "функцию", "code", "script"],
    "architecture_design":   ["архитектур", "дизайн", "architecture", "design"],
    "summarization":         ["суммируй", "кратко", "резюме", "summarize"],
    "image_generation":      ["изображени", "картинк", "image", "photo", "нарисуй"],
    "video_generation":      ["видео", "video", "снять"],
    "file_analysis":         ["файл", "document", "pdf", "docx", "xlsx анализ"],
    "workflow":              ["n8n", "автоматизац", "workflow", "триггер"],
    "note_creation":         ["заметк", "obsidian", "запиши", "note"],
}


def analyze_task(query: str) -> Dict[str, Any]:
    """Analyze a query and return capability requirements + complexity."""
    q = query.lower()
    required: List[str] = []

    for cap, keywords in _CAPABILITY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            required.append(cap)

    # Fallback: if nothing matched, default to research
    if not required:
        required = ["research"]

    compound = is_compound_task(query)
    complexity = "compound" if compound or len(required) >= 3 else (
        "complex" if len(required) == 2 else "simple"
    )

    expected_outputs = []
    if "excel" in required or "comparison_tables" in required:
        expected_outputs.append("table_xlsx")
    if "image_generation" in required:
        expected_outputs.append("image_file")
    if "video_generation" in required:
        expected_outputs.append("video_file")
    if not expected_outputs:
        expected_outputs.append("text")

    return {
        "required_capabilities": required,
        "complexity": complexity,
        "expected_outputs": expected_outputs,
        "is_compound": compound,
        "original_query": query,
    }


# ---------------------------------------------------------------------------
# Agent selection
# ---------------------------------------------------------------------------

_CAPABILITY_PRIORITY: Dict[str, List[str]] = {
    "web_search":          ["internet_research", "perplexity_researcher"],
    "research":            ["perplexity_researcher", "internet_research"],
    "excel":               ["smart_table"],
    "comparison_tables":   ["smart_table"],
    "code_generation":     ["claude_coder"],
    "architecture_design": ["claude_coder", "ai_engineer"],
    "summarization":       ["openai_reasoner", "claude_coder"],
    "image_generation":    ["image_generator"],
    "video_generation":    ["video_generator"],
    "file_analysis":       ["file_processor"],
    "workflow":            ["n8n_workflow"],
    "note_creation":       ["obsidian_writer"],
}


def select_agents(requirements: Dict[str, Any]) -> List[str]:
    """Select ordered list of agents for the given requirements. Deduplicates."""
    caps = requirements.get("required_capabilities", ["research"])
    seen: set = set()
    ordered: List[str] = []
    for cap in caps:
        candidates = _CAPABILITY_PRIORITY.get(cap) or list_agents_by_capability(cap)
        for cid in candidates:
            cfg = get_agent(cid)
            if cfg and cfg.get("available") and cid not in seen:
                seen.add(cid)
                ordered.append(cid)
                break  # one agent per capability
    return ordered or ["internet_research"]


# ---------------------------------------------------------------------------
# Execution plan
# ---------------------------------------------------------------------------

@dataclass
class ExecutionStep:
    step_num: int
    agent_id: str
    input_query: str
    result: Optional[Dict[str, Any]] = None
    status: str = "pending"  # pending | running | done | error
    duration_sec: float = 0.0


@dataclass
class ExecutionPlan:
    query: str
    steps: List[ExecutionStep] = field(default_factory=list)
    final_result: Optional[str] = None
    status: str = "pending"

    def summary(self) -> str:
        lines = [f"📋 План ({len(self.steps)} шаг(а)):"]
        for s in self.steps:
            cfg = get_agent(s.agent_id) or {}
            label = cfg.get("label", s.agent_id)
            lines.append(f"  [{s.step_num}] {label}")
        return "\n".join(lines)


def build_execution_plan(query: str, agents: List[str]) -> ExecutionPlan:
    """Build an ordered execution plan from agent list."""
    plan = ExecutionPlan(query=query)
    for i, aid in enumerate(agents, 1):
        plan.steps.append(ExecutionStep(
            step_num=i,
            agent_id=aid,
            input_query=query,
        ))
    return plan


# ---------------------------------------------------------------------------
# Plan execution (stub — calls backend endpoints via HTTP)
# ---------------------------------------------------------------------------

def _call_agent(agent_id: str, query: str) -> Dict[str, Any]:
    """Call an agent and return its result dict."""
    import urllib.request
    import urllib.error

    cfg = get_agent(agent_id)
    if not cfg:
        return {"_error": f"Unknown agent: {agent_id}"}

    base = os.environ.get("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
    endpoint = cfg.get("endpoint")

    if not endpoint:
        # cowork_file_agent: try real Cowork bridge if watcher is active
        if agent_id == "cowork_file_agent":
            try:
                from app.services.cowork_watcher import get_watcher
                from app.services.cowork_bridge import submit_and_wait
                watcher = get_watcher()
                if watcher and watcher.is_active():
                    result = submit_and_wait(query, timeout_sec=300)
                    if result:
                        return {"answer": result.get("result", str(result))}
                    return {"answer": "⚠️ Cowork не ответил за 300с. Попробуй позже."}
            except Exception as e:
                pass
            # Fallback to file_processor
            fallback_cfg = get_agent("file_processor")
            if fallback_cfg and fallback_cfg.get("endpoint"):
                url = base + fallback_cfg["endpoint"]
                payload = json.dumps({"query": query}).encode()
                req = urllib.request.Request(url, data=payload, headers=backend_headers(url, {"Content-Type": "application/json"}), method="POST")
                try:
                    resp = urllib.request.urlopen(req, timeout=120)
                    return json.loads(resp.read())
                except Exception as e2:
                    return {"_error": str(e2)}
        # n8n_workflow: trigger via n8n_integration
        if agent_id == "n8n_workflow":
            try:
                from app.services.n8n_integration import trigger_workflow
                result = trigger_workflow(query, {"query": query})
                exec_id = result.get("execution_id") or result.get("id", "?")
                return {"answer": f"✅ n8n workflow запущен. Execution ID: {exec_id}", "execution_id": exec_id}
            except Exception as e_n8n:
                return {"_error": f"n8n trigger failed: {e_n8n}"}
        # Агенты без HTTP-эндпоинта, но с адаптером: транспорт есть, он просто
        # не HTTP. Отказ адаптера — это `_error`, а не заглушка: он ОТВЕТИЛ.
        adapter_name = cfg.get("adapter")
        if adapter_name:
            try:
                from app.services.agent_adapters import invoke_adapter
                res = invoke_adapter(adapter_name, {"prompt": query, "message": query})
            except Exception as exc:
                return {"_error": f"{adapter_name}: {exc}"}
            if not res.get("ok"):
                return {"_error": f"{adapter_name}: {res.get('error') or 'адаптер вернул отказ без причины'}"}
            text = (res.get("output") or {}).get("text") or ""
            if not text.strip():
                return {"_error": f"{adapter_name}: пустой ответ"}
            return {"answer": text, "adapter": adapter_name, "raw": res}

        # Non-HTTP agents return stub
        return {"answer": f"[{agent_id}] не имеет HTTP endpoint — требует прямого вызова", "_stub": True}

    # n8n_workflow: always route through n8n_integration regardless of endpoint config
    if agent_id == "n8n_workflow":
        try:
            from app.services.n8n_integration import trigger_workflow
            result = trigger_workflow(query, {"query": query})
            exec_id = result.get("execution_id") or result.get("id", "?")
            return {"answer": f"✅ n8n workflow запущен. Execution ID: {exec_id}", "execution_id": exec_id}
        except Exception as e_n8n:
            return {"_error": f"n8n trigger failed: {e_n8n}"}

    url = base + endpoint
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(url, data=payload, headers=backend_headers(url, {"Content-Type": "application/json"}), method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)}


def step_failed(result: Dict[str, Any]) -> bool:
    """True, если шаг НЕ сделал работы.

    Два случая, а не один. `_error` — транспорт ответил отказом. `_stub` —
    транспорта нет вовсе: `_call_agent` вернул строку «не имеет HTTP endpoint».
    Второй случай раньше считался успехом, потому что `_error` в нём пуст, и
    план рапортовал «✅ готово (0.0s)» о шаге, которого не было.
    """
    return bool(result.get("_error")) or bool(result.get("_stub"))


def execute_plan(
    plan: ExecutionPlan,
    send_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Execute all steps in the plan, collect results."""
    def _notify(msg: str) -> None:
        if send_progress:
            send_progress(msg)

    plan.status = "running"
    all_results: List[Dict[str, Any]] = []
    any_ok = False
    errors: List[str] = []

    for step in plan.steps:
        cfg = get_agent(step.agent_id) or {}
        label = cfg.get("label", step.agent_id)
        _notify(f"⏳ [{step.step_num}/{len(plan.steps)}] {label}...")
        step.status = "running"
        t0 = time.time()
        result = _call_agent(step.agent_id, step.input_query)
        step.duration_sec = round(time.time() - t0, 1)
        step.result = result
        if step_failed(result):
            step.status = "error"
            reason = result.get("_error") or (
                f"нет транспорта: агент {step.agent_id} объявлен доступным, "
                f"но вызвать его нечем"
            )
            errors.append(f"{label}: {reason}")
            _notify(f"❌ [{step.step_num}/{len(plan.steps)}] {label}: {reason}")
        else:
            step.status = "done"
            any_ok = True
            _notify(f"✅ [{step.step_num}/{len(plan.steps)}] {label}: готово ({step.duration_sec}s)")
        all_results.append({"agent": step.agent_id, "result": result})

    # Loud failure: if the plan had steps but NONE succeeded, every agent call
    # failed at the transport layer (all 401 under enforce, or backend down).
    # Raise so the caller falls back to the standard planner instead of
    # synthesising a bogus "готово" answer out of pure errors.
    if plan.steps and not any_ok:
        plan.status = "error"
        raise MeshExecutionError(
            "All agent steps failed: " + "; ".join(errors)
        )

    plan.final_result = synthesize_results(plan.query, all_results)
    plan.status = "done"
    return {"plan_result": plan.final_result, "steps": all_results}


# ---------------------------------------------------------------------------
# Result synthesis
# ---------------------------------------------------------------------------

# Ключи, под которыми агенты отдают сделанное. Списки ЛИТЕРАЛЬНЫЕ и ЗАМЕРЕНЫ
# живыми вызовами 06.09.2026, а не взяты из `output_format` каталога: каталог
# для smart_table обещает `file_path`/`drive_url`, а боевой ответ несёт
# `xlsx_path`, `csv_path`, `table_path`, `json_path`. Список пополняется только
# вместе с замером нового агента.
_TEXT_KEYS = ("answer", "plan", "text")
_ARTIFACT_KEYS = (
    "xlsx_path",
    "csv_path",
    "table_path",
    "json_path",
    "artifact_path",
    "file_path",
    "path",
    "drive_url",
)


def describe_result(result: Optional[Dict[str, Any]]) -> str:
    """Человекочитаемое описание результата шага.

    Текст — как есть. Текста нет, но есть артефакт — назвать файл и объём:
    «работа сделана» без имени файла человек проверить не может, а именно это
    и выдавал синтез раньше («Результат получен.», 18 символов, DEV-101).
    """
    result = result or {}

    for key in _TEXT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value

    paths: List[str] = []
    for key in _ARTIFACT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip() and value not in paths:
            paths.append(value)

    if not paths:
        return "Результат получен."

    parts = [f"Готово: {paths[0]}"]
    rows = result.get("rows_count")
    if isinstance(rows, int):
        parts.append(f"строк: {rows}")
    columns = result.get("columns")
    if isinstance(columns, (list, tuple)):
        parts.append(f"колонок: {len(columns)}")
    if len(paths) > 1:
        parts.append(f"ещё файлов: {len(paths) - 1}")
    return " — ".join(parts[:1]) + (", " + ", ".join(parts[1:]) if len(parts) > 1 else "")


def synthesize_results(query: str, agent_results: List[Dict[str, Any]]) -> str:
    """Combine multiple agent results into a coherent final answer via LLM."""
    if not agent_results:
        return "Нет результатов для синтеза."

    if len(agent_results) == 1:
        return describe_result(agent_results[0].get("result", {}))

    # Build context block
    parts = []
    for i, ar in enumerate(agent_results, 1):
        agent_id = ar.get("agent", f"agent_{i}")
        result = ar.get("result", {})
        content = describe_result(result)
        if content == "Результат получен." and result:
            # Ни текста, ни известного артефакта — отдаём сырое, чтобы синтез
            # видел хоть что-то, а не общую фразу.
            content = str(result)[:300]
        cfg = get_agent(agent_id) or {}
        label = cfg.get("label", agent_id)
        parts.append(f"Агент {i} ({label}):\n{content}")

    combined = "\n\n".join(parts)

    # Try LLM synthesis
    try:
        payload = json.dumps({
            "query": (
                f"Пользователь спросил: {query}\n\n"
                f"Результаты агентов:\n{combined}\n\n"
                "Сделай единый связный ответ на русском языке. "
                "Сохрани ключевые факты и структуру. Максимум 500 слов."
            )
        }).encode()
        import urllib.request
        base = os.environ.get("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
        _url = base + "/api/jarvis/tools/internet/research"
        req = urllib.request.Request(
            _url,
            data=payload,
            headers=backend_headers(_url, {"Content-Type": "application/json"}),
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        if data.get("answer"):
            return data["answer"]
    except Exception:
        pass

    # Fallback: concatenate with headers
    lines = [f"📊 Результаты по запросу: {query}\n"]
    for ar in agent_results:
        agent_id = ar.get("agent", "?")
        result = ar.get("result", {})
        cfg = get_agent(agent_id) or {}
        label = cfg.get("label", agent_id)
        content = describe_result(result)
        lines.append(f"**{label}:**\n{content[:400]}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def route_and_execute(
    query: str,
    send_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Full pipeline: analyze → select → plan → execute → synthesize."""
    def _notify(msg: str) -> None:
        if send_progress:
            send_progress(msg)

    _notify("🧠 Анализирую задачу...")
    requirements = analyze_task(query)
    agents = select_agents(requirements)
    plan = build_execution_plan(query, agents)
    _notify(plan.summary())
    _notify("🚀 Выполняю...")
    result = execute_plan(plan, send_progress)
    return result
