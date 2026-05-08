from __future__ import annotations

import json
from pathlib import Path

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

registry = AgentRegistry(REGISTRY_PATH)
workers = WorkerRegistry(WORKERS_PATH)
workers.bootstrap_from_registry(registry)
provider_health = ProviderHealthRegistry(PROVIDERS_PATH)
approval_manager = ApprovalManager(APPROVALS_PATH)

connectors = ConnectorRegistry(CONNECTORS_PATH)
secrets = SecretRegistry(SECRETS_PATH)
bootstrap_default_connectors(connectors, secrets)
auth_manager = AuthManager(connectors, secrets)
executor = ConnectorExecutor(connectors, auth_manager, EXEC_LOG_PATH)
policy_store = AdaptivePolicyStore(POLICY_PATH)
strategy_store = StrategyRuntimeStore(STRATEGY_PATH)
experiment_registry = ExperimentRegistry(EXPERIMENTS_PATH)
skill_memory = SkillMemoryStore(SKILLS_PATH)
lesson_store = LessonStore(LESSONS_PATH)
applied_learning = AppliedLearningEngine(POLICY_PATH, STRATEGY_PATH, skill_memory, lesson_store)
policy_router = ConnectorPolicyRouter(connectors, policy_path=POLICY_PATH)
bridge = RuntimeConnectorBridge(policy_router, executor, provider_health)
learner = TelemetryLearner(BASE, policy_store, auth_manager, provider_health)
improvement = ImprovementManager(BASE, policy_store, strategy_store, experiment_registry, auth_manager, provider_health)
integration_exec = IntegrationTaskExecutor(GENERATED_DIR)

consultation = ConsultationCoordinator(registry)
dispatcher = Dispatcher(
    registry,
    ProviderRouter(policy_path=POLICY_PATH),
    consultation,
    applied_learning=applied_learning,
    strategy_path=STRATEGY_PATH,
)
memory = SmartMemoryManager(BASE, obsidian_vault=VAULT_DIR)
snapshot_store = SnapshotStore(BASE)

runtime = SupervisorRuntime(
    registry=registry,
    dispatcher=dispatcher,
    consultation_coordinator=consultation,
    memory=memory,
    qa_gate=QAGate(strategy_path=STRATEGY_PATH),
    recovery=RecoveryGuardian(),
    adapters=AdapterFactory(),
    generated_dir=GENERATED_DIR,
    worker_registry=workers,
    dependency_queue=DependencyQueue(),
    snapshot_store=snapshot_store,
    provider_health=provider_health,
    approval_manager=approval_manager,
    runtime_connector_bridge=bridge,
    integration_task_executor=integration_exec,
    applied_learning=applied_learning,
    max_parallel_workers=4,
)

learner.rebuild_policy()
improvement.run_experiment_sweep("demo_eval")
best = improvement.recommend_best_variant()
if best.get("ok"):
    improvement.apply_variant(best["variant"]["variant_id"])

snapshot = runtime.create_mission(
    goal="Create a resilient v10 skill-memory demo mission.",
    tasks=build_demo_tasks(),
)
snapshot = runtime.run_mission(snapshot, context={"internet_available": True})

guidance_preview = applied_learning.preview(
    task=build_demo_tasks()[2],
    agent_id="coding_agent_main",
)

print(json.dumps({
    "mission": snapshot.model_dump(mode="json"),
    "active_strategy": strategy_store.dump(),
    "adaptive_policy": policy_store.dump(),
    "skills_summary": skill_memory.summary(),
    "lessons_summary": lesson_store.summary(),
    "guidance_preview": guidance_preview.model_dump(mode="json"),
    "skills": [x.model_dump(mode="json") for x in skill_memory.list_profiles(limit=10)],
    "lessons": [x.model_dump(mode="json") for x in lesson_store.list_lessons(limit=10)],
}, ensure_ascii=False, indent=2))