from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import utc_now_iso


class MissionRegistry:
    def __init__(self, store: Any, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.registry_file = self.store.root / "mission_registry.json"
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        if not self.registry_file.exists():
            self.store.write_json(
                self.registry_file,
                {
                    "missions": [
                        {
                            "mission_id": "mission_custom_001",
                            "mission_type": "generic_supervisor_mission",
                            "entrypoint": "mission_bridge",
                            "execution_mode": "supervised",
                            "supports_resume": True,
                            "supports_repair": True,
                            "verification_policy": "basic_completion",
                            "updated_at": utc_now_iso(),
                        }
                    ]
                },
            )

    def list_missions(self) -> List[Dict[str, Any]]:
        self._ensure_registry()
        data = self.store.read_json(self.registry_file, {"missions": []})
        return data.get("missions", [])

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        for item in self.list_missions():
            if item.get("mission_id") == mission_id:
                return item
        return None

    def upsert_mission(self, record: Dict[str, Any]) -> Dict[str, Any]:
        missions = self.list_missions()
        found = False
        for idx, item in enumerate(missions):
            if item.get("mission_id") == record.get("mission_id"):
                record["updated_at"] = utc_now_iso()
                missions[idx] = record
                found = True
                break
        if not found:
            record["updated_at"] = utc_now_iso()
            missions.append(record)

        self.store.write_json(self.registry_file, {"missions": missions})
        self.event_bus.publish(
            "mission_registry_updated",
            mission_id=record.get("mission_id"),
            payload={"mission": record},
            source="mission_registry",
        )
        return record
