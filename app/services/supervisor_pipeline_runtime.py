from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

from app.services.supervisor_automation_runtime import get_supervisor_automation_runtime


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    try:
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SupervisorPipelineConfig:
    artifact_dir: Path
    max_steps: int


class SupervisorPipelineRuntime:
    def __init__(self, config: Optional[SupervisorPipelineConfig] = None) -> None:
        self.config = config or self.from_env()

    @staticmethod
    def from_env() -> SupervisorPipelineConfig:
        raw_dir = (os.getenv("JARVIS_PIPELINE_ARTIFACT_DIR", "jarvis_stage3_artifacts/n8n_supervisor/pipelines") or "").strip()
        artifact_dir = Path(raw_dir)
        if not artifact_dir.is_absolute():
            artifact_dir = _PROJECT_ROOT / artifact_dir

        return SupervisorPipelineConfig(
            artifact_dir=artifact_dir,
            max_steps=max(1, _env_int("JARVIS_PIPELINE_MAX_STEPS", 12)),
        )

    def _ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self._ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        self._ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _normalize_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []

        if not isinstance(steps, list) or not steps:
            raise RuntimeError("Pipeline steps must be a non-empty list")

        if len(steps) > self.config.max_steps:
            raise RuntimeError(f"Pipeline exceeds max steps: {len(steps)} > {self.config.max_steps}")

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise RuntimeError(f"Step #{idx} must be an object")

            action = str(step.get("action", "") or "").strip()
            intent = str(step.get("intent", action or f"step_{idx}") or "").strip()

            if not action:
                raise RuntimeError(f"Step #{idx} is missing action")

            normalized.append(
                {
                    "index": idx,
                    "step_id": str(step.get("step_id", f"step-{idx}")),
                    "action": action,
                    "intent": intent,
                    "payload": step.get("payload", {}),
                    "source": str(step.get("source", "jarvis")),
                    "use_test_webhook": bool(step.get("use_test_webhook", False)),
                    "continue_on_error": bool(step.get("continue_on_error", False)),
                }
            )

        return normalized

    def preview(
        self,
        *,
        pipeline_name: str,
        steps: List[Dict[str, Any]],
        mission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        automation = get_supervisor_automation_runtime()
        normalized_steps = self._normalize_steps(steps)
        pipeline_id = mission_id or f"pipeline-{uuid.uuid4().hex[:10]}"

        step_previews = []
        for step in normalized_steps:
            step_previews.append(
                automation.preview(
                    action=step["action"],
                    intent=step["intent"],
                    payload=step["payload"],
                    mission_id=pipeline_id,
                    task_id=step["step_id"],
                    source=step["source"],
                    use_test_webhook=step["use_test_webhook"],
                )
            )

        return {
            "pipeline_name": pipeline_name,
            "pipeline_id": pipeline_id,
            "step_count": len(normalized_steps),
            "steps": normalized_steps,
            "step_previews": step_previews,
            "artifact_dir": str(self.config.artifact_dir),
            "max_steps": self.config.max_steps,
        }

    def run(
        self,
        *,
        pipeline_name: str,
        steps: List[Dict[str, Any]],
        mission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        automation = get_supervisor_automation_runtime()
        preview = self.preview(
            pipeline_name=pipeline_name,
            steps=steps,
            mission_id=mission_id,
        )

        pipeline_run_id = f"pipe-{uuid.uuid4().hex[:12]}"
        run_dir = self._ensure_dir(self.config.artifact_dir / pipeline_run_id)
        journal_path = self.config.artifact_dir / "pipeline_journal.jsonl"

        request_record = {
            "pipeline_run_id": pipeline_run_id,
            "created_at": _utc_now(),
            "kind": "request",
            "preview": preview,
        }
        self._write_json(run_dir / "request.json", request_record)
        self._append_jsonl(journal_path, request_record)

        step_results: List[Dict[str, Any]] = []
        overall_success = True
        stop_reason: Optional[str] = None

        for step in preview["steps"]:
            result = automation.execute(
                action=step["action"],
                intent=step["intent"],
                payload=step["payload"],
                mission_id=preview["pipeline_id"],
                task_id=step["step_id"],
                source=step["source"],
                use_test_webhook=step["use_test_webhook"],
            )

            step_result = {
                "index": step["index"],
                "step_id": step["step_id"],
                "action": step["action"],
                "intent": step["intent"],
                "continue_on_error": step["continue_on_error"],
                "success": bool(result.get("success", False)),
                "run_id": result.get("run_id"),
                "artifact_dir": result.get("artifact_dir"),
                "attempt_count": result.get("attempt_count"),
                "final_result": result.get("final_result"),
            }
            step_results.append(step_result)

            if not step_result["success"]:
                overall_success = False
                if not step["continue_on_error"]:
                    stop_reason = f"step_failed:{step['step_id']}"
                    break

        response = {
            "pipeline_run_id": pipeline_run_id,
            "pipeline_name": pipeline_name,
            "pipeline_id": preview["pipeline_id"],
            "created_at": _utc_now(),
            "artifact_dir": str(run_dir),
            "journal_path": str(journal_path),
            "step_count": preview["step_count"],
            "completed_steps": len(step_results),
            "success": overall_success,
            "stop_reason": stop_reason,
            "preview": preview,
            "step_results": step_results,
        }

        self._write_json(run_dir / "result.json", response)
        self._append_jsonl(
            journal_path,
            {
                "pipeline_run_id": pipeline_run_id,
                "created_at": _utc_now(),
                "kind": "result",
                "success": overall_success,
                "completed_steps": len(step_results),
                "stop_reason": stop_reason,
            },
        )

        return response


_RUNTIME: Optional[SupervisorPipelineRuntime] = None


def get_supervisor_pipeline_runtime() -> SupervisorPipelineRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = SupervisorPipelineRuntime()
    return _RUNTIME