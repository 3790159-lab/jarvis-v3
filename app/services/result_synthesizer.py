"""Phase 18: Result Synthesizer — combines outputs from multiple agents into a coherent answer.

Smart formatting:
- Single result → pass through
- Multiple results → LLM synthesis or structured fallback
- Excel/file results → labeled with attachment hint
- References/sources → preserved as footnotes
- Unrelated results → honest "agents returned unrelated data"
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from app.services.agent_registry import get_agent


# ---------------------------------------------------------------------------
# Content extraction from agent results
# ---------------------------------------------------------------------------

def _extract_content(result: Dict[str, Any]) -> str:
    """Extract human-readable content from any agent result dict."""
    if result.get("_error"):
        return f"[ошибка: {result['_error']}]"
    for key in ("answer", "plan", "text", "result", "summary", "content"):
        val = result.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()[:2000]
    return str(result)[:500]


def _has_file_output(result: Dict[str, Any]) -> bool:
    return bool(result.get("file_path") or result.get("drive_url") or result.get("job_id"))


def _extract_sources(result: Dict[str, Any]) -> List[str]:
    sources = result.get("sources") or result.get("citations") or []
    return [str(s) for s in sources[:5]]


def _results_are_related(contents: List[str]) -> bool:
    """Simple heuristic: if results share at least 2 common non-trivial words."""
    if len(contents) < 2:
        return True
    words_sets = []
    for c in contents:
        words = {w.lower() for w in c.split() if len(w) > 4}
        words_sets.append(words)
    for i in range(len(words_sets)):
        for j in range(i + 1, len(words_sets)):
            if len(words_sets[i] & words_sets[j]) >= 2:
                return True
    return False


# ---------------------------------------------------------------------------
# LLM synthesis (via backend research endpoint)
# ---------------------------------------------------------------------------

def _llm_synthesize(query: str, context: str) -> Optional[str]:
    """Call LLM via backend to synthesize context into a final answer."""
    try:
        import urllib.request
        base = os.environ.get("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
        prompt = (
            f"Пользователь спросил: {query}\n\n"
            f"Результаты от разных агентов:\n{context}\n\n"
            "Напиши единый связный ответ на русском языке. "
            "Сохрани ключевые факты, укажи источники если есть. "
            "Максимум 400 слов."
        )
        payload = json.dumps({"query": prompt}).encode()
        req = urllib.request.Request(
            base + "/api/jarvis/tools/internet/research",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        return data.get("answer") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main synthesize function
# ---------------------------------------------------------------------------

def synthesize_results(
    query: str,
    agent_results: List[Dict[str, Any]],
    try_llm: bool = True,
) -> str:
    """
    Combine multiple agent results into a single coherent response.

    Args:
        query: Original user query.
        agent_results: List of {"agent": agent_id, "result": result_dict}.
        try_llm: Whether to try LLM synthesis (may hit backend).

    Returns:
        Formatted string response for Telegram.
    """
    if not agent_results:
        return "❌ Нет результатов для синтеза."

    # Single result — pass through with label
    if len(agent_results) == 1:
        ar = agent_results[0]
        agent_id = ar.get("agent", "?")
        result = ar.get("result", {})
        cfg = get_agent(agent_id) or {}
        label = cfg.get("label", agent_id)
        content = _extract_content(result)
        sources = _extract_sources(result)

        if _has_file_output(result):
            file_hint = result.get("drive_url") or result.get("file_path") or f"job {result.get('job_id')}"
            return f"📎 {label} → {file_hint}\n\n{content}"

        footer = ""
        if sources:
            footer = "\n\n📚 Источники:\n" + "\n".join(f"• {s}" for s in sources)
        return content + footer

    # Multiple results
    contents = [_extract_content(ar.get("result", {})) for ar in agent_results]
    all_sources: List[str] = []
    file_outputs: List[str] = []

    for ar in agent_results:
        result = ar.get("result", {})
        all_sources.extend(_extract_sources(result))
        if _has_file_output(result):
            hint = result.get("drive_url") or result.get("file_path") or f"job {result.get('job_id')}"
            cfg = get_agent(ar.get("agent", "")) or {}
            file_outputs.append(f"📎 {cfg.get('label', ar.get('agent', '?'))}: {hint}")

    # Check relatedness
    if not _results_are_related(contents):
        lines = ["⚠️ Агенты вернули несвязанные результаты:\n"]
        for i, ar in enumerate(agent_results, 1):
            cfg = get_agent(ar.get("agent", "")) or {}
            label = cfg.get("label", ar.get("agent", f"агент {i}"))
            lines.append(f"**{label}:**\n{contents[i-1][:300]}\n")
        return "\n".join(lines)

    # Try LLM synthesis
    if try_llm:
        context_parts = []
        for i, ar in enumerate(agent_results, 1):
            cfg = get_agent(ar.get("agent", "")) or {}
            label = cfg.get("label", ar.get("agent", f"агент {i}"))
            context_parts.append(f"{label}:\n{contents[i-1][:600]}")
        context = "\n\n".join(context_parts)
        synthesized = _llm_synthesize(query, context)
        if synthesized:
            result_text = f"✨ {synthesized}"
            if file_outputs:
                result_text += "\n\n" + "\n".join(file_outputs)
            if all_sources:
                result_text += "\n\n📚 Источники:\n" + "\n".join(f"• {s}" for s in all_sources[:5])
            return result_text

    # Fallback: structured concatenation
    lines = [f"📊 Результаты по запросу: {query[:100]}\n"]
    for i, ar in enumerate(agent_results, 1):
        cfg = get_agent(ar.get("agent", "")) or {}
        label = cfg.get("label", ar.get("agent", f"агент {i}"))
        lines.append(f"**{label}:**")
        lines.append(contents[i-1][:400])
        lines.append("")

    if file_outputs:
        lines.append("📎 Файлы:")
        lines.extend(file_outputs)
        lines.append("")
    if all_sources:
        lines.append("📚 Источники:")
        lines.extend(f"• {s}" for s in all_sources[:5])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smart formatting helpers
# ---------------------------------------------------------------------------

def format_for_telegram(content: str, max_length: int = 4000) -> str:
    """Ensure content fits Telegram message limit."""
    if len(content) <= max_length:
        return content
    truncated = content[:max_length - 50]
    return truncated + "\n\n... [усечено до 4000 символов]"


def format_agent_error(agent_id: str, error: str) -> str:
    cfg = get_agent(agent_id) or {}
    label = cfg.get("label", agent_id)
    return f"❌ {label}: {error}"
