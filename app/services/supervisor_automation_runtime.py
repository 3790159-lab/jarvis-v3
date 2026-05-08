from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

from app.services.n8n_bridge import get_n8n_bridge


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    try:
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
class SupervisorAutomationConfig:
    artifact_dir: Path
    max_retries: int
    retry_delay_ms: int
    require_n8n_health: bool


class SupervisorAutomationRuntime:
    def __init__(self, config: Optional[SupervisorAutomationConfig] = None) -> None:
        self.config = config or self.from_env()

    @staticmethod
    def from_env() -> SupervisorAutomationConfig:
        raw_dir = (os.getenv("JARVIS_AUTOMATION_ARTIFACT_DIR", "jarvis_stage3_artifacts/n8n_supervisor") or "").strip()
        artifact_dir = Path(raw_dir)
        if not artifact_dir.is_absolute():
            artifact_dir = _PROJECT_ROOT / artifact_dir

        return SupervisorAutomationConfig(
            artifact_dir=artifact_dir,
            max_retries=max(0, _env_int("JARVIS_AUTOMATION_MAX_RETRIES", 2)),
            retry_delay_ms=max(0, _env_int("JARVIS_AUTOMATION_RETRY_DELAY_MS", 1200)),
            require_n8n_health=_env_bool("JARVIS_AUTOMATION_REQUIRE_N8N_HEALTH", True),
        )

    def _ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        self._ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self._ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def preview(
        self,
        *,
        action: str,
        intent: str,
        payload: Optional[Any] = None,
        mission_id: Optional[str] = None,
        task_id: Optional[str] = None,
        source: str = "jarvis",
        use_test_webhook: bool = False,
    ) -> Dict[str, Any]:
        bridge = get_n8n_bridge()
        bridge_cfg = bridge.config

        mission_id = mission_id or f"preview-{uuid.uuid4().hex[:10]}"
        task_id = task_id or "dispatch-1"

        selected_path = bridge_cfg.test_trigger_path if use_test_webhook else bridge_cfg.trigger_path
        return {
            "route": "n8n",
            "action": action,
            "intent": intent,
            "mission_id": mission_id,
            "task_id": task_id,
            "source": source,
            "use_test_webhook": use_test_webhook,
            "base_url": bridge_cfg.base_url,
            "selected_path": selected_path,
            "selected_url": f"{bridge_cfg.base_url}{selected_path}",
            "payload_preview": payload if payload is not None else {},
            "retry_policy": {
                "max_retries": self.config.max_retries,
                "retry_delay_ms": self.config.retry_delay_ms,
                "require_n8n_health": self.config.require_n8n_health,
            },
        }

    def execute(
        self,
        *,
        action: str,
        intent: str,
        payload: Optional[Any] = None,
        mission_id: Optional[str] = None,
        task_id: Optional[str] = None,
        source: str = "jarvis",
        use_test_webhook: bool = False,
    ) -> Dict[str, Any]:
        bridge = get_n8n_bridge()
        preview = self.preview(
            action=action,
            intent=intent,
            payload=payload,
            mission_id=mission_id,
            task_id=task_id,
            source=source,
            use_test_webhook=use_test_webhook,
        )

        run_id = f"n8nrun-{uuid.uuid4().hex[:12]}"
        run_dir = self._ensure_dir(self.config.artifact_dir / "runs" / run_id)
        journal_path = self.config.artifact_dir / "journal.jsonl"

        request_record = {
            "run_id": run_id,
            "created_at": _utc_now(),
            "kind": "request",
            "preview": preview,
        }
        self._write_json(run_dir / "request.json", request_record)
        self._append_jsonl(journal_path, request_record)

        health = bridge.health()
        if self.config.require_n8n_health and not bool(health.get("reachable", False)):
            failure = {
                "run_id": run_id,
                "created_at": _utc_now(),
                "kind": "failure",
                "reason": "n8n_unreachable",
                "health": health,
            }
            self._write_json(run_dir / "result.json", failure)
            self._append_jsonl(journal_path, failure)
            raise RuntimeError(f"n8n is not reachable: {health}")

        attempts = []
        final_result: Optional[Dict[str, Any]] = None
        max_attempts = self.config.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            started = time.time()
            try:
                result = bridge.dispatch(
                    action=action,
                    intent=intent,
                    payload=payload if payload is not None else {},
                    mission_id=preview["mission_id"],
                    task_id=preview["task_id"],
                    source=source,
                    use_test_webhook=use_test_webhook,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "status_code": 0,
                    "error": str(exc),
                }

            result["attempt"] = attempt
            result["elapsed_ms"] = int((time.time() - started) * 1000)
            attempts.append(result)
            final_result = result

            if bool(result.get("ok", False)):
                break

            if attempt < max_attempts and self.config.retry_delay_ms > 0:
                time.sleep(self.config.retry_delay_ms / 1000.0)

        response = {
            "run_id": run_id,
            "route": "n8n",
            "created_at": _utc_now(),
            "artifact_dir": str(run_dir),
            "journal_path": str(journal_path),
            "preview": preview,
            "health": health,
            "attempt_count": len(attempts),
            "attempts": attempts,
            "final_result": final_result,
            "success": bool(final_result and final_result.get("ok", False)),
        }

        self._write_json(run_dir / "result.json", response)
        self._append_jsonl(
            journal_path,
            {
                "run_id": run_id,
                "created_at": _utc_now(),
                "kind": "result",
                "success": response["success"],
                "attempt_count": response["attempt_count"],
                "status_code": (final_result or {}).get("status_code", 0),
                "url": (final_result or {}).get("url", ""),
            },
        )

        return response


_RUNTIME: Optional[SupervisorAutomationRuntime] = None


def get_supervisor_automation_runtime() -> SupervisorAutomationRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = SupervisorAutomationRuntime()
    return _RUNTIME