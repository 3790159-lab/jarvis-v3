from __future__ import annotations

from app.dependencies import get_mission_repository
from app.schemas.execution import TaskType


def seed_demo_mission() -> None:
    repository = get_mission_repository()

    mission = {
        "mission_id": "mission_demo_001",
        "objective": "Test execution pipeline",
        "status": "pending",
        "summary": "",
        "context": {
            "source": "demo",
            "project": "jarvis_v3",
        },
        "tasks": [
            {
                "task_id": "health_check",
                "title": "Check local backend health",
                "type": TaskType.SHELL_COMMAND.value,
                "enabled": True,
                "depends_on": [],
                "timeout_seconds": 20,
                "max_retries": 1,
                "continue_on_error": True,
                "parallel_group": None,
                "payload": {
                    "command": "powershell -Command ""Write-Output Hello_From_Jarvis"""
                },
            },
            {
                "task_id": "write_file",
                "title": "Write test artifact",
                "type": TaskType.FILE_WRITE.value,
                "enabled": True,
                "depends_on": ["health_check"],
                "timeout_seconds": 20,
                "max_retries": 1,
                "continue_on_error": False,
                "parallel_group": None,
                "payload": {
                    "path": "artifacts/mission_demo.txt",
                    "content": "Mission demo file created successfully."
                },
            },
        ],
    }

    repository.seed_mission(mission)
