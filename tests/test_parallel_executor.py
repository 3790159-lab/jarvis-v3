"""Tests for Phase 31: Parallel Multi-Agent Execution."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.parallel_executor import (
    ParallelProgress,
    build_execution_layers,
    estimate_parallel_speedup,
    execute_parallel,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def echo_agent(task: Dict[str, Any]) -> str:
    """Simple agent that returns task id as result."""
    return f"result_{task['id']}"


def slow_agent(task: Dict[str, Any]) -> str:
    """Agent that sleeps to simulate real work."""
    time.sleep(task.get("sleep", 0.01))
    return f"done_{task['id']}"


def failing_agent(task: Dict[str, Any]) -> str:
    raise RuntimeError(f"Task {task['id']} failed")


# ─── execute_parallel ────────────────────────────────────────────────────────

class TestExecuteParallel:
    def test_empty_tasks_returns_empty(self):
        result = execute_parallel([], echo_agent)
        assert result == []

    def test_single_task(self):
        tasks = [{"id": "t1", "agent": "echo", "query": "q1"}]
        results = execute_parallel(tasks, echo_agent)
        assert len(results) == 1
        assert results[0]["task_id"] == "t1"
        assert results[0]["status"] == "done"
        assert results[0]["result"] == "result_t1"

    def test_results_in_input_order(self):
        tasks = [
            {"id": "a", "agent": "echo", "query": "qa"},
            {"id": "b", "agent": "echo", "query": "qb"},
            {"id": "c", "agent": "echo", "query": "qc"},
        ]
        results = execute_parallel(tasks, echo_agent)
        assert [r["task_id"] for r in results] == ["a", "b", "c"]

    def test_parallel_tasks_run_concurrently(self):
        """Tasks without dependencies should overlap in time."""
        start_times = {}
        lock = threading.Lock()

        def timed_agent(task):
            with lock:
                start_times[task["id"]] = time.monotonic()
            time.sleep(0.05)
            return task["id"]

        tasks = [
            {"id": "1", "depends_on": []},
            {"id": "2", "depends_on": []},
            {"id": "3", "depends_on": []},
        ]
        t_start = time.monotonic()
        execute_parallel(tasks, timed_agent, max_workers=3)
        elapsed = time.monotonic() - t_start
        # 3 parallel tasks × 0.05s each should take ~0.05-0.1s, not 0.15s
        assert elapsed < 0.13, f"Expected parallel, got {elapsed:.2f}s"

    def test_dependent_task_runs_after_dependency(self):
        execution_order = []
        lock = threading.Lock()

        def ordered_agent(task):
            time.sleep(0.01)
            with lock:
                execution_order.append(task["id"])
            return task["id"]

        tasks = [
            {"id": "A", "depends_on": []},
            {"id": "B", "depends_on": ["A"]},
        ]
        execute_parallel(tasks, ordered_agent)
        assert execution_order.index("A") < execution_order.index("B")

    def test_failing_task_captured_in_result(self):
        tasks = [{"id": "bad", "agent": "fail"}]
        results = execute_parallel(tasks, failing_agent)
        assert results[0]["status"] == "error"
        assert results[0]["error"] is not None

    def test_failing_task_does_not_block_others(self):
        tasks = [
            {"id": "good1", "depends_on": []},
            {"id": "bad", "depends_on": []},
            {"id": "good2", "depends_on": []},
        ]

        def mixed_agent(task):
            if task["id"] == "bad":
                raise ValueError("intentional")
            return "ok"

        results = execute_parallel(tasks, mixed_agent)
        statuses = {r["task_id"]: r["status"] for r in results}
        assert statuses["good1"] == "done"
        assert statuses["good2"] == "done"
        assert statuses["bad"] == "error"

    def test_progress_fn_called(self):
        events = []

        def progress(task_id, status, result):
            events.append((task_id, status))

        tasks = [{"id": "t1", "depends_on": []}]
        execute_parallel(tasks, echo_agent, progress_fn=progress)
        task_ids = [e[0] for e in events]
        statuses = [e[1] for e in events]
        assert "t1" in task_ids
        assert "started" in statuses
        assert "done" in statuses

    def test_duration_ms_populated(self):
        tasks = [{"id": "t1", "sleep": 0.02}]
        results = execute_parallel(tasks, slow_agent)
        assert results[0]["duration_ms"] > 0

    def test_max_workers_respected(self):
        """With max_workers=1, tasks run serially."""
        order = []

        def serial_agent(task):
            order.append(task["id"])
            return task["id"]

        tasks = [{"id": str(i), "depends_on": []} for i in range(3)]
        execute_parallel(tasks, serial_agent, max_workers=1)
        assert len(order) == 3


# ─── build_execution_layers ──────────────────────────────────────────────────

class TestBuildExecutionLayers:
    def test_independent_tasks_in_one_layer(self):
        tasks = [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": []},
            {"id": "c", "depends_on": []},
        ]
        layers = build_execution_layers(tasks)
        assert len(layers) == 1
        assert len(layers[0]) == 3

    def test_linear_chain_in_separate_layers(self):
        tasks = [
            {"id": "1", "depends_on": []},
            {"id": "2", "depends_on": ["1"]},
            {"id": "3", "depends_on": ["2"]},
        ]
        layers = build_execution_layers(tasks)
        assert len(layers) == 3
        assert layers[0][0]["id"] == "1"
        assert layers[1][0]["id"] == "2"
        assert layers[2][0]["id"] == "3"

    def test_diamond_dependency(self):
        # A -> B, A -> C, B+C -> D
        tasks = [
            {"id": "A", "depends_on": []},
            {"id": "B", "depends_on": ["A"]},
            {"id": "C", "depends_on": ["A"]},
            {"id": "D", "depends_on": ["B", "C"]},
        ]
        layers = build_execution_layers(tasks)
        # Layer 0: A; Layer 1: B, C; Layer 2: D
        assert len(layers) == 3
        assert layers[0][0]["id"] == "A"
        bc_ids = {t["id"] for t in layers[1]}
        assert bc_ids == {"B", "C"}
        assert layers[2][0]["id"] == "D"

    def test_empty_tasks(self):
        layers = build_execution_layers([])
        assert layers == []


# ─── estimate_parallel_speedup ───────────────────────────────────────────────

class TestEstimateParallelSpeedup:
    def test_independent_tasks_speedup_ratio(self):
        tasks = [
            {"id": "1", "estimated_seconds": 10.0, "depends_on": []},
            {"id": "2", "estimated_seconds": 10.0, "depends_on": []},
            {"id": "3", "estimated_seconds": 10.0, "depends_on": []},
        ]
        est = estimate_parallel_speedup(tasks)
        assert est["sequential_s"] == 30.0
        assert est["parallel_s"] == 10.0
        assert est["speedup_ratio"] == 3.0

    def test_sequential_chain_no_speedup(self):
        tasks = [
            {"id": "1", "estimated_seconds": 5.0, "depends_on": []},
            {"id": "2", "estimated_seconds": 5.0, "depends_on": ["1"]},
        ]
        est = estimate_parallel_speedup(tasks)
        assert est["sequential_s"] == 10.0
        assert est["parallel_s"] == 10.0
        assert est["speedup_ratio"] == 1.0

    def test_default_estimate_when_no_seconds(self):
        tasks = [
            {"id": "1", "depends_on": []},
            {"id": "2", "depends_on": []},
        ]
        est = estimate_parallel_speedup(tasks)
        assert est["sequential_s"] == 10.0  # 2 * 5.0
        assert est["parallel_s"] == 5.0    # max(5.0, 5.0)


# ─── ParallelProgress ────────────────────────────────────────────────────────

class TestParallelProgress:
    def test_initial_state_pending(self):
        tasks = [{"id": "t1", "agent": "echo"}, {"id": "t2", "agent": "research"}]
        progress = ParallelProgress(tasks)
        text = progress.format()
        assert "⏳" in text

    def test_update_started(self):
        tasks = [{"id": "t1", "agent": "echo"}]
        progress = ParallelProgress(tasks)
        progress.update("t1", "started")
        text = progress.format()
        assert "🔄" in text

    def test_update_done(self):
        tasks = [{"id": "t1", "agent": "echo"}]
        progress = ParallelProgress(tasks)
        progress.update("t1", "done")
        text = progress.format()
        assert "✅" in text

    def test_update_error(self):
        tasks = [{"id": "t1", "agent": "echo"}]
        progress = ParallelProgress(tasks)
        progress.update("t1", "error")
        text = progress.format()
        assert "❌" in text

    def test_is_complete_when_all_done(self):
        tasks = [{"id": "t1"}, {"id": "t2"}]
        progress = ParallelProgress(tasks)
        assert not progress.is_complete()
        progress.update("t1", "done")
        assert not progress.is_complete()
        progress.update("t2", "done")
        assert progress.is_complete()

    def test_completion_message(self):
        tasks = [{"id": "t1", "agent": "echo"}]
        progress = ParallelProgress(tasks)
        progress.update("t1", "done")
        text = progress.format()
        assert "Готово" in text or "done" in text.lower()
