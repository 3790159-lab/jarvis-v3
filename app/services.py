from __future__ import annotations

import uuid

from app.models import (
    GoalCreate,
    GoalRecord,
    IntakePlan,
    IntakeRequest,
    IntakeResponse,
    MissionCreate,
    MissionRecord,
    TaskCreate,
    TaskRecord,
    TaskStatus,
    utc_now_iso,
)
from app.planner import SimplePlanner
from app.state_store import StateStore


class SupervisorService:
    def __init__(self, store: StateStore | None = None, planner: SimplePlanner | None = None) -> None:
        self.store = store or StateStore()
        self.planner = planner or SimplePlanner()

    def create_goal(self, payload: GoalCreate) -> GoalRecord:
        goal_id = f"goal_{uuid.uuid4().hex[:8]}"
        created_at = utc_now_iso()
        self.store.insert_goal(
            goal_id=goal_id,
            objective=payload.objective,
            constraints=payload.constraints,
            created_at=created_at,
        )
        return GoalRecord(goal_id=goal_id, created_at=created_at, **payload.model_dump())

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        row = self.store.get_goal(goal_id)
        return GoalRecord(**row) if row else None

    def list_goals(self) -> list[GoalRecord]:
        return [GoalRecord(**row) for row in self.store.list_goals()]

    def create_mission(self, payload: MissionCreate) -> MissionRecord:
        goal = self.store.get_goal(payload.goal_id)
        if not goal:
            raise ValueError(f"Goal not found: {payload.goal_id}")

        mission_id = f"mission_{uuid.uuid4().hex[:8]}"
        created_at = utc_now_iso()
        self.store.insert_mission(
            mission_id=mission_id,
            goal_id=payload.goal_id,
            objective=payload.objective,
            constraints=payload.constraints,
            created_at=created_at,
        )
        return MissionRecord(mission_id=mission_id, created_at=created_at, **payload.model_dump())

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        row = self.store.get_mission(mission_id)
        return MissionRecord(**row) if row else None

    def list_missions(self, goal_id: str | None = None) -> list[MissionRecord]:
        return [MissionRecord(**row) for row in self.store.list_missions(goal_id=goal_id)]

    def create_task(self, payload: TaskCreate) -> TaskRecord:
        mission = self.store.get_mission(payload.mission_id)
        if not mission:
            raise ValueError(f"Mission not found: {payload.mission_id}")

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        created_at = utc_now_iso()
        self.store.insert_task(
            task_id=task_id,
            mission_id=payload.mission_id,
            title=payload.title,
            task_type=payload.type,
            executor=payload.executor.value,
            working_directory=payload.working_directory,
            payload=payload.payload,
            priority=payload.priority,
            status=TaskStatus.queued.value,
            created_at=created_at,
        )
        row = self.store.get_task(task_id)
        if not row:
            raise RuntimeError('Failed to read task after insert.')
        return TaskRecord(**row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self.store.get_task(task_id)
        return TaskRecord(**row) if row else None

    def list_tasks(self, mission_id: str | None = None, status: str | None = None) -> list[TaskRecord]:
        return [TaskRecord(**row) for row in self.store.list_tasks(mission_id=mission_id, status=status)]

    def intake(self, payload: IntakeRequest) -> IntakeResponse:
        plan = self.planner.plan(payload)
        goal = self.create_goal(
            GoalCreate(
                objective=plan.goal_objective,
                constraints={
                    'source': 'intake',
                    'user_request': payload.user_request,
                    'project_root': payload.project_root,
                    **payload.goal_constraints,
                },
            )
        )
        mission = self.create_mission(
            MissionCreate(
                goal_id=goal.goal_id,
                objective=plan.mission_objective,
                constraints={
                    'strategy': plan.strategy,
                    'rationale': plan.rationale,
                    **payload.mission_constraints,
                },
            )
        )

        created_tasks: list[TaskRecord] = []
        for planned_task in plan.tasks:
            created_tasks.append(
                self.create_task(
                    TaskCreate(
                        mission_id=mission.mission_id,
                        title=planned_task.title,
                        type=planned_task.type,
                        executor=planned_task.executor,
                        working_directory=planned_task.working_directory,
                        payload=planned_task.payload,
                        priority=planned_task.priority,
                    )
                )
            )

        return IntakeResponse(plan=plan, goal=goal, mission=mission, tasks=created_tasks)
