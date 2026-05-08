"""Phase 31: Parallel Multi-Agent Execution.

Runs multiple agent tasks in parallel using ThreadPoolExecutor.
Supports dependency graph: tasks that don't depend on each other run simultaneously.
Results are returned in the original input order.

Example:
    tasks = [
        {"id": "1", "agent": "perplexity", "query": "AI trends", "depends_on": []},
        {"id": "2", "agent": "obsidian", "query": "save note", "depends_on": []},
        {"id": "3", "agent": "table", "query": "make table", "depends_on": ["1"]},
    ]
    results = execute_parallel(tasks, agent_fn=my_agent)
    # tasks 1 and 2 run simultaneously; task 3 runs after 1 completes
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core parallel execution
# ---------------------------------------------------------------------------

def execute_parallel(
    tasks: List[Dict[str, Any]],
    agent_fn: Callable[[Dict[str, Any]], Any],
    max_workers: int = 5,
    timeout_seconds: float = 120.0,
    progress_fn: Optional[Callable[[str, str, Any], None]] = None,
) -> List[Dict[str, Any]]:
    """Execute tasks in parallel respecting dependency order.

    Args:
        tasks: List of task dicts. Each must have "id"; optional "depends_on" (list of ids).
        agent_fn: Called as agent_fn(task) -> result. Must be thread-safe.
        max_workers: Maximum concurrent threads.
        timeout_seconds: Per-task timeout.
        progress_fn: Called as progress_fn(task_id, status, result) on state changes.
                     status: "started" | "done" | "error"

    Returns:
        List of result dicts in the same order as input tasks.
        Each result: {"task_id", "status", "result", "error", "duration_ms"}
    """
    if not tasks:
        return []

    # Build dependency graph
    task_map = {t["id"]: t for t in tasks}
    results: Dict[str, Dict[str, Any]] = {}
    futures: Dict[Future, str] = {}  # future -> task_id

    def _run_task(task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task["id"]
        start = time.monotonic()
        try:
            if progress_fn:
                progress_fn(task_id, "started", None)
            result = agent_fn(task)
            duration = (time.monotonic() - start) * 1000
            if progress_fn:
                progress_fn(task_id, "done", result)
            return {"task_id": task_id, "status": "done", "result": result, "error": None, "duration_ms": duration}
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            logger.warning("Task %s failed: %s", task_id, exc)
            if progress_fn:
                progress_fn(task_id, "error", str(exc))
            return {"task_id": task_id, "status": "error", "result": None, "error": str(exc), "duration_ms": duration}

    def _can_start(task: Dict[str, Any]) -> bool:
        deps = task.get("depends_on") or []
        return all(
            results.get(dep, {}).get("status") in ("done", "error")
            for dep in deps
        )

    pending = list(tasks)
    completed_ids: set = set()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while pending or futures:
            # Submit tasks whose dependencies are met
            ready = [t for t in pending if _can_start(t)]
            for task in ready:
                pending.remove(task)
                f = executor.submit(_run_task, task)
                futures[f] = task["id"]

            if not futures:
                # No futures running and remaining tasks have unresolvable deps
                break

            # Wait for at least one to complete
            done, _ = wait(futures.keys(), timeout=timeout_seconds, return_when=FIRST_COMPLETED)

            for f in done:
                task_id = futures.pop(f)
                try:
                    result = f.result(timeout=1)
                except Exception as exc:
                    result = {"task_id": task_id, "status": "error", "result": None,
                              "error": str(exc), "duration_ms": 0}
                results[task_id] = result
                completed_ids.add(task_id)

    # Tasks that never ran (broken deps)
    for task in pending:
        results[task["id"]] = {
            "task_id": task["id"],
            "status": "skipped",
            "result": None,
            "error": f"Dependency not met: {task.get('depends_on')}",
            "duration_ms": 0,
        }

    # Return in original order
    return [results.get(t["id"], {"task_id": t["id"], "status": "unknown"}) for t in tasks]


# ---------------------------------------------------------------------------
# Dependency analysis
# ---------------------------------------------------------------------------

def build_execution_layers(tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group tasks into sequential layers (topological sort).

    All tasks in the same layer can run in parallel.
    Returns list of layers, each layer is a list of tasks.
    """
    task_map = {t["id"]: t for t in tasks}
    in_degree: Dict[str, int] = {t["id"]: 0 for t in tasks}
    dependents: Dict[str, List[str]] = {t["id"]: [] for t in tasks}

    for task in tasks:
        for dep in (task.get("depends_on") or []):
            if dep in in_degree:
                in_degree[task["id"]] += 1
                dependents[dep].append(task["id"])

    layers = []
    queue = [tid for tid, deg in in_degree.items() if deg == 0]

    while queue:
        layers.append([task_map[tid] for tid in queue])
        next_queue = []
        for tid in queue:
            for dep in dependents.get(tid, []):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        queue = next_queue

    return layers


def estimate_parallel_speedup(tasks: List[Dict[str, Any]]) -> Dict[str, float]:
    """Estimate sequential vs parallel execution time.

    Assumes each task has an optional "estimated_seconds" field.
    Returns {"sequential_s", "parallel_s", "speedup_ratio"}.
    """
    layers = build_execution_layers(tasks)
    sequential = sum(t.get("estimated_seconds", 5.0) for t in tasks)
    parallel = sum(
        max((t.get("estimated_seconds", 5.0) for t in layer), default=0)
        for layer in layers
    )
    speedup = sequential / parallel if parallel > 0 else 1.0
    return {
        "sequential_s": round(sequential, 1),
        "parallel_s": round(parallel, 1),
        "speedup_ratio": round(speedup, 2),
    }


# ---------------------------------------------------------------------------
# Progress visualizer
# ---------------------------------------------------------------------------

class ParallelProgress:
    """Tracks and formats progress for Telegram messages."""

    def __init__(self, tasks: List[Dict[str, Any]]) -> None:
        self._tasks = {t["id"]: t for t in tasks}
        self._status: Dict[str, str] = {t["id"]: "pending" for t in tasks}
        self._durations: Dict[str, float] = {}
        self._start = time.monotonic()

    def update(self, task_id: str, status: str, result: Any = None) -> None:
        self._status[task_id] = status
        if status in ("done", "error"):
            self._durations[task_id] = (time.monotonic() - self._start) * 1000

    def format(self) -> str:
        icons = {"pending": "⏳", "started": "🔄", "done": "✅", "error": "❌", "skipped": "⏭"}
        total = len(self._tasks)
        done = sum(1 for s in self._status.values() if s == "done")
        lines = [f"🚀 Параллельное выполнение ({done}/{total}):"]
        for i, (tid, task) in enumerate(self._tasks.items(), 1):
            status = self._status.get(tid, "pending")
            icon = icons.get(status, "❓")
            name = task.get("agent", tid)
            dur = self._durations.get(tid)
            dur_str = f" ({dur/1000:.1f}s)" if dur else ""
            lines.append(f"[{i}/{total}] {icon} {name}{dur_str}")

        total_elapsed = (time.monotonic() - self._start)
        if all(s in ("done", "error", "skipped") for s in self._status.values()):
            lines.append(f"\n✨ Готово! Время: {total_elapsed:.1f}s")

        return "\n".join(lines)

    def is_complete(self) -> bool:
        return all(s in ("done", "error", "skipped") for s in self._status.values())
