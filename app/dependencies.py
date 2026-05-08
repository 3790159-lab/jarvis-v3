from __future__ import annotations

from typing import Any


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._missions: dict[str, dict[str, Any]] = {}

    async def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        return self._missions.get(mission_id)

    async def update_mission_status(
        self,
        mission_id: str,
        status: str,
        summary: str | None = None,
    ) -> None:
        mission = self._missions.get(mission_id)
        if not mission:
            return

        mission["status"] = status
        if summary is not None:
            mission["summary"] = summary

    async def append_log(
        self,
        mission_id: str,
        level: str,
        message: str,
    ) -> None:
        mission = self._missions.setdefault(mission_id, {})
        mission.setdefault("logs", [])
        mission["logs"].append(f"[{level}] {message}")

    async def save_task_results(
        self,
        mission_id: str,
        task_results: list[dict[str, Any]],
    ) -> None:
        mission = self._missions.setdefault(mission_id, {})
        mission["task_results"] = task_results

    def seed_mission(self, mission: dict[str, Any]) -> None:
        self._missions[mission["mission_id"]] = mission


_repository = InMemoryMissionRepository()


def get_mission_repository() -> InMemoryMissionRepository:
    return _repository
