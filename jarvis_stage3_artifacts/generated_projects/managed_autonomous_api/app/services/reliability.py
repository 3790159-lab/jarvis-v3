from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.time_utils import utc_now
from app.services.role_router import choose_agent, build_handoff_path


BASE_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = BASE_DIR / "artifacts"
JOURNAL_FILE = ARTIFACTS_DIR / "execution_journal.json"
IDEMPOTENCY_FILE = ARTIFACTS_DIR / "idempotency_index.json"

RUN_STATUSES = {"created", "running", "retrying", "completed", "failed", "quarantined"}
STAGE_STATUSES = {"pending", "running", "completed", "failed", "blocked"}
ALLOWED_STAGE_TRANSITIONS = {
    "pending": {"running", "blocked", "completed", "failed"},
    "running": {"completed", "failed", "blocked"},
    "completed": set(),
    "failed": {"running", "blocked"},
    "blocked": {"running"}
}
MAX_RETRY_COUNT = 3


def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
        return fallback
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            path.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
            return fallback
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _journal_template() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "runs": []
    }


def _idempotency_template() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "keys": {}
    }


def build_idempotency_key(task_id: str, objective: str, requested_tools: List[str], task_type: Optional[str]) -> str:
    normalized = json.dumps({
        "task_id": task_id,
        "objective": objective.strip().lower(),
        "requested_tools": sorted(requested_tools),
        "task_type": (task_type or "").strip().lower()
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _derive_run_status(stages: List[Dict[str, Any]], current_status: str) -> str:
    stage_statuses = [stage["status"] for stage in stages]

    if current_status == "quarantined":
        return "quarantined"

    if all(status == "completed" for status in stage_statuses):
        return "completed"

    if any(status == "running" for status in stage_statuses):
        return "running"

    if any(status == "failed" for status in stage_statuses):
        return "failed"

    if any(status == "blocked" for status in stage_statuses):
        return "retrying" if current_status == "retrying" else "failed"

    if any(status == "completed" for status in stage_statuses):
        return "running"

    return current_status


class ReliabilityManager:
    def __init__(self) -> None:
        self.journal_file = JOURNAL_FILE
        self.idempotency_file = IDEMPOTENCY_FILE

    def load_journal(self) -> Dict[str, Any]:
        return _read_json(self.journal_file, _journal_template())

    def save_journal(self, journal: Dict[str, Any]) -> Dict[str, Any]:
        journal["updated_at"] = utc_now().isoformat()
        _write_json(self.journal_file, journal)
        return journal

    def load_idempotency(self) -> Dict[str, Any]:
        return _read_json(self.idempotency_file, _idempotency_template())

    def save_idempotency(self, index: Dict[str, Any]) -> Dict[str, Any]:
        index["updated_at"] = utc_now().isoformat()
        _write_json(self.idempotency_file, index)
        return index

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        journal = self.load_journal()
        for run in journal["runs"]:
            if run["run_id"] == run_id:
                return run
        return None

    def list_runs(self) -> List[Dict[str, Any]]:
        return self.load_journal()["runs"]

    def append_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        journal = self.load_journal()
        replaced = False
        new_runs = []
        for item in journal["runs"]:
            if item["run_id"] == run["run_id"]:
                new_runs.append(run)
                replaced = True
            else:
                new_runs.append(item)
        if not replaced:
            new_runs.append(run)
        journal["runs"] = new_runs
        self.save_journal(journal)
        return run

    def mark_stage(self, run_id: str, stage_name: str, status: str, note: Optional[str] = None) -> Dict[str, Any]:
        if status not in STAGE_STATUSES:
            raise ValueError(f"Invalid stage status: {status}")

        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        found = False

        for stage in run["stages"]:
            if stage["stage_name"] == stage_name:
                found = True
                current_status = stage["status"]
                allowed = ALLOWED_STAGE_TRANSITIONS.get(current_status, set())
                if status not in allowed:
                    raise ValueError(f"Illegal stage transition: {current_status} -> {status} for {stage_name}")

                stage["status"] = status
                stage["updated_at"] = utc_now().isoformat()
                if note:
                    stage["note"] = note

        if not found:
            raise ValueError(f"Stage not found: {stage_name}")

        run["status"] = _derive_run_status(run["stages"], run["status"])
        run["updated_at"] = utc_now().isoformat()
        self.append_run(run)
        return run

    def quarantine_run(self, run_id: str, reason: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        run["status"] = "quarantined"
        run["quarantine_reason"] = reason
        run["updated_at"] = utc_now().isoformat()

        for stage in run["stages"]:
            if stage["status"] in {"pending", "running"}:
                stage["status"] = "blocked"
                stage["updated_at"] = utc_now().isoformat()
                stage["note"] = "blocked_by_quarantine"

        self.append_run(run)
        return run

    def create_retry(self, run_id: str, reason: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        current_retry_count = run.get("retry_count", 0)
        if current_retry_count >= MAX_RETRY_COUNT:
            raise ValueError(f"Retry limit reached for run: {run_id}")

        run["retry_count"] = current_retry_count + 1
        run["last_retry_reason"] = reason
        run["status"] = "retrying"
        run["updated_at"] = utc_now().isoformat()

        reopened = False
        for stage in run["stages"]:
            if stage["status"] in {"failed", "blocked"} and not reopened:
                stage["status"] = "running"
                stage["updated_at"] = utc_now().isoformat()
                stage["note"] = "reopened_by_retry"
                reopened = True

        if not reopened:
            for stage in run["stages"]:
                if stage["status"] == "pending":
                    stage["status"] = "running"
                    stage["updated_at"] = utc_now().isoformat()
                    stage["note"] = "retry_started_from_pending"
                    reopened = True
                    break

        self.append_run(run)
        return run

    def start_reliable_run(
        self,
        task_id: str,
        objective: str,
        requested_tools: List[str],
        task_type: Optional[str],
        mission_id: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
        input_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        constraints = constraints or {}
        input_payload = input_payload or {}

        idem_key = build_idempotency_key(task_id, objective, requested_tools, task_type)
        idem = self.load_idempotency()

        if idem_key in idem["keys"]:
            existing_run_id = idem["keys"][idem_key]
            existing = self.get_run(existing_run_id)
            return {
                "status": "duplicate",
                "idempotency_key": idem_key,
                "run": existing
            }

        decision = choose_agent(
            objective=objective,
            requested_tools=requested_tools,
            task_type=task_type,
            input_payload=input_payload
        )

        path = build_handoff_path(decision["agent_id"])
        run_id = hashlib.sha256(f"{task_id}:{objective}:{utc_now().isoformat()}".encode("utf-8")).hexdigest()[:20]

        stages = []
        for idx, agent_id in enumerate(path, start=1):
            initial_status = "running" if idx == 1 else "pending"
            stages.append({
                "order": idx,
                "agent_id": agent_id,
                "stage_name": f"stage_{idx}",
                "status": initial_status,
                "created_at": utc_now().isoformat(),
                "updated_at": utc_now().isoformat(),
                "rollback_marker": f"rb_{run_id}_{idx}"
            })

        run = {
            "run_id": run_id,
            "mission_id": mission_id,
            "task_id": task_id,
            "objective": objective,
            "task_type": task_type,
            "requested_tools": requested_tools,
            "constraints": constraints,
            "input_payload": input_payload,
            "decision": decision,
            "handoff_path": path,
            "status": "running",
            "retry_count": 0,
            "created_at": utc_now().isoformat(),
            "updated_at": utc_now().isoformat(),
            "idempotency_key": idem_key,
            "stages": stages
        }

        self.append_run(run)

        idem["keys"][idem_key] = run_id
        self.save_idempotency(idem)

        return {
            "status": "ok",
            "idempotency_key": idem_key,
            "run": run
        }
