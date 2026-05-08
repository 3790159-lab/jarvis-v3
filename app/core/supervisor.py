from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.infra.logger import logger
from app.memory.mission_store import mission_store
from app.services.task_service import task_service


class Supervisor:
    def analyze_goal(self, objective: str, constraints: dict[str, Any]) -> dict[str, Any]:
        objective_lower = objective.lower()

        if "youtube" in objective_lower or "shorts" in objective_lower:
            strategy = "content_automation"
            confidence = 0.86
        elif "telegram" in objective_lower or "bot" in objective_lower:
            strategy = "telegram_ai_assistant"
            confidence = 0.90
        elif "n8n" in objective_lower or "workflow" in objective_lower:
            strategy = "workflow_orchestration"
            confidence = 0.87
        elif "github" in objective_lower or "code" in objective_lower or "repo" in objective_lower:
            strategy = "code_operations"
            confidence = 0.82
        else:
            strategy = "general_supervisor"
            confidence = 0.70

        warnings: list[str] = []
        if len(objective.strip()) < 10:
            warnings.append("Objective is very short; strategy quality may be limited.")

        return {
            "strategy": strategy,
            "confidence": confidence,
            "warnings": warnings,
        }

    def create_mission(self, objective: str, constraints: dict[str, Any]) -> dict[str, Any]:
        logger.info(f"Creating mission for objective: {objective}")

        goal_id = task_service.create_goal_id()
        mission_id = task_service.create_mission_id()
        created_at = datetime.now(timezone.utc).isoformat()

        analysis = self.analyze_goal(objective, constraints)
        tasks = task_service.build_initial_tasks(objective, constraints)

        summary = (
            "Draft mission created. Supervisor analyzed the objective, "
            "selected an initial strategy, and prepared the first task chain."
        )

        result = {
            "success": True,
            "summary": summary,
            "goal_id": goal_id,
            "mission_id": mission_id,
            "objective": objective,
            "strategy": analysis["strategy"],
            "status": "draft",
            "tasks": tasks,
            "warnings": analysis["warnings"],
            "created_at": created_at,
        }

        mission_store.save_mission(
            mission_id=mission_id,
            goal_id=goal_id,
            objective=objective,
            strategy=analysis["strategy"],
            status="draft",
            summary=summary,
            constraints=constraints,
            tasks=tasks,
            warnings=analysis["warnings"],
        )
        mission_store.add_log(mission_id, "INFO", "Mission created.")
        mission_store.add_log(mission_id, "INFO", f"Strategy selected: {analysis['strategy']}")

        return result

    def get_mission(self, mission_id: str) -> dict[str, Any]:
        mission = mission_store.get_mission(mission_id)

        if mission is None:
            return {
                "found": False,
                "mission": None,
            }

        return {
            "found": True,
            "mission": mission,
        }

    def list_missions(self, limit: int = 10) -> dict[str, Any]:
        missions = mission_store.list_missions(limit=limit)
        return {
            "count": len(missions),
            "missions": missions,
        }

    def get_logs(self, mission_id: str, limit: int = 50) -> dict[str, Any]:
        mission = mission_store.get_mission(mission_id)
        if mission is None:
            return {"found": False, "logs": []}

        return {
            "found": True,
            "logs": mission_store.get_logs(mission_id, limit=limit),
        }

    def run_mission(self, mission_id: str) -> dict[str, Any]:
        mission = mission_store.get_mission(mission_id)

        if mission is None:
            return {
                "found": False,
                "ran": False,
                "mission": None,
                "message": "Mission not found",
            }

        status = mission.get("status", "draft")
        if status == "running":
            return {
                "found": True,
                "ran": False,
                "mission": mission,
                "message": "Mission is already running",
            }

        tasks = mission.get("tasks", [])
        constraints = mission.get("constraints", {})
        warnings = mission.get("warnings", [])
        objective = mission.get("objective", "")
        strategy = mission.get("strategy", "general_supervisor")
        goal_id = mission.get("goal_id", "")

        mission_store.add_log(mission_id, "INFO", "Mission run requested.")
        mission_store.add_log(mission_id, "INFO", "Mission status changed to running.")

        for task in tasks:
            task["status"] = "queued"

        mission_store.save_mission(
            mission_id=mission_id,
            goal_id=goal_id,
            objective=objective,
            strategy=strategy,
            status="running",
            summary=mission.get("summary", ""),
            constraints=constraints,
            tasks=tasks,
            warnings=warnings,
        )

        try:
            if len(tasks) >= 1:
                tasks[0]["status"] = "completed"
                tasks[0]["output"] = {
                    "result": "Goal interpreted successfully."
                }
                mission_store.add_log(mission_id, "INFO", "Task interpret_goal completed.")

            if len(tasks) >= 2:
                tasks[1]["status"] = "completed"
                tasks[1]["output"] = {
                    "result": "Execution plan prepared."
                }
                mission_store.add_log(mission_id, "INFO", "Task build_plan completed.")

            if len(tasks) >= 3:
                tasks[2]["status"] = "completed"
                tasks[2]["output"] = {
                    "result": "Execution prepared in safe mode."
                }
                mission_store.add_log(mission_id, "INFO", "Task prepare_execution completed.")

            final_summary = (
                "Mission executed in safe mode. Initial task chain completed successfully."
            )

            mission_store.save_mission(
                mission_id=mission_id,
                goal_id=goal_id,
                objective=objective,
                strategy=strategy,
                status="completed",
                summary=final_summary,
                constraints=constraints,
                tasks=tasks,
                warnings=warnings,
            )
            mission_store.add_log(mission_id, "INFO", "Mission status changed to completed.")

            updated = mission_store.get_mission(mission_id)

            return {
                "found": True,
                "ran": True,
                "mission": updated,
                "message": "Mission executed successfully in safe mode",
            }

        except Exception as e:
            logger.exception("Mission run failed")
            mission_store.add_log(mission_id, "ERROR", f"Mission run failed: {e}")

            mission_store.save_mission(
                mission_id=mission_id,
                goal_id=goal_id,
                objective=objective,
                strategy=strategy,
                status="failed",
                summary="Mission execution failed.",
                constraints=constraints,
                tasks=tasks,
                warnings=warnings + [str(e)],
            )

            failed = mission_store.get_mission(mission_id)

            return {
                "found": True,
                "ran": False,
                "mission": failed,
                "message": f"Mission execution failed: {e}",
            }


supervisor = Supervisor()
