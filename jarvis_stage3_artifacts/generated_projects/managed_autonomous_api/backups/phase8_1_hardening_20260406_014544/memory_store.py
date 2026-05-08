from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.time_utils import utc_now


BASE_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = BASE_DIR / "artifacts"
MEMORY_FILE = ARTIFACTS_DIR / "memory_store.json"


def _memory_template() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "missions": {},
        "runs": {},
        "notes": []
    }


def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
        return fallback
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
            return fallback
        data = json.loads(raw)
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class MemoryStore:
    def __init__(self) -> None:
        self.path = MEMORY_FILE

    def load(self) -> Dict[str, Any]:
        return _read_json(self.path, _memory_template())

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["updated_at"] = utc_now().isoformat()
        _write_json(self.path, doc)
        return doc

    def upsert_run_summary(self, run: Dict[str, Any]) -> Dict[str, Any]:
        doc = self.load()
        run_id = run["run_id"]

        summary = {
            "run_id": run_id,
            "mission_id": run.get("mission_id"),
            "task_id": run.get("task_id"),
            "objective": run.get("objective"),
            "task_type": run.get("task_type"),
            "status": run.get("status"),
            "retry_count": run.get("retry_count", 0),
            "decision": run.get("decision", {}),
            "handoff_path": run.get("handoff_path", []),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "stage_statuses": [
                {
                    "stage_name": s.get("stage_name"),
                    "agent_id": s.get("agent_id"),
                    "status": s.get("status")
                }
                for s in run.get("stages", [])
            ]
        }

        doc["runs"][run_id] = summary

        mission_id = run.get("mission_id")
        if mission_id:
            mission = doc["missions"].get(mission_id, {
                "mission_id": mission_id,
                "run_ids": [],
                "last_objective": None,
                "last_status": None,
                "updated_at": None
            })

            if run_id not in mission["run_ids"]:
                mission["run_ids"].append(run_id)

            mission["last_objective"] = run.get("objective")
            mission["last_status"] = run.get("status")
            mission["updated_at"] = utc_now().isoformat()
            doc["missions"][mission_id] = mission

        self.save(doc)
        return summary

    def add_note(self, note_type: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        doc = self.load()
        item = {
            "note_id": f"note_{len(doc['notes']) + 1:05d}",
            "type": note_type,
            "text": text,
            "metadata": metadata or {},
            "created_at": utc_now().isoformat()
        }
        doc["notes"].append(item)
        self.save(doc)
        return item

    def get_run_summary(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.load()["runs"].get(run_id)

    def get_mission_summary(self, mission_id: str) -> Optional[Dict[str, Any]]:
        return self.load()["missions"].get(mission_id)

    def list_notes(self) -> List[Dict[str, Any]]:
        return self.load()["notes"]

    def stats(self) -> Dict[str, Any]:
        doc = self.load()
        return {
            "missions_count": len(doc["missions"]),
            "runs_count": len(doc["runs"]),
            "notes_count": len(doc["notes"]),
            "updated_at": doc.get("updated_at")
        }
