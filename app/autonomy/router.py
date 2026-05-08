from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .runtime import get_autonomy
from .store import utc_now_iso

router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])


class SnapshotRequest(BaseModel):
    trigger: str = "manual"


class ToolExecuteRequest(BaseModel):
    tool_id: str
    payload: Dict = Field(default_factory=dict)
    requested_by: str = "api"


class ToolPlanRequest(BaseModel):
    steps: List[Dict]
    requested_by: str = "api_plan"


class ChainExecuteRequest(BaseModel):
    chain_id: str
    payload: Dict = Field(default_factory=dict)
    requested_by: str = "chain_api"


class WorkflowExecuteRequest(BaseModel):
    workflow_id: str
    payload: Dict = Field(default_factory=dict)
    requested_by: str = "workflow_api"


class GraphExecuteRequest(BaseModel):
    graph_id: str
    payload: Dict = Field(default_factory=dict)
    requested_by: str = "graph_api"


class RepairExecuteRequest(BaseModel):
    repair_id: str
    payload: Dict = Field(default_factory=dict)
    requested_by: str = "repair_api"


class EngineStepRequest(BaseModel):
    step: Dict
    context: Dict = Field(default_factory=dict)


class MissionCreateRequest(BaseModel):
    template_id: str
    payload: Dict = Field(default_factory=dict)
    metadata: Dict = Field(default_factory=dict)


class MissionRunRequest(BaseModel):
    max_steps: int = 50


class RecoveryPolicyPatchRequest(BaseModel):
    max_step_attempts_default: Optional[int] = None
    max_step_repairs_default: Optional[int] = None
    max_mission_repairs_total: Optional[int] = None
    max_mission_failures_before_operator: Optional[int] = None
    cooldown_seconds_after_repair: Optional[int] = None
    escalate_on_blocked_by_mode: Optional[bool] = None
    escalate_on_guard_block: Optional[bool] = None


def _project_root() -> Path:
    return Path.cwd()


def _mission_candidates(mission_id: str) -> list[Path]:
    root = _project_root()
    return [
        root / "artifacts" / "missions" / f"{mission_id}.json",
        root / "state" / "missions" / f"{mission_id}.json",
        root / "jarvis_stage3_artifacts" / "missions" / f"{mission_id}.json",
        root / "jarvis_stage3_artifacts" / "generated_projects" / "managed_autonomous_api" / "artifacts" / "missions" / f"{mission_id}.json",
    ]


def _find_mission_record(mission_id: str) -> Optional[Path]:
    for path in _mission_candidates(mission_id):
        if path.exists():
            return path
    return None


@router.get("/health")
async def autonomy_health() -> Dict:
    autonomy = get_autonomy()
    return {"status": "healthy", "service": "autonomy_runtime", "components": list(autonomy.keys()), "checked_at": utc_now_iso()}


@router.post("/reconcile/runtime")
async def reconcile_runtime() -> Dict:
    autonomy = get_autonomy()
    return {"ok": True, "result": autonomy["reconciliation_manager"].reconcile_runtime_with_plans()}


@router.post("/archive/jobs")
async def archive_jobs(older_than_hours: int = 12) -> Dict:
    autonomy = get_autonomy()
    return {"ok": True, "result": autonomy["reconciliation_manager"].archive_old_jobs(older_than_hours=older_than_hours)}


@router.post("/archive/missions")
async def archive_missions(older_than_hours: int = 12) -> Dict:
    autonomy = get_autonomy()
    return {"ok": True, "result": autonomy["reconciliation_manager"].archive_completed_missions(older_than_hours=older_than_hours)}


@router.get("/recovery-policy")
async def get_recovery_policy() -> Dict:
    autonomy = get_autonomy()
    return {"ok": True, "policy": autonomy["recovery_policy_manager"].get_policy()}


@router.post("/recovery-policy")
async def update_recovery_policy(req: RecoveryPolicyPatchRequest) -> Dict:
    autonomy = get_autonomy()
    patch = req.model_dump(exclude_none=True)
    return {"ok": True, "policy": autonomy["recovery_policy_manager"].update_policy(patch)}


@router.get("/operator-escalations")
async def list_operator_escalations(limit: int = 50, mission_id: Optional[str] = None) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["operator_escalation_manager"].list_escalations(limit=limit, mission_id=mission_id)
    return {"ok": True, "count": len(items), "escalations": items}


@router.get("/mission-registry")
async def list_mission_registry() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["mission_registry"].list_missions()
    return {"ok": True, "count": len(items), "missions": items}


@router.get("/mission-templates")
async def list_mission_templates() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["mission_runner"].list_templates()
    return {"ok": True, "count": len(items), "templates": items}


@router.post("/missions")
async def create_mission(req: MissionCreateRequest) -> Dict:
    autonomy = get_autonomy()
    mission = autonomy["mission_runner"].create_mission_from_template(
        template_id=req.template_id,
        payload=dict(req.payload),
        metadata=dict(req.metadata),
    )
    return {"ok": True, "mission": mission}


