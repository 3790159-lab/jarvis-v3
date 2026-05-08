from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        self.scheduler_jobs_file = self.root / "scheduler_jobs.json"
        self.pauses_file = self.root / "pauses.json"
        self.runtime_file = self.root / "mission_runtime.json"
        self.events_file = self.root / "event_log.jsonl"
        self.replans_file = self.root / "plan_revisions.jsonl"

        self._ensure_json_file(self.scheduler_jobs_file, [])
        self._ensure_json_file(self.pauses_file, {})
        self._ensure_json_file(self.runtime_file, {})

    def _ensure_json_file(self, path: Path, default: Any) -> None:
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_json(self, path: Path, default: Any) -> Any:
        with self._lock:
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return default

    def write_json(self, path: Path, value: Any) -> None:
        with self._lock:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---------- Scheduler jobs ----------
    def list_jobs(self) -> List[Dict[str, Any]]:
        return self.read_json(self.scheduler_jobs_file, [])

    def save_jobs(self, jobs: List[Dict[str, Any]]) -> None:
        self.write_json(self.scheduler_jobs_file, jobs)

    # ---------- Pause flags ----------
    def get_pauses(self) -> Dict[str, Any]:
        return self.read_json(self.pauses_file, {})

    def save_pauses(self, pauses: Dict[str, Any]) -> None:
        self.write_json(self.pauses_file, pauses)

    # ---------- Runtime ----------
    def get_runtime(self) -> Dict[str, Any]:
        return self.read_json(self.runtime_file, {})

    def save_runtime(self, runtime: Dict[str, Any]) -> None:
        self.write_json(self.runtime_file, runtime)

    # ---------- Events ----------
    def append_event(self, event: Dict[str, Any]) -> None:
        self.append_jsonl(self.events_file, event)

    def read_events(
        self,
        mission_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.events_file.exists():
                return []
            lines = self.events_file.read_text(encoding="utf-8").splitlines()

        result: List[Dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if mission_id and item.get("mission_id") != mission_id:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        result.reverse()
        return result

    # ---------- Replans ----------
    def append_replan(self, revision: Dict[str, Any]) -> None:
        self.append_jsonl(self.replans_file, revision)

    def read_replans(
        self,
        mission_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.replans_file.exists():
                return []
            lines = self.replans_file.read_text(encoding="utf-8").splitlines()

        result: List[Dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if mission_id and item.get("mission_id") != mission_id:
                continue
            result.append(item)
            if len(result) >= limit:
                break
        result.reverse()
        return result


def ensure_runtime_bucket(runtime: Dict[str, Any], mission_id: str) -> Dict[str, Any]:
    if mission_id not in runtime:
        runtime[mission_id] = {
            "mission_id": mission_id,
            "paused": False,
            "pause_reason": None,
            "status": "idle",
            "last_run_at": None,
            "last_event_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "run_count": 0,
            "failure_count": 0,
            "consecutive_failures": 0,
            "replan_count": 0,
            "continue_count": 0,
            "scheduled_count": 0,
            "last_replan_at": None,
            "last_continue_at": None,
            "updated_at": utc_now_iso(),
        }
    return runtime[mission_id]
