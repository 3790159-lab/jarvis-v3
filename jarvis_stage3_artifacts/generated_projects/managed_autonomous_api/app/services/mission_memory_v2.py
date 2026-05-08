from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MissionMemoryV2Service:
    MAX_HISTORY_PER_TYPE = 100
    MAX_PATTERNS_PER_TYPE = 50
    MAX_FAILURES_PER_TYPE = 50

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.base_dir / "mission_memory_v2.json"
        self._lock = threading.RLock()

    def _default_store(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "mission_types": {}
        }

    def _write_store(self, store: Dict[str, Any]) -> None:
        store["updated_at"] = utc_now()
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    def _read_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            store = self._default_store()
            self._write_store(store)
            return store

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Store must be a JSON object")
            if "mission_types" not in data or not isinstance(data["mission_types"], dict):
                data["mission_types"] = {}
            if "created_at" not in data:
                data["created_at"] = utc_now()
            if "updated_at" not in data:
                data["updated_at"] = utc_now()
            return data
        except Exception:
            store = self._default_store()
            self._write_store(store)
            return store

    def _get_type_bucket(self, store: Dict[str, Any], mission_type: str) -> Dict[str, Any]:
        types = store["mission_types"]
        if mission_type not in types:
            types[mission_type] = {
                "mission_type": mission_type,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "stats": {
                    "success_count": 0,
                    "failure_count": 0,
                    "known_good_count": 0,
                    "failure_signature_count": 0,
                },
                "known_good_plans": [],
                "reusable_patterns": [],
                "failure_signatures": [],
                "history": [],
            }
        return types[mission_type]

    def health(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            return {
                "status": "healthy",
                "store_path": str(self.store_path),
                "mission_types_count": len(store["mission_types"]),
                "updated_at": store["updated_at"],
            }

    def record_success(
        self,
        mission_type: str,
        mission_id: str,
        plan_summary: str,
        reusable_pattern: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            bucket = self._get_type_bucket(store, mission_type)

            good_plan = {
                "mission_id": mission_id,
                "plan_summary": plan_summary,
                "reusable_pattern": reusable_pattern,
                "notes": notes,
                "recorded_at": utc_now(),
            }
            bucket["known_good_plans"].append(good_plan)
            if len(bucket["known_good_plans"]) > self.MAX_PATTERNS_PER_TYPE:
                bucket["known_good_plans"] = bucket["known_good_plans"][-self.MAX_PATTERNS_PER_TYPE:]

            if reusable_pattern and reusable_pattern not in bucket["reusable_patterns"]:
                bucket["reusable_patterns"].append(reusable_pattern)
                if len(bucket["reusable_patterns"]) > self.MAX_PATTERNS_PER_TYPE:
                    bucket["reusable_patterns"] = bucket["reusable_patterns"][-self.MAX_PATTERNS_PER_TYPE:]

            bucket["history"].append({
                "ts": utc_now(),
                "mission_id": mission_id,
                "outcome": "success",
                "plan_summary": plan_summary,
                "notes": notes,
            })
            if len(bucket["history"]) > self.MAX_HISTORY_PER_TYPE:
                bucket["history"] = bucket["history"][-self.MAX_HISTORY_PER_TYPE:]

            bucket["stats"]["success_count"] += 1
            bucket["stats"]["known_good_count"] = len(bucket["known_good_plans"])
            bucket["updated_at"] = utc_now()

            self._write_store(store)
            return deepcopy(bucket)

    def record_failure(
        self,
        mission_type: str,
        mission_id: str,
        failure_signature: str,
        failed_stage: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            bucket = self._get_type_bucket(store, mission_type)

            failure_item = {
                "mission_id": mission_id,
                "failure_signature": failure_signature,
                "failed_stage": failed_stage,
                "notes": notes,
                "recorded_at": utc_now(),
            }
            bucket["failure_signatures"].append(failure_item)
            if len(bucket["failure_signatures"]) > self.MAX_FAILURES_PER_TYPE:
                bucket["failure_signatures"] = bucket["failure_signatures"][-self.MAX_FAILURES_PER_TYPE:]

            bucket["history"].append({
                "ts": utc_now(),
                "mission_id": mission_id,
                "outcome": "failure",
                "failed_stage": failed_stage,
                "failure_signature": failure_signature,
                "notes": notes,
            })
            if len(bucket["history"]) > self.MAX_HISTORY_PER_TYPE:
                bucket["history"] = bucket["history"][-self.MAX_HISTORY_PER_TYPE:]

            bucket["stats"]["failure_count"] += 1
            bucket["stats"]["failure_signature_count"] = len(bucket["failure_signatures"])
            bucket["updated_at"] = utc_now()

            self._write_store(store)
            return deepcopy(bucket)

    def get_mission_type(self, mission_type: str) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            bucket = store["mission_types"].get(mission_type)
            if not bucket:
                return {
                    "found": False,
                    "mission_type": mission_type,
                    "message": "Mission type memory not found",
                }
            return deepcopy(bucket)

    def list_mission_types(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            items: List[Dict[str, Any]] = list(store["mission_types"].values())
            items.sort(
                key=lambda x: (
                    -(int(x["stats"].get("success_count", 0)) + int(x["stats"].get("failure_count", 0))),
                    x.get("mission_type", "")
                )
            )
            return {
                "count": len(items),
                "items": deepcopy(items),
            }

    def suggest(self, mission_type: str) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            bucket = self._get_type_bucket(store, mission_type)

            latest_good = bucket["known_good_plans"][-3:] if bucket["known_good_plans"] else []
            recent_failures = bucket["failure_signatures"][-3:] if bucket["failure_signatures"] else []

            avoid_signatures = [x["failure_signature"] for x in recent_failures if x.get("failure_signature")]
            reuse_patterns = bucket["reusable_patterns"][-5:] if bucket["reusable_patterns"] else []

            return {
                "mission_type": mission_type,
                "suggestion": {
                    "reuse_patterns": reuse_patterns,
                    "known_good_plans": latest_good,
                    "avoid_failure_signatures": avoid_signatures,
                },
                "stats": deepcopy(bucket["stats"]),
            }


service = MissionMemoryV2Service(Path(__file__).resolve().parents[2] / "artifacts" / "mission_memory_v2")
