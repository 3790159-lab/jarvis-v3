from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionOrchestrator:
    def __init__(self, runtime_dir: str | Path):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._global_lock = threading.RLock()
        self._threads: Dict[str, threading.Thread] = {}

    def _sanitize_mission_id(self, mission_id: str) -> str:
        safe = "".join(ch for ch in mission_id if ch.isalnum() or ch in ("-", "_"))
        return safe or "mission_default"

    def _mission_path(self, mission_id: str) -> Path:
        safe_id = self._sanitize_mission_id(mission_id)
        return self.runtime_dir / f"{safe_id}.json"

    def _default_journal(self, mission_id: str) -> Dict[str, Any]:
        return {
            "mission_id": mission_id,
            "status": "idle",
            "current_stage": None,
            "requested_action": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "stage_index": -1,
            "run_count": 0,
            "last_error": None,
            "stages": [
                {"name": "plan_mission", "status": "pending", "started_at": None, "finished_at": None, "message": None},
                {"name": "dispatch_stage", "status": "pending", "started_at": None, "finished_at": None, "message": None},
                {"name": "execute_stage", "status": "pending", "started_at": None, "finished_at": None, "message": None},
                {"name": "qa_stage_result", "status": "pending", "started_at": None, "finished_at": None, "message": None},
                {"name": "complete_run", "status": "pending", "started_at": None, "finished_at": None, "message": None}
            ],
            "events": [],
            "result": None
        }

    def _repair_journal(self, mission_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        default = self._default_journal(mission_id)
        if not isinstance(data, dict):
            return default

        for key, value in default.items():
            if key not in data:
                data[key] = deepcopy(value)

        if not isinstance(data.get("stages"), list) or not data["stages"]:
            data["stages"] = deepcopy(default["stages"])

        if not isinstance(data.get("events"), list):
            data["events"] = []

        return data

    def _write_journal(self, mission_id: str, journal: Dict[str, Any]) -> None:
        path = self._mission_path(mission_id)
        journal["updated_at"] = utc_now()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _read_journal(self, mission_id: str) -> Dict[str, Any]:
        path = self._mission_path(mission_id)
        if not path.exists():
            journal = self._default_journal(mission_id)
            self._write_journal(mission_id, journal)
            return journal

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            repaired = self._repair_journal(mission_id, data)
            if repaired != data:
                self._write_journal(mission_id, repaired)
            return repaired
        except Exception as exc:
            broken = self._default_journal(mission_id)
            broken["status"] = "failed"
            broken["finished_at"] = utc_now()
            broken["last_error"] = f"Journal recovery error: {exc}"
            broken["events"].append({
                "ts": utc_now(),
                "level": "error",
                "message": "Journal could not be parsed; state was reset",
                "extra": {"error": str(exc)}
            })
            self._write_journal(mission_id, broken)
            return broken

    def _append_event(self, journal: Dict[str, Any], level: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        event = {
            "ts": utc_now(),
            "level": level,
            "message": message
        }
        if extra:
            event["extra"] = extra
        journal["events"].append(event)

        if len(journal["events"]) > 200:
            journal["events"] = journal["events"][-200:]

    def get_journal(self, mission_id: str) -> Dict[str, Any]:
        with self._global_lock:
            return deepcopy(self._read_journal(mission_id))

    def health(self) -> Dict[str, Any]:
        with self._global_lock:
            files = list(self.runtime_dir.glob("*.json"))
            return {
                "status": "healthy",
                "runtime_dir": str(self.runtime_dir),
                "journal_files": len(files),
                "active_threads": len([t for t in self._threads.values() if t.is_alive()])
            }

    def request_action(self, mission_id: str, action: str) -> Dict[str, Any]:
        if action not in {"stop", "resume", "cancel"}:
            raise ValueError(f"Unsupported action: {action}")

        with self._global_lock:
            journal = self._read_journal(mission_id)

            if action == "resume" and journal["status"] not in {"paused", "idle"}:
                self._append_event(journal, "warning", "Resume ignored because mission is not paused or idle")
                self._write_journal(mission_id, journal)
                return deepcopy(journal)

            journal["requested_action"] = action
            self._append_event(journal, "info", f"Requested action: {action}")
            self._write_journal(mission_id, journal)
            return deepcopy(journal)

    def _reset_for_new_run(self, journal: Dict[str, Any]) -> None:
        journal["status"] = "running"
        journal["current_stage"] = None
        journal["requested_action"] = None
        journal["finished_at"] = None
        journal["last_error"] = None
        journal["result"] = None
        journal["run_count"] = int(journal.get("run_count", 0)) + 1
        journal["stage_index"] = -1
        for stage in journal["stages"]:
            stage["status"] = "pending"
            stage["started_at"] = None
            stage["finished_at"] = None
            stage["message"] = None

    def start_run(self, mission_id: str) -> Dict[str, Any]:
        with self._global_lock:
            journal = self._read_journal(mission_id)
            mission_id = self._sanitize_mission_id(mission_id)

            existing_thread = self._threads.get(mission_id)
            if existing_thread and existing_thread.is_alive():
                return deepcopy(journal)

            self._reset_for_new_run(journal)
            journal["started_at"] = utc_now()
            self._append_event(journal, "info", "Mission run started")
            self._write_journal(mission_id, journal)

            thread = threading.Thread(
                target=self._run_worker,
                args=(mission_id,),
                daemon=True,
                name=f"mission-runner-{mission_id}"
            )
            self._threads[mission_id] = thread
            thread.start()
            return deepcopy(journal)

    def resume_run(self, mission_id: str) -> Dict[str, Any]:
        with self._global_lock:
            journal = self._read_journal(mission_id)
            mission_id = self._sanitize_mission_id(mission_id)

            existing_thread = self._threads.get(mission_id)
            if existing_thread and existing_thread.is_alive():
                journal["requested_action"] = "resume"
                self._append_event(journal, "info", "Resume signaled to existing worker")
                self._write_journal(mission_id, journal)
                return deepcopy(journal)

            if journal["status"] not in {"paused", "idle"}:
                self._append_event(journal, "warning", "Resume ignored because mission is not paused or idle")
                self._write_journal(mission_id, journal)
                return deepcopy(journal)

            journal["status"] = "running"
            journal["requested_action"] = None
            self._append_event(journal, "info", "Mission resumed")
            self._write_journal(mission_id, journal)

            thread = threading.Thread(
                target=self._run_worker,
                args=(mission_id,),
                daemon=True,
                name=f"mission-runner-{mission_id}"
            )
            self._threads[mission_id] = thread
            thread.start()
            return deepcopy(journal)

    def _set_stage_status(self, journal: Dict[str, Any], idx: int, status: str, message: Optional[str] = None) -> None:
        stage = journal["stages"][idx]
        stage["status"] = status

        if status == "running" and not stage["started_at"]:
            stage["started_at"] = utc_now()

        if status in {"completed", "failed", "cancelled", "skipped"}:
            stage["finished_at"] = utc_now()

        if message is not None:
            stage["message"] = message

        journal["stage_index"] = idx
        journal["current_stage"] = stage["name"]

    def _handle_control_flags(self, mission_id: str, journal: Dict[str, Any]) -> Optional[str]:
        action = journal.get("requested_action")

        if action == "cancel":
            journal["status"] = "cancelled"
            journal["finished_at"] = utc_now()
            self._append_event(journal, "warning", "Mission cancelled by operator")
            return "cancelled"

        if action == "stop":
            journal["status"] = "paused"
            journal["requested_action"] = None
            self._append_event(journal, "warning", "Mission paused by operator")
            self._write_journal(mission_id, journal)

            while True:
                time.sleep(1.0)
                with self._global_lock:
                    latest = self._read_journal(mission_id)
                    latest_action = latest.get("requested_action")

                    if latest_action == "cancel":
                        latest["status"] = "cancelled"
                        latest["finished_at"] = utc_now()
                        latest["requested_action"] = None
                        self._append_event(latest, "warning", "Mission cancelled while paused")
                        self._write_journal(mission_id, latest)
                        journal.clear()
                        journal.update(latest)
                        return "cancelled"

                    if latest_action == "resume":
                        latest["status"] = "running"
                        latest["requested_action"] = None
                        self._append_event(latest, "info", "Mission resumed by operator")
                        self._write_journal(mission_id, latest)
                        journal.clear()
                        journal.update(latest)
                        return "resumed"

        return None

    def _simulate_stage_work(self, stage_name: str) -> str:
        if stage_name == "plan_mission":
            time.sleep(1.0)
            return "Mission plan loaded and execution path prepared"
        if stage_name == "dispatch_stage":
            time.sleep(0.8)
            return "Dispatcher selected next executable stage"
        if stage_name == "execute_stage":
            time.sleep(1.5)
            return "Executor finished stage operation successfully"
        if stage_name == "qa_stage_result":
            time.sleep(0.8)
            return "QA validated the stage result"
        if stage_name == "complete_run":
            time.sleep(0.6)
            return "Completion logic finalized mission state"
        time.sleep(0.5)
        return "Stage processed"

    def _run_worker(self, mission_id: str) -> None:
        try:
            with self._global_lock:
                journal = self._read_journal(mission_id)
                start_idx = int(journal.get("stage_index", -1)) + 1

            for idx in range(start_idx, len(journal["stages"])):
                with self._global_lock:
                    journal = self._read_journal(mission_id)

                    if journal["status"] in {"completed", "cancelled", "failed"}:
                        return

                    control = self._handle_control_flags(mission_id, journal)
                    if control == "cancelled":
                        self._write_journal(mission_id, journal)
                        return

                    if journal["stages"][idx]["status"] == "completed":
                        continue

                    self._set_stage_status(journal, idx, "running")
                    self._append_event(journal, "info", f"Stage started: {journal['stages'][idx]['name']}")
                    self._write_journal(mission_id, journal)

                stage_name = journal["stages"][idx]["name"]

                try:
                    message = self._simulate_stage_work(stage_name)
                except Exception as exc:
                    with self._global_lock:
                        fail_journal = self._read_journal(mission_id)
                        self._set_stage_status(fail_journal, idx, "failed", str(exc))
                        fail_journal["status"] = "failed"
                        fail_journal["finished_at"] = utc_now()
                        fail_journal["last_error"] = str(exc)
                        self._append_event(fail_journal, "error", f"Stage failed: {stage_name}", {"error": str(exc)})
                        self._write_journal(mission_id, fail_journal)
                    return

                with self._global_lock:
                    latest = self._read_journal(mission_id)
                    control = self._handle_control_flags(mission_id, latest)
                    if control == "cancelled":
                        self._write_journal(mission_id, latest)
                        return

                    self._set_stage_status(latest, idx, "completed", message)
                    self._append_event(latest, "info", f"Stage completed: {stage_name}", {"message": message})
                    self._write_journal(mission_id, latest)

            with self._global_lock:
                final = self._read_journal(mission_id)
                if final["status"] not in {"cancelled", "failed"}:
                    final["status"] = "completed"
                    final["finished_at"] = utc_now()
                    final["current_stage"] = "complete_run"
                    final["result"] = {
                        "message": "Mission finished successfully",
                        "completed_stages": [s["name"] for s in final["stages"] if s["status"] == "completed"],
                        "failed_stages": [s["name"] for s in final["stages"] if s["status"] == "failed"]
                    }
                    self._append_event(final, "info", "Mission completed successfully")
                    self._write_journal(mission_id, final)

        except Exception as exc:
            with self._global_lock:
                fail = self._read_journal(mission_id)
                fail["status"] = "failed"
                fail["finished_at"] = utc_now()
                fail["last_error"] = str(exc)
                self._append_event(fail, "error", "Unhandled orchestrator error", {"error": str(exc)})
                self._write_journal(mission_id, fail)


_RUNTIME_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "execution_runtime"
orchestrator = ExecutionOrchestrator(_RUNTIME_DIR)
