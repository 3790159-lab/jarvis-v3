from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class CodeImprovementResult:
    run_id: str
    status: str
    task: str
    changed_files: List[str]
    checks: List[Dict[str, Any]]
    lessons: List[str]
    artifacts_dir: str
    created_at: str = field(default_factory=utc_now_iso)


class JarvisCodeImprovementLane:
    """
    Safe additive code-improvement lane.

    V1 capabilities:
    - creates validation reports
    - creates service connector registry
    - creates memory learning event
    - applies known safe fixes only
    - compile-checks core modules
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "code_improvement_lane")
        self.runs_dir = ensure_dir(self.root / "runs")
        self.runtime_dir = ensure_dir(self.root / "runtime")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        ensure_dir(path.parent)
        path.write_text(text, encoding="utf-8")

    def _compile(self, rel_path: str) -> Dict[str, Any]:
        path = self.project_root / rel_path
        if not path.exists():
            return {"path": rel_path, "ok": False, "reason": "missing"}

        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return {
            "path": rel_path,
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def _ensure_service_registry(self) -> str:
        path = self.project_root / "jarvis_stage3_artifacts" / "service_registry" / "connectors.json"
        payload = {
            "version": "1.0",
            "updated_at": utc_now_iso(),
            "connectors": [
                {"name": "n8n", "status": "ready", "risk": "medium", "actions": ["create_workflow", "activate", "test_webhook"]},
                {"name": "telegram", "status": "ready", "risk": "medium", "actions": ["send_message", "operator_control"]},
                {"name": "google_sheets", "status": "planned", "risk": "medium", "actions": ["append_rows", "read_table", "update_cells"]},
                {"name": "gmail", "status": "planned", "risk": "medium", "actions": ["summarize", "draft", "send_with_approval"]},
                {"name": "calendar", "status": "planned", "risk": "medium", "actions": ["read", "create_event_with_approval"]},
                {"name": "http_api", "status": "ready", "risk": "medium", "actions": ["GET", "POST", "validate_response"]},
                {"name": "local_filesystem", "status": "guarded", "risk": "medium", "actions": ["allowlisted_read", "allowlisted_write"]},
            ],
        }
        self._write_json(path, payload)
        return str(path)

    def _ensure_text_safety_module(self) -> str:
        path = self.project_root / "app" / "services" / "jarvis_text_safety.py"
        content = '''from __future__ import annotations

def ascii_safe_summary(text: str) -> str:
    """Return stable UTF-8/console-safe summary fallback."""
    if text is None:
        return ""
    return str(text).encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def compact_status(status: str, workflow_id: str | None = None) -> str:
    workflow = workflow_id or "none"
    return f"status={status}; workflow_id={workflow}"
'''
        self._write_text(path, content)
        return str(path)

    def _fix_night_v6_safe_result_handling(self) -> Dict[str, Any]:
        path = self.project_root / "scripts" / "jarvis_night_mode_v6_brain_loop.ps1"
        if not path.exists():
            return {"changed": False, "reason": "night_v6_script_missing"}

        raw = path.read_text(encoding="utf-8", errors="replace")
        dangerous = '$WorkflowId = $Result.execution.primary_result.workflow_id'

        if dangerous not in raw:
            return {"changed": False, "reason": "already_safe_or_pattern_missing"}

        old = '''        $Status = $Result.execution.status
        $WorkflowId = $Result.execution.primary_result.workflow_id
        $WorkflowStatus = $Result.execution.primary_result.status

        Log "Iteration $i completed: status=$Status workflow=$WorkflowId workflow_status=$WorkflowStatus"
'''
        new = '''        $Status = "unknown"
        $WorkflowId = $null
        $WorkflowStatus = $null

        if ($null -ne $Result -and $null -ne $Result.execution) {
            $Status = $Result.execution.status

            if ($null -ne $Result.execution.primary_result) {
                $WorkflowId = $Result.execution.primary_result.workflow_id
                $WorkflowStatus = $Result.execution.primary_result.status
            }
        }

        if (-not $WorkflowId) { $WorkflowId = "none" }
        if (-not $WorkflowStatus) { $WorkflowStatus = "none" }

        Log "Iteration $i completed: status=$Status workflow=$WorkflowId workflow_status=$WorkflowStatus"
'''
        raw = raw.replace(old, new)
        path.write_text(raw, encoding="utf-8")
        return {"changed": True, "path": str(path)}

    def run(self, task: str) -> CodeImprovementResult:
        run_id = "codefix_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        run_dir = ensure_dir(self.runs_dir / run_id)

        changed_files: List[str] = []
        checks: List[Dict[str, Any]] = []
        lessons: List[str] = []

        registry = self._ensure_service_registry()
        changed_files.append(registry)
        lessons.append("Service connector registry was created/updated.")

        text_safety = self._ensure_text_safety_module()
        changed_files.append(text_safety)
        lessons.append("Text safety helper module was created/updated.")

        fix = self._fix_night_v6_safe_result_handling()
        checks.append({"name": "night_v6_safe_result_handling", **fix})
        if fix.get("changed"):
            changed_files.append(fix["path"])
            lessons.append("Night V6 result handling was made null-safe.")
        else:
            lessons.append("Night V6 result handling was already safe or no exact pattern was found.")

        for rel in [
            "app/services/jarvis_code_improvement_lane.py",
            "app/services/jarvis_brain_executor.py",
            "app/services/jarvis_brain_foundation.py",
            "app/services/jarvis_n8n_super_agent.py",
            "app/services/jarvis_text_safety.py",
        ]:
            checks.append({"name": "py_compile", **self._compile(rel)})

        ok = all(c.get("ok", True) for c in checks if c.get("name") == "py_compile")
        status = "completed" if ok else "failed_validation"

        result = CodeImprovementResult(
            run_id=run_id,
            status=status,
            task=task,
            changed_files=changed_files,
            checks=checks,
            lessons=lessons,
            artifacts_dir=str(run_dir),
        )

        self._write_json(run_dir / "result.json", asdict(result))
        self._write_json(self.runtime_dir / "latest_result.json", asdict(result))

        learning_path = self.project_root / "jarvis_stage3_artifacts" / "memory_learning" / "code_improvement_lessons.jsonl"
        ensure_dir(learning_path.parent)
        with learning_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "run_id": run_id,
                "task": task,
                "status": status,
                "lessons": lessons,
                "created_at": utc_now_iso(),
            }, ensure_ascii=False) + "\n")

        return result