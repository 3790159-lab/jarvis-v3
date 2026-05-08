from __future__ import annotations

"""Phase 14: Multi-step task planner.

Detects compound queries and decomposes them into ordered steps.
Each step maps to a known intent that the bot can execute.

Usage:
    from app.services.task_planner import is_compound_task, decompose_task, TaskPlan
"""

import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TaskStep:
    step_number: int
    intent: str       # research | table | gen | brain | capabilities | chat
    query: str
    description: str  # human-readable label shown in progress
    result: Optional[str] = None
    done: bool = False
    error: Optional[str] = None


@dataclass
class TaskPlan:
    original_query: str
    steps: List[TaskStep] = field(default_factory=list)
    current_step: int = 0
    completed: bool = False

    def next_step(self) -> Optional[TaskStep]:
        for s in self.steps:
            if not s.done:
                return s
        return None

    def summary(self) -> str:
        lines = [f"📋 План: {self.original_query}", ""]
        for s in self.steps:
            icon = "✅" if s.done else ("▶️" if s.step_number == self.current_step + 1 else "⏳")
            lines.append(f"{icon} {s.step_number}. {s.description}")
        return "\n".join(lines)

    def results_summary(self) -> str:
        done = [s for s in self.steps if s.done and s.result]
        if not done:
            return "Нет результатов."
        parts = []
        for s in done:
            parts.append(f"**{s.description}**\n{s.result[:400]}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Compound-task detection
# ---------------------------------------------------------------------------

_COMPOUND_PATTERNS = [
    r"\bи\s+(потом|затем|после\s+этого|также)\b",
    r"\bплюс\s+(?:также\s+)?(?:сделай|найди|покажи|составь|напиши|создай)\b",
    r"\bтакже\s+(?:сделай|найди|покажи|составь|напиши|создай)\b",
    r"\bа\s+(?:ещё|еще|потом|затем)\b",
    r"\bзатем\s+(?:сделай|найди|напиши|покажи)\b",
    r"\b(?:во-первых|во-вторых|в-третьих)\b",
    r"\bstep\s+\d+\b",
    r"\band\s+then\b",
    r"\bfirst\b.{0,30}\bthen\b",
]

_COMPOUND_CONJUNCTIONS = [
    " и затем ", " и после ", " потом ", " затем ", " также найди ",
    " также сделай ", " плюс ", " а потом ", " а затем ", " а ещё ",
    " а еще ", " and then ", " after that ", " then ",
]


def is_compound_task(query: str) -> bool:
    q = query.lower().strip()
    if len(q) < 20:
        return False
    for pat in _COMPOUND_PATTERNS:
        if re.search(pat, q):
            return True
    for conj in _COMPOUND_CONJUNCTIONS:
        if conj in q:
            return True
    return False


# ---------------------------------------------------------------------------
# LLM decomposition
# ---------------------------------------------------------------------------

_INTENTS = ["research", "table", "gen", "brain", "capabilities", "chat"]

_SYSTEM_PROMPT = """You decompose a user request into ordered steps for an AI assistant.
Return ONLY valid JSON: {"steps": [{"intent": "...", "query": "...", "description": "..."}, ...]}.
intent must be one of: research, table, gen, brain, chat.
- research: search the web for information
- table: create a comparison/ranking table with data
- gen: generate text content (article, email, post, code snippet)
- brain: complex analysis, reasoning, long-form answer
- chat: short conversational answer
Keep queries short and self-contained. Maximum 5 steps. No markdown, no explanation."""


def _call_openai(query: str) -> Optional[List[Dict[str, str]]]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Decompose: {query}"},
        ],
        "temperature": 0,
        "max_tokens": 600,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"]["content"]
        return _parse_steps_json(text)
    except Exception:
        return None


def _call_anthropic(query: str) -> Optional[List[Dict[str, str]]]:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Decompose: {query}"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        text = data["content"][0]["text"]
        return _parse_steps_json(text)
    except Exception:
        return None


def _parse_steps_json(text: str) -> Optional[List[Dict[str, str]]]:
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        obj = json.loads(text)
        steps = obj.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return None
        result = []
        for s in steps[:5]:
            if not isinstance(s, dict):
                continue
            intent = s.get("intent", "chat")
            if intent not in _INTENTS:
                intent = "chat"
            result.append({
                "intent": intent,
                "query": str(s.get("query", ""))[:300],
                "description": str(s.get("description", s.get("query", "")))[:100],
            })
        return result if result else None
    except Exception:
        return None


def _heuristic_decompose(query: str) -> List[Dict[str, str]]:
    """Fallback: split on known conjunctions."""
    q = query.lower()
    for sep in [" и затем ", " затем ", " а потом ", " потом ", " and then ", " after that "]:
        if sep in q:
            idx = query.lower().index(sep)
            part1 = query[:idx].strip()
            part2 = query[idx + len(sep):].strip()
            return [
                {"intent": "research", "query": part1, "description": f"Шаг 1: {part1[:60]}"},
                {"intent": "research", "query": part2, "description": f"Шаг 2: {part2[:60]}"},
            ]
    # Split on ", и "
    parts = re.split(r",\s*и\s+", query, maxsplit=4)
    if len(parts) > 1:
        return [
            {"intent": "research", "query": p.strip(), "description": f"Шаг {i+1}: {p.strip()[:60]}"}
            for i, p in enumerate(parts) if p.strip()
        ]
    return [{"intent": "research", "query": query, "description": query[:80]}]


def decompose_task(query: str) -> TaskPlan:
    """Decompose a compound query into a TaskPlan."""
    raw_steps = _call_openai(query) or _call_anthropic(query) or _heuristic_decompose(query)
    steps = [
        TaskStep(
            step_number=i + 1,
            intent=s["intent"],
            query=s["query"],
            description=s["description"],
        )
        for i, s in enumerate(raw_steps)
    ]
    return TaskPlan(original_query=query, steps=steps)
