from __future__ import annotations

import json
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .registry import AgentRegistry


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    STALE = "stale"
    DISABLED = "disabled"


class WorkerLease(BaseModel):
    worker_id: str
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    status: WorkerStatus = WorkerStatus.IDLE
    current_task_id: Optional[str] = None
    heartbeat_ts: float = 0.0
    concurrency_limit: int = 1
    busy_slots: int = 0
    preferred_provider: str = "cloud"
    fallback_provider: str = "ollama"

    def is_available(self) -> bool:
        return self.status == WorkerStatus.IDLE and self.busy_slots < self.concurrency_limit


class WorkerRegistry:
    def __init__(self, path: Path, stale_after_seconds: int = 180) -> None:
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.workers: dict[str, WorkerLease] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"workers": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.workers = {}
        for item in raw.get("workers", []):
            worker = WorkerLease.model_validate(item)
            self.workers[worker.worker_id] = worker

    def save(self) -> None:
        data = {"workers": [w.model_dump(mode="json") for w in self.workers.values()]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def bootstrap_from_registry(self, registry: AgentRegistry) -> None:
        now = time.time()
        changed = False

        for agent in registry.list_agents():
            worker_id = f"worker_{agent.agent_id}"
            if worker_id not in self.workers:
                self.workers[worker_id] = WorkerLease(
                    worker_id=worker_id,
                    agent_id=agent.agent_id,
                    capabilities=agent.all_capabilities(),
                    status=WorkerStatus.IDLE,
                    current_task_id=None,
                    heartbeat_ts=now,
                    concurrency_limit=1,
                    busy_slots=0,
                    preferred_provider=agent.preferred_provider,
                    fallback_provider=agent.fallback_provider,
                )
                changed = True
            else:
                worker = self.workers[worker_id]
                worker.capabilities = agent.all_capabilities()
                worker.preferred_provider = agent.preferred_provider
                worker.fallback_provider = agent.fallback_provider
                changed = True

        if changed:
            self.save()

    def heartbeat(self, worker_id: str) -> None:
        worker = self.workers.get(worker_id)
        if not worker:
            return
        worker.heartbeat_ts = time.time()
        if worker.status == WorkerStatus.STALE:
            worker.status = WorkerStatus.IDLE
        self.save()

    def mark_stale(self) -> None:
        now = time.time()
        changed = False
        for worker in self.workers.values():
            if worker.status == WorkerStatus.BUSY and (now - worker.heartbeat_ts) > self.stale_after_seconds:
                worker.status = WorkerStatus.STALE
                changed = True
        if changed:
            self.save()

    def claim(self, capability: str, preferred_agent_id: str | None = None) -> WorkerLease | None:
        self.mark_stale()

        candidates = list(self.workers.values())
        if preferred_agent_id:
            candidates.sort(key=lambda w: 0 if w.agent_id == preferred_agent_id else 1)

        for worker in candidates:
            if capability not in worker.capabilities:
                continue
            if preferred_agent_id and worker.agent_id != preferred_agent_id:
                continue
            if worker.is_available():
                worker.status = WorkerStatus.BUSY
                worker.busy_slots += 1
                worker.heartbeat_ts = time.time()
                self.save()
                return worker
        return None

    def assign_task(self, worker_id: str, task_id: str) -> None:
        worker = self.workers.get(worker_id)
        if not worker:
            return
        worker.current_task_id = task_id
        worker.heartbeat_ts = time.time()
        self.save()

    def release(self, worker_id: str) -> None:
        worker = self.workers.get(worker_id)
        if not worker:
            return
        worker.busy_slots = max(0, worker.busy_slots - 1)
        worker.current_task_id = None
        worker.status = WorkerStatus.IDLE if worker.busy_slots == 0 else WorkerStatus.BUSY
        worker.heartbeat_ts = time.time()
        self.save()

    def list_workers(self) -> list[WorkerLease]:
        self.mark_stale()
        return list(self.workers.values())

    def summary(self) -> dict:
        all_workers = self.list_workers()
        return {
            "total": len(all_workers),
            "idle": sum(1 for w in all_workers if w.status == WorkerStatus.IDLE),
            "busy": sum(1 for w in all_workers if w.status == WorkerStatus.BUSY),
            "stale": sum(1 for w in all_workers if w.status == WorkerStatus.STALE),
            "disabled": sum(1 for w in all_workers if w.status == WorkerStatus.DISABLED),
        }