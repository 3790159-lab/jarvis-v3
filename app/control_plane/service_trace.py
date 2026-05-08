from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class ServiceTraceStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        task_id: str,
        agent_id: str,
        capability: str,
        task_type: str,
        planned_provider: str,
        desired_service: str,
        attempted_service: str,
        actual_service: str,
        status: str,
        reason: str,
        dry_run: bool,
        guidance_domains: list[str] | None = None,
    ) -> None:
        row = {
            "trace_id": f"trace_{uuid.uuid4().hex[:10]}",
            "ts": time.time(),
            "task_id": task_id,
            "agent_id": agent_id,
            "capability": capability,
            "task_type": task_type,
            "planned_provider": planned_provider,
            "desired_service": desired_service,
            "attempted_service": attempted_service,
            "actual_service": actual_service,
            "status": status,
            "reason": reason,
            "dry_run": bool(dry_run),
            "guidance_domains": list(guidance_domains or []),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, limit):]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out