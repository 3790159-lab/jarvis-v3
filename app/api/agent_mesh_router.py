from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.control_plane.adapters import AdapterFactory
from app.control_plane.adaptive_policy import AdaptivePolicyStore
from app.control_plane.applied_learning import AppliedLearningEngine
from app.control_plane.approval import ApprovalManager
from app.control_plane.auth_manager import AuthManager
from app.control_plane.connector_executor import ConnectorExecutor
from app.control_plane.connector_policy import ConnectorPolicyRouter
from app.control_plane.connector_registry import ConnectorRegistry
from app.control_plane.consultation import ConsultationCoordinator
from app.control_plane.dependency_queue import DependencyQueue
from app.control_plane.dispatcher import Dispatcher
from app.control_plane.experiment_registry import ExperimentRegistry
from app.control_plane.improvement_manager import ImprovementManager
from app.control_plane.integration_layer import bootstrap_default_connectors
from app.control_plane.integration_task_runtime import IntegrationTaskExecutor
from app.control_plane.lesson_store import LessonStore
from app.control_plane.memory import SmartMemoryManager
from app.control_plane.provider_health import ProviderHealthRegistry
from app.control_plane.provider_router import ProviderRouter
from app.control_plane.qa import QAGate
from app.control_plane.recovery import RecoveryGuardian
from app.control_plane.registry import AgentRegistry
from app.control_plane.runtime import SupervisorRuntime, build_demo_tasks
from app.control_plane.runtime_connector_bridge import RuntimeConnectorBridge
from app.control_plane.secrets import SecretRegistry
from app.control_plane.skill_memory import SkillMemoryStore
from app.control_plane.snapshot_store import SnapshotStore
from app.control_plane.strategy_runtime import StrategyRuntimeStore
from app.control_plane.telemetry_learning import TelemetryLearner
from app.control_plane.worker_registry import WorkerRegistry
from app.control_plane.models import TaskSpec

router = APIRouter(prefix="/api/agent-mesh", tags=["agent-mesh"])

BASE = Path("state/agent_mesh")
REGISTRY_PATH = BASE / "agent_registry.seed.json"
WORKERS_PATH = BASE / "worker_registry.json"
PROVIDERS_PATH = BASE / "provider_health.json"
APPROVALS_PATH = BASE / "approvals.json"
CONNECTORS_PATH = BASE / "connectors.json"
SECRETS_PATH = BASE / "secrets_registry.json"
POLICY_PATH = BASE / "adaptive_policy.json"
STRATEGY_PATH = BASE / "strategy_runtime.json"
EXPERIMENTS_PATH = BASE / "experiment_registry.json"
SKILLS_PATH = BASE / "skill_memory.json"
LESSONS_PATH = BASE / "lesson_store.json"
EXEC_LOG_PATH = BASE / "connector_execution_log.jsonl"
GENERATED_DIR = BASE / "demo_artifacts"
VAULT_DIR = BASE / "obsidian_vault"

_registry = AgentRegistry(REGISTRY_PATH)
_workers = WorkerRegistry(WORKERS_PATH)
_workers.bootstrap_from_registry(_registry)
_provider_health = ProviderHealthRegistry(PROVIDERS_PATH)
_approval_manager = ApprovalManager(APPROVALS_PATH)
_connector_registry = ConnectorRegistry(CONNECTORS_PATH)
_secret_registry = SecretRegistry(SECRETS_PATH)
bootstrap_default_connectors(_connector_registry, _secret_registry)
_auth_manager = AuthManager(_connector_registry, _secret_registry)
_connector_executor = ConnectorExecutor(_connector_registry, _auth_manager, EXEC_LOG_PATH)
_adaptive_policy = AdaptivePolicyStore(POLICY_PATH)
_strategy_runtime = StrategyRuntimeStore(STRATEGY_PATH)
_experiments = ExperimentRegistry(EXPERIMENTS_PATH)
_skills = SkillMemoryStore(SKILLS_PATH)
_lessons = LessonStore(LESSONS_PATH)
_applied_learning = AppliedLearningEngine(POLICY_PATH, STRATEGY_PATH, _skills, _lessons)
_connector_policy = ConnectorPolicyRouter(_connector_registry, policy_path=POLICY_PATH)
_runtime_connector_bridge = RuntimeConnectorBridge(_connector_policy, _connector_executor, _provider_health)
_telemetry_learner = TelemetryLearner(BASE, _adaptive_policy, _auth_manager, _provider_health)
_integration_task_executor = IntegrationTaskExecutor(GENERATED_DIR)
_improvement_manager = ImprovementManager(
    BASE,
    _adaptive_policy,
    _strategy_runtime,
    _experiments,
    _auth_manager,
    _provider_health,
)

_consultation = ConsultationCoordinator(_registry)
_dispatcher = Dispatcher(
    _registry,
    ProviderRouter(policy_path=POLICY_PATH),
    _consultation,
    applied_learning=_applied_learning,
    strategy_path=STRATEGY_PATH,
)
_memory = SmartMemoryManager(BASE, obsidian_vault=VAULT_DIR)
_snapshot_store = SnapshotStore(BASE)
_runtime = SupervisorRuntime(
    registry=_registry,
    dispatcher=_dispatcher,
    consultation_coordinator=_consultation,
    memory=_memory,
    qa_gate=QAGate(strategy_path=STRATEGY_PATH),
    recovery=RecoveryGuardian(),
    adapters=AdapterFactory(),
    generated_dir=GENERATED_DIR,
    worker_registry=_workers,
    dependency_queue=DependencyQueue(),
    snapshot_store=_snapshot_store,
    provider_health=_provider_health,
    approval_manager=_approval_manager,
    runtime_connector_bridge=_runtime_connector_bridge,
    integration_task_executor=_integration_task_executor,
    applied_learning=_applied_learning,
)

_telemetry_learner.rebuild_policy()
_improvement_manager.run_experiment_sweep("boot_eval")
best = _improvement_manager.recommend_best_variant()
if best.get("ok"):
    _improvement_manager.apply_variant(best["variant"]["variant_id"])


class DemoRunRequest(BaseModel):
    goal: str = "Create a resilient multi-agent mission and validate applied learning."


class RuntimeConnectorRequest(BaseModel):
    agent_id: str
    capability: str
    action: str
    payload: dict = Field(default_factory=dict)
    preferred_service: str = ""
    dry_run: bool = True


class NativeIntegrationTaskRequest(BaseModel):
    task_id: str = "native_integration_task"
    title: str = "Run native integration task"
    required_capability: str
    preferred_service: str = ""
    connector_action: str = "config_check"
    dry_run: bool = True
    connector_payload: dict = Field(default_factory=dict)
    priority: str = "normal"


class VariantProposalRequest(BaseModel):
    name: str
    description: str = ""
    knobs: dict = Field(default_factory=dict)


class GuidancePreviewRequest(BaseModel):
    required_capability: str
    task_type: str = "codegen"
    title: str = "Guidance preview task"
    priority: str = "normal"
    risk_level: str = "normal"
    preferred_service: str = ""
    connector_action: str = "config_check"
    dry_run: bool = True
    agent_id: str = "coding_agent_main"


@router.get("/health")
def agent_mesh_health():
    return {
        "status": "healthy",
        "service": "agent_mesh_control_plane_v16",
        "agents_registered": len(_registry.list_agents()),
        "worker_summary": _workers.summary(),
        "provider_summary": _provider_health.summary(),
        "pending_approvals": len(_approval_manager.list_pending()),
        "connectors": len(_connector_registry.list_all()),
        "active_missions": _snapshot_store.list_active(),
        "active_strategy": _strategy_runtime.dump(),
        "variants_count": len(_experiments.list_variants()),
        "skills_summary": _skills.summary(),
        "lessons_summary": _lessons.summary(),
    }


@router.get("/learning/health")
def learning_health():
    return _telemetry_learner.health()


@router.post("/learning/rebuild")
def learning_rebuild():
    policy = _telemetry_learner.rebuild_policy()
    return {
        "status": "ok",
        "policy": policy,
    }


@router.get("/adaptive-policy")
def adaptive_policy():
    return {
        "status": "ok",
        "policy": _adaptive_policy.dump(),
    }


@router.get("/strategy/active")
def strategy_active():
    return {
        "status": "ok",
        "strategy": _strategy_runtime.dump(),
    }


@router.get("/learning/variants")
def learning_variants():
    return {
        "status": "ok",
        "variants": [v.model_dump(mode="json") for v in _experiments.list_variants()],
    }


@router.post("/learning/variants/propose")
def propose_variant(request: VariantProposalRequest):
    variant = _experiments.propose_variant(
        name=request.name,
        description=request.description,
        knobs=request.knobs,
    )
    return {
        "status": "ok",
        "variant": variant.model_dump(mode="json"),
    }


@router.post("/learning/variants/approve/{variant_id}")
def approve_variant(variant_id: str):
    ok = _experiments.approve_variant(variant_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"message": f"Variant not found: {variant_id}"})
    return {
        "status": "ok",
        "variant": _experiments.get_variant(variant_id).model_dump(mode="json"),
    }


@router.get("/learning/experiments/runs")
def experiment_runs(limit: int = 50):
    return {
        "status": "ok",
        "runs": [r.model_dump(mode="json") for r in _experiments.list_runs(limit=limit)],
    }


@router.post("/learning/experiments/run")
def experiment_run(scenario: str = "manual_eval"):
    result = _improvement_manager.run_experiment_sweep(scenario=scenario)
    return {
        "status": "ok",
        "result": result,
    }


@router.get("/learning/recommendation")
def learning_recommendation():
    return {
        "status": "ok",
        "recommendation": _improvement_manager.recommend_best_variant(),
    }


@router.post("/learning/variants/apply/{variant_id}")
def apply_variant(variant_id: str):
    result = _improvement_manager.apply_variant(variant_id)
    if not result.get("ok", False):
        raise HTTPException(status_code=400, detail=result)
    return {
        "status": "ok",
        "result": result,
    }


@router.get("/learning/skills")
def learning_skills(limit: int = 100):
    return {
        "status": "ok",
        "summary": _skills.summary(),
        "profiles": [x.model_dump(mode="json") for x in _skills.list_profiles(limit=limit)],
    }


@router.get("/learning/lessons")
def learning_lessons(limit: int = 100):
    return {
        "status": "ok",
        "summary": _lessons.summary(),
        "lessons": [x.model_dump(mode="json") for x in _lessons.list_lessons(limit=limit)],
    }


@router.post("/learning/guidance/preview")
def guidance_preview(request: GuidancePreviewRequest):
    task = TaskSpec(
        task_id="guidance_preview_task",
        title=request.title,
        task_type=request.task_type,
        required_capability=request.required_capability,
        priority=request.priority,
        risk_level=request.risk_level,
        metadata={
            "preferred_service": request.preferred_service,
            "connector_action": request.connector_action,
            "dry_run": request.dry_run,
        },
    )
    guidance = _applied_learning.preview(task, request.agent_id)
    return {
        "status": "ok",
        "guidance": guidance.model_dump(mode="json"),
    }


@router.get("/connector-policy/{agent_id}/{capability}")
def connector_policy(agent_id: str, capability: str, preferred_service: str = ""):
    route = _connector_policy.select(agent_id=agent_id, capability=capability, preferred_service=preferred_service)
    return {
        "status": "ok",
        "route": route.model_dump(mode="json"),
    }


@router.post("/runtime/connectors/execute")
def runtime_connector_execute(request: RuntimeConnectorRequest):
    result = _runtime_connector_bridge.run(
        agent_id=request.agent_id,
        capability=request.capability,
        action=request.action,
        payload=request.payload,
        preferred_service=request.preferred_service,
        dry_run=request.dry_run,
    )
    return {
        "status": "ok" if result.ok else "error",
        "result": result.model_dump(mode="json"),
    }


@router.post("/runtime/integration-task/execute")
def runtime_integration_task_execute(request: NativeIntegrationTaskRequest):
    task = TaskSpec(
        task_id=request.task_id,
        title=request.title,
        task_type="integration",
        required_capability=request.required_capability,
        priority=request.priority,
        consultation_allowed=False,
        metadata={
            "preferred_service": request.preferred_service,
            "connector_action": request.connector_action,
            "dry_run": request.dry_run,
            "persist_output": True,
            "connector_payload": request.connector_payload,
        },
    )
    mission = _runtime.run_single_task(
        task=task,
        goal=f"Execute native integration task: {request.title}",
        context={"internet_available": True},
    )
    _telemetry_learner.rebuild_policy()
    _improvement_manager.run_experiment_sweep("post_native_task_eval")
    return {
        "status": "ok",
        "mission": mission.model_dump(mode="json"),
        "adaptive_policy": _adaptive_policy.dump(),
        "active_strategy": _strategy_runtime.dump(),
        "skills_summary": _skills.summary(),
        "lessons_summary": _lessons.summary(),
    }


@router.get("/connectors/log")
def connector_log(limit: int = 25):
    return {
        "status": "ok",
        "events": _connector_executor.read_log(limit=limit),
    }


@router.get("/auth/health")
def auth_health():
    return {
        "status": "ok",
        "connectors": _auth_manager.list_connector_health(),
    }


@router.post("/demo-run-v10")
def demo_run_v10(request: DemoRunRequest):
    _workers.bootstrap_from_registry(_registry)
    mission = _runtime.create_mission(goal=request.goal, tasks=build_demo_tasks())
    result = _runtime.run_mission(mission)
    _telemetry_learner.rebuild_policy()
    _improvement_manager.run_experiment_sweep("post_demo_eval")
    recommendation = _improvement_manager.recommend_best_variant()
    return {
        "status": "ok",
        "mission": result.model_dump(mode="json"),
        "worker_summary": _workers.summary(),
        "provider_summary": _provider_health.summary(),
        "pending_approvals": len(_approval_manager.list_pending()),
        "connector_count": len(_connector_registry.list_all()),
        "adaptive_policy": _adaptive_policy.dump(),
        "active_strategy": _strategy_runtime.dump(),
        "recommendation": recommendation,
        "skills_summary": _skills.summary(),
        "lessons_summary": _lessons.summary(),
    }


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str):
    mission = _runtime.load_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail={"message": f"Mission not found: {mission_id}"})
    return {
        "status": "ok",
        "mission": mission.model_dump(mode="json"),
    }