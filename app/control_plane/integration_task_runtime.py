from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import AgentMetrics, AgentResult, ArtifactRef, TaskSpec


class IntegrationTaskExecutor:
    def __init__(self, generated_dir: Path) -> None:
        self.generated_dir = Path(generated_dir)
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        agent_id: str,
        task: TaskSpec,
        runtime_connector_bridge: Any,
    ) -> AgentResult:
        started = time.perf_counter()

        capability = str(task.metadata.get("connector_capability") or task.required_capability or "").strip()
        action = str(task.metadata.get("connector_action") or "config_check").strip()
        preferred_service = str(task.metadata.get("preferred_service") or "").strip()
        payload = dict(task.metadata.get("connector_payload") or {})
        dry_run = bool(task.metadata.get("dry_run", True))
        persist_output = bool(task.metadata.get("persist_output", True))

        outcome = runtime_connector_bridge.run(
            agent_id=agent_id,
            capability=capability,
            action=action,
            payload=payload,
            preferred_service=preferred_service,
            dry_run=dry_run,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        artifacts: list[ArtifactRef] = []
        normalized_text = outcome.message
        next_actions: list[str] = []

        if outcome.output:
            normalized_text = json.dumps(outcome.output, ensure_ascii=False, indent=2)

        if outcome.ok:
            next_actions = [
                "Commit integration result",
                "Persist integration outcome to mission memory",
            ]
            if persist_output:
                out = self.generated_dir / f"{task.task_id}_integration.json"
                out.write_text(
                    json.dumps({
                        "task_id": task.task_id,
                        "service": outcome.service,
                        "connector_id": outcome.connector_id,
                        "action": action,
                        "dry_run": dry_run,
                        "output": outcome.output,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                artifacts.append(ArtifactRef(name=out.name, path=str(out), kind="json"))
        else:
            next_actions = [
                "Review connector health and auth state",
                "Retry with fallback connector or safer mode",
            ]

        return AgentResult(
            agent_id=agent_id,
            status="ok" if outcome.ok else "error",
            summary=f"{agent_id} executed integration task '{task.title}' via {outcome.service or 'unknown_service'}",
            normalized_text=normalized_text,
            artifacts=artifacts,
            next_actions=next_actions,
            confidence=0.82 if outcome.ok else 0.35,
            metrics=AgentMetrics(
                duration_ms=elapsed_ms,
                tokens_in=0,
                tokens_out=0,
                cost_estimate=0.0,
            ),
            error=None if outcome.ok else outcome.message,
            handoff_notes=[
                f"connector_id={outcome.connector_id}",
                f"service={outcome.service}",
                f"action={action}",
                f"dry_run={dry_run}",
            ],
            validation_hints=[
                "Verify connector output and auth configuration before promoting to broader autonomy.",
            ],
        )