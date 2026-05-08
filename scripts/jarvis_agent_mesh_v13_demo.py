from __future__ import annotations

import json
from pathlib import Path

from app.control_plane.auth_manager import AuthManager
from app.control_plane.connector_registry import ConnectorRegistry
from app.control_plane.experiment_registry import ExperimentRegistry
from app.control_plane.knowledge_domains import KnowledgeGraphStore
from app.control_plane.knowledge_governance import KnowledgeGovernanceManager
from app.control_plane.lesson_store import LessonStore
from app.control_plane.secrets import SecretRegistry
from app.control_plane.strategy_runtime import StrategyRuntimeStore
from app.control_plane.adaptive_policy import AdaptivePolicyStore

BASE = Path("state/agent_mesh")
CONNECTORS_PATH = BASE / "connectors.json"
SECRETS_PATH = BASE / "secrets_registry.json"
POLICY_PATH = BASE / "adaptive_policy.json"
STRATEGY_PATH = BASE / "strategy_runtime.json"
EXPERIMENTS_PATH = BASE / "experiment_registry.json"
LESSONS_PATH = BASE / "lesson_store.json"
GRAPH_PATH = BASE / "knowledge_graph.json"

connector_registry = ConnectorRegistry(CONNECTORS_PATH)
secret_registry = SecretRegistry(SECRETS_PATH)
auth_manager = AuthManager(connector_registry, secret_registry)
policy_store = AdaptivePolicyStore(POLICY_PATH)
strategy_store = StrategyRuntimeStore(STRATEGY_PATH)
experiment_registry = ExperimentRegistry(EXPERIMENTS_PATH)
lesson_store = LessonStore(LESSONS_PATH)
graph = KnowledgeGraphStore(GRAPH_PATH)

manager = KnowledgeGovernanceManager(
    BASE,
    lesson_store=lesson_store,
    policy_store=policy_store,
    strategy_store=strategy_store,
    experiment_registry=experiment_registry,
    auth_manager=auth_manager,
)

sample_text = """
Claude is usually strongest for concrete engineering, refactor and implementation work when prompts are explicit.
OpenAI can remain strong for planning, review and QA framing.
Avoid promoting n8n to broad live execution until auth and webhook verification are complete.
Keep communication between agents structured: explicit roles, limited consultation, second opinions mostly for risky tasks.
Knowledge should be separated by domain and block, but linked logically across engineering, planning, communication, memory and integration patterns.
"""

result = manager.ingest_text(
    title="V13 memory mesh governance demo",
    text=sample_text,
    source_type="best_practice",
    category="agent_guidance",
    auto_apply=True,
)

print(json.dumps({
    "result": result,
    "graph_summary": graph.summary(),
}, ensure_ascii=False, indent=2))