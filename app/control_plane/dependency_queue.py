from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from .models import TaskRuntimeState, TaskSpec, TaskStatus


class QueueStats(BaseModel):
    total: int = 0
    pending: int = 0
    ready: int = 0
    queued: int = 0
    running: int = 0
    blocked: int = 0
    waiting_consultation: int = 0
    completed: int = 0
    failed: int = 0


class DependencyQueue:
    def validate_no_cycles(self, tasks: list[TaskSpec]) -> None:
        graph: dict[str, list[str]] = {t.task_id: list(t.depends_on) for t in tasks}

        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise RuntimeError(f"Circular dependency detected at task: {node}")
            visiting.add(node)
            for dep in graph.get(node, []):
                if dep not in graph:
                    raise RuntimeError(f"Task '{node}' depends on missing task '{dep}'")
                dfs(dep)
            visiting.remove(node)
            visited.add(node)

        for key in graph:
            dfs(key)

    def refresh(self, states: list[TaskRuntimeState]) -> None:
        state_by_id = {s.task.task_id: s for s in states}

        for state in states:
            if state.status in {TaskStatus.COMPLETED, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                continue

            if not state.task.depends_on:
                if state.status in {
                    TaskStatus.DRAFT,
                    TaskStatus.PENDING,
                    TaskStatus.BLOCKED,
                    TaskStatus.QUEUED,
                    TaskStatus.RETRY_SCHEDULED,
                }:
                    state.status = TaskStatus.READY
                continue

            blockers: list[str] = []
            for dep in state.task.depends_on:
                dep_state = state_by_id.get(dep)
                if not dep_state or dep_state.status != TaskStatus.COMPLETED:
                    blockers.append(dep)

            if blockers:
                state.status = TaskStatus.BLOCKED
            else:
                if state.status in {
                    TaskStatus.PENDING,
                    TaskStatus.BLOCKED,
                    TaskStatus.QUEUED,
                    TaskStatus.RETRY_SCHEDULED,
                    TaskStatus.DRAFT,
                }:
                    state.status = TaskStatus.READY

    def next_ready(self, states: list[TaskRuntimeState], max_items: int = 4) -> list[TaskRuntimeState]:
        ready = [s for s in states if s.status == TaskStatus.READY]
        priority_rank = {
            "critical": 0,
            "urgent": 1,
            "high": 2,
            "normal": 3,
            "low": 4,
            "background": 5,
        }
        ready.sort(
            key=lambda s: (
                priority_rank.get(s.task.priority, 99),
                -len(s.task.depends_on),
                s.task.task_id,
            )
        )
        return ready[:max_items]

    def stats(self, states: list[TaskRuntimeState]) -> QueueStats:
        data = QueueStats(total=len(states))
        for state in states:
            if state.status == TaskStatus.PENDING:
                data.pending += 1
            elif state.status == TaskStatus.READY:
                data.ready += 1
            elif state.status == TaskStatus.QUEUED:
                data.queued += 1
            elif state.status == TaskStatus.RUNNING:
                data.running += 1
            elif state.status == TaskStatus.BLOCKED:
                data.blocked += 1
            elif state.status == TaskStatus.WAITING_CONSULTATION:
                data.waiting_consultation += 1
            elif state.status == TaskStatus.COMPLETED:
                data.completed += 1
            elif state.status == TaskStatus.FAILED:
                data.failed += 1
        return data