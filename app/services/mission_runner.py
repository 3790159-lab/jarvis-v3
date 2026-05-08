from __future__ import annotations

from typing import Any

from app.schemas.execution import MissionExecutionRequest, MissionExecutionResult
from app.services.executor import ExecutorService
from app.services.mission_lock import mission_lock_manager


class MissionRepositoryProtocol:
    async def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        ...

    async def update_mission_status(
        self,
        mission_id: str,
        status: str,
        summary: str | None = None,
    ) -> None:
        ...

    async def append_log(
        self,
        mission_id: str,
        level: str,
        message: str,
    ) -> None:
        ...

    async def save_task_results(
        self,
        mission_id: str,
        task_results: list[dict[str, Any]],
    ) -> None:
        ...


class MissionRunnerService:
    def __init__(self, repository: MissionRepositoryProtocol) -> None:
        self.repository = repository
        self.executor = ExecutorService()

    async def run_mission(self, mission_id: str) -> MissionExecutionResult:
        lock = mission_lock_manager.get_lock(mission_id)

        if lock.locked():
            raise RuntimeError(f"Mission '{mission_id}' is already running")

        async with lock:
            mission = await self.repository.get_mission(mission_id)
            if not mission:
                raise ValueError(f"Mission '{mission_id}' not found")

            await self.repository.append_log(mission_id, "INFO", "Mission run requested.")
            await self.repository.update_mission_status(mission_id, "running")
            await self.repository.append_log(mission_id, "INFO", "Mission status changed to running.")

            request = MissionExecutionRequest(
                mission_id=mission["mission_id"],
                objective=mission["objective"],
                tasks=mission["tasks"],
                context=mission.get("context", {}),
            )

            result = await self.executor.execute_mission(request)

            await self.repository.save_task_results(
                mission_id,
                [item.model_dump() for item in result.task_results],
            )

            for task_result in result.task_results:
                level = "INFO" if task_result.status.value == "completed" else "ERROR"
                await self.repository.append_log(
                    mission_id,
                    level,
                    f"Task {task_result.task_id} finished with status {task_result.status.value}. {task_result.message}",
                )

            await self.repository.update_mission_status(
                mission_id,
                result.status.value,
                result.summary,
            )
            await self.repository.append_log(
                mission_id,
                "INFO",
                f"Mission status changed to {result.status.value}.",
            )

            return result
