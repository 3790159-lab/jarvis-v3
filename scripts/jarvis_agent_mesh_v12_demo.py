from __future__ import annotations

import json
from pathlib import Path

from app.control_plane.knowledge_domains import KnowledgeGraphStore
from app.control_plane.knowledge_ingest import KnowledgeIngestor
from app.control_plane.lesson_store import LessonStore
from app.control_plane.adaptive_policy import AdaptivePolicyStore
from app.control_plane.strategy_runtime import StrategyRuntimeStore
from app.control_plane.experiment_registry import ExperimentRegistry
from app.control_plane.auth_manager import AuthManager
from app.control_plane.connector_registry import ConnectorRegistry
from app.control_plane.secrets import SecretRegistry

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

ingestor = KnowledgeIngestor(
    BASE,
    lesson_store=lesson_store,
    policy_store=policy_store,
    strategy_store=strategy_store,
    experiment_registry=experiment_registry,
    auth_manager=auth_manager,
)

sample_text = """
When coordinating agents, keep roles explicit and avoid over-consultation.
Use second opinions mainly for risky tasks.
Claude is usually strongest for concrete engineering and refactor work.
OpenAI can remain strong for planning and QA framing.
n8n should stay guarded until auth and live webhook configuration are verified.
Knowledge should be organized by domain so programming guidance, communication rules, and integration patterns do not collapse into one flat memory.
"""

result = ingestor.ingest_text(
    title="V12 domain-structured knowledge ingest demo",
    text=sample_text,
    source_type="lecture",
    category="agent_guidance",
    auto_apply=True,
)

print(json.dumps({
    "ingest_result": result,
    "graph_summary": graph.summary(),
    "domains": graph.list_domains(include_items=True),
}, ensure_ascii=False, indent=2))