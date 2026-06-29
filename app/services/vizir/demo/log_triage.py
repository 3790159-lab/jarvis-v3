# -*- coding: utf-8 -*-
"""Log-triage demo: a real, FREE 3-step coordination over a jarvis-format log
('TS | LEVEL | LOGGER | MSG'). read_log -> analyze_levels -> summarize.

Each step is a free StepHandler; later steps read earlier results via
ctx['results']. Used to prove the CC-driver runs the core end-to-end at $0.
"""
from __future__ import annotations

from pathlib import Path

from ..handlers import HandlerRegistry, HandlerResult
from ..models import Plan, Step


async def _read_log(step, ctx) -> HandlerResult:
    path = Path(step.params["path"])
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return HandlerResult(result=lines)


async def _analyze_levels(step, ctx) -> HandlerResult:
    lines = ctx["results"]["read_log"]
    counts: dict[str, int] = {}
    for ln in lines:
        parts = ln.split("|")
        if len(parts) < 4:
            continue                       # not the jarvis format -> skip
        level = parts[1].strip()
        counts[level] = counts.get(level, 0) + 1
    return HandlerResult(result={"lines": len(lines), "counts": counts})


async def _summarize(step, ctx) -> HandlerResult:
    a = ctx["results"]["analyze_levels"]
    parts = [f"lines={a['lines']}"]
    for level, n in sorted(a["counts"].items()):
        parts.append(f"{level}={n}")
    return HandlerResult(result=" ".join(parts))


def build_log_triage(path: str) -> tuple[HandlerRegistry, Plan]:
    """Return a registry + 3-step plan to triage the log at ``path`` (all free)."""
    reg = HandlerRegistry()
    reg.register("read_log", _read_log)
    reg.register("analyze_levels", _analyze_levels)
    reg.register("summarize", _summarize)
    plan = Plan(steps=[
        Step(kind="read_log", params={"path": path}),
        Step(kind="analyze_levels"),
        Step(kind="summarize"),
    ])
    return reg, plan