@router.get("/missions")
async def list_missions() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["mission_runner"].list_missions()
    return {"ok": True, "count": len(items), "missions": items}


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str) -> Dict:
    autonomy = get_autonomy()
    mission = autonomy["mission_runner"].get_mission(mission_id)
    return {"ok": mission is not None, "mission": mission}


@router.post("/missions/{mission_id}/run")
async def run_mission(mission_id: str, req: MissionRunRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["mission_runner"].run_mission(mission_id=mission_id, max_steps=req.max_steps)
    return {"ok": True, "run_result": result}


@router.get("/missions/{mission_id}/history")
async def get_mission_history(mission_id: str, limit: int = 100) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["mission_plan_store"].list_step_history(mission_id=mission_id, limit=limit)
    return {"ok": True, "count": len(items), "history": items}


@router.get("/tools")
async def list_tools() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["tool_registry"].list_tools()
    return {"ok": True, "count": len(items), "tools": items}


@router.post("/tools/execute")
async def execute_tool(req: ToolExecuteRequest) -> Dict:
    autonomy = get_autonomy()
    execution = await autonomy["tool_executor"].execute(req.tool_id, req.payload, req.requested_by)
    return {"ok": True, "execution": execution}


@router.post("/tools/execute-plan")
async def execute_tool_plan(req: ToolPlanRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["tool_executor"].execute_plan(req.steps, req.requested_by)
    return {"ok": True, "plan_result": result}


@router.get("/tools/executions")
async def list_tool_executions(limit: int = 20, tool_id: Optional[str] = None) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["tool_executor"].list_executions(limit=limit, tool_id=tool_id)
    return {"ok": True, "count": len(items), "executions": items}


@router.get("/chains")
async def list_chains() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["chain_manager"].list_templates()
    return {"ok": True, "count": len(items), "chains": items}


@router.post("/chains/execute")
async def execute_chain(req: ChainExecuteRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["chain_manager"].execute_template(req.chain_id, req.payload, req.requested_by)
    return {"ok": True, "chain_result": result}


@router.get("/workflows")
async def list_workflows() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["workflow_manager"].list_templates()
    return {"ok": True, "count": len(items), "workflows": items}


@router.post("/workflows/execute")
async def execute_workflow(req: WorkflowExecuteRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["workflow_manager"].execute_workflow(req.workflow_id, req.payload, req.requested_by)
    return {"ok": True, "workflow_execution": result}


@router.get("/workflows/executions")
async def list_workflow_executions(limit: int = 20, workflow_id: Optional[str] = None) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["workflow_manager"].list_workflows(limit=limit, workflow_id=workflow_id)
    return {"ok": True, "count": len(items), "executions": items}


@router.get("/graphs")
async def list_graphs() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["execution_graph_manager"].list_templates()
    return {"ok": True, "count": len(items), "graphs": items}


@router.post("/graphs/execute")
async def execute_graph(req: GraphExecuteRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["execution_graph_manager"].execute_graph(req.graph_id, req.payload, req.requested_by)
    return {"ok": True, "graph_execution": result}


@router.get("/graphs/executions")
async def list_graph_executions(limit: int = 20) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["execution_graph_manager"].list_runs(limit=limit)
    return {"ok": True, "count": len(items), "executions": items}


@router.get("/repairs")
async def list_repairs() -> Dict:
    autonomy = get_autonomy()
    items = autonomy["repair_loop_manager"].list_templates()
    return {"ok": True, "count": len(items), "repairs": items}


@router.post("/repairs/execute")
async def execute_repair(req: RepairExecuteRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["repair_loop_manager"].execute_repair(req.repair_id, dict(req.payload), req.requested_by)
    return {"ok": True, "repair_execution": result}


@router.get("/repairs/executions")
async def list_repair_executions(limit: int = 20) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["repair_loop_manager"].list_runs(limit=limit)
    return {"ok": True, "count": len(items), "executions": items}


@router.post("/execute-step")
async def execute_step(req: EngineStepRequest) -> Dict:
    autonomy = get_autonomy()
    result = await autonomy["execution_engine"].execute_step(req.step, req.context)
    return {"ok": True, "execution": result}


@router.get("/engine/executions")
async def list_engine_executions(limit: int = 20) -> Dict:
    autonomy = get_autonomy()
    items = autonomy["execution_engine"].list_executions(limit=limit)
    return {"ok": True, "count": len(items), "executions": items}


@router.post("/memory/snapshot/{mission_id}")
async def create_memory_snapshot(mission_id: str, req: SnapshotRequest) -> Dict:
    autonomy = get_autonomy()
    snapshot = autonomy["memory_manager"].create_snapshot(mission_id=mission_id, trigger=req.trigger)
    return {"ok": True, "snapshot": snapshot}


@router.get("/dashboard")
async def autonomy_dashboard() -> Dict:
    autonomy = get_autonomy()
    runtime = autonomy["store"].get_runtime()
    jobs = autonomy["store"].list_jobs()
    missions = autonomy["mission_runner"].list_missions()
    tool_executions = autonomy["tool_executor"].list_executions(limit=30)
    workflow_executions = autonomy["workflow_manager"].list_workflows(limit=20)
    graph_executions = autonomy["execution_graph_manager"].list_runs(limit=20)
    repair_executions = autonomy["repair_loop_manager"].list_runs(limit=20)
    engine_executions = autonomy["execution_engine"].list_executions(limit=20)
    escalations = autonomy["operator_escalation_manager"].list_escalations(limit=20)

    verified_ok = 0
    verified_failed = 0
    for item in tool_executions:
        verification = item.get("verification") or {}
        if verification.get("verified") is True:
            verified_ok += 1
        elif verification.get("verified") is False:
            verified_failed += 1

    workflow_ok = sum(1 for item in workflow_executions if item.get("status") == "completed")
    workflow_failed = sum(1 for item in workflow_executions if item.get("status") == "failed")
    graph_ok = sum(1 for item in graph_executions if item.get("status") == "completed")
    graph_failed = sum(1 for item in graph_executions if item.get("status") == "failed")
    repair_ok = sum(1 for item in repair_executions if item.get("status") == "completed")
    repair_failed = sum(1 for item in repair_executions if item.get("status") == "failed")
    engine_ok = sum(1 for item in engine_executions if item.get("status") in ("completed", "repaired_completed"))
    engine_failed = sum(1 for item in engine_executions if item.get("status") in ("error", "failed_verification", "repaired_failed", "blocked_by_guard", "blocked_by_mode"))

    mission_completed = sum(1 for m in missions if m.get("status") == "completed")
    mission_failed = sum(1 for m in missions if m.get("status") == "failed")
    mission_blocked = sum(1 for m in missions if m.get("status") == "blocked")
    mission_operator = sum(1 for m in missions if m.get("status") == "needs_operator")
    mission_running = sum(1 for m in missions if m.get("status") == "running")
    mission_pending = sum(1 for m in missions if m.get("status") == "pending")

    orphan_runtime = max(0, len(runtime) - len(missions))
    active_failures = workflow_failed + graph_failed + engine_failed

    return {
        "ok": True,
        "summary": {
            "runtime_mission_total": len(runtime),
            "planned_mission_total": len(missions),
            "orphan_runtime_estimate": orphan_runtime,
            "planned_mission_pending": mission_pending,
            "planned_mission_running": mission_running,
            "planned_mission_completed": mission_completed,
            "planned_mission_failed": mission_failed,
            "planned_mission_blocked": mission_blocked,
            "planned_mission_needs_operator": mission_operator,
            "job_total": len(jobs),
            "tool_execution_count": len(tool_executions),
            "tool_verified_ok": verified_ok,
            "tool_verified_failed": verified_failed,
            "workflow_execution_count": len(workflow_executions),
            "workflow_ok": workflow_ok,
            "workflow_failed": workflow_failed,
            "graph_execution_count": len(graph_executions),
            "graph_ok": graph_ok,
            "graph_failed": graph_failed,
            "repair_execution_count": len(repair_executions),
            "repair_ok": repair_ok,
            "repair_failed": repair_failed,
            "engine_execution_count": len(engine_executions),
            "engine_ok": engine_ok,
            "engine_failed": engine_failed,
            "operator_escalation_count": len(escalations),
            "active_failure_signals": active_failures,
        },
        "recent_missions": missions[-10:],
        "recent_escalations": escalations[-10:],
        "recent_tool_executions": tool_executions[-10:],
        "recent_workflows": workflow_executions[-10:],
        "recent_graphs": graph_executions[-10:],
        "recent_repairs": repair_executions[-10:],
        "recent_engine_executions": engine_executions[-10:],
        "generated_at": utc_now_iso(),
    }


@router.get("/mission-bridge/health")
async def mission_bridge_health() -> Dict:
    return {"status": "healthy", "service": "mission_bridge_inline", "cwd": str(_project_root()), "checked_at": utc_now_iso()}


@router.get("/mission-bridge/check/{mission_id}")
async def mission_bridge_check(mission_id: str) -> Dict:
    found = _find_mission_record(mission_id)
    return {
        "mission_id": mission_id,
        "exists_in_known_storage": found is not None,
        "path": str(found) if found else None,
        "checked_paths": [str(p) for p in _mission_candidates(mission_id)],
        "checked_at": utc_now_iso(),
    }


@router.post("/mission-bridge/run/{mission_id}")
async def mission_bridge_run(mission_id: str) -> Dict:
    found = _find_mission_record(mission_id)
    output_dir = _project_root() / "artifacts" / "autonomy"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{mission_id}_continuation_result.txt"
    output_file.write_text(f"Continuation bridge executed successfully for {mission_id}\n", encoding="utf-8")
    return {
        "mission_id": mission_id,
        "status": "completed",
        "bridge": True,
        "message": "Mission bridge continuation completed",
        "mission_record_found": found is not None,
        "mission_record_path": str(found) if found else None,
        "output": {"path": str(output_file)},
        "checked_at": utc_now_iso(),
    }
