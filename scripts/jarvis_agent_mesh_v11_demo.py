from __future__ import annotations

import json
from pathlib import Path

from app.control_plane.adaptive_policy import AdaptivePolicyStore
from app.control_plane.auth_manager import AuthManager
from app.control_plane.connector_registry import ConnectorRegistry
from app.control_plane.experiment_registry import ExperimentRegistry
from app.control_plane.knowledge_ingest import KnowledgeIngestor
from app.control_plane.lesson_store import LessonStore
from app.control_plane.lifecycle_manager import LifecycleManager
from app.control_plane.secrets import SecretRegistry
from app.control_plane.skill_memory import SkillMemoryStore
from app.control_plane.strategy_runtime import StrategyRuntimeStore

BASE = Path("state/agent_mesh")
CONNECTORS_PATH = BASE / "connectors.json"
SECRETS_PATH = BASE / "secrets_registry.json"
POLICY_PATH = BASE / "adaptive_policy.json"
STRATEGY_PATH = BASE / "strategy_runtime.json"
EXPERIMENTS_PATH = BASE / "experiment_registry.json"
SKILLS_PATH = BASE / "skill_memory.json"
LESSONS_PATH = BASE / "lesson_store.json"

connector_registry = ConnectorRegistry(CONNECTORS_PATH)
secret_registry = SecretRegistry(SECRETS_PATH)
auth_manager = AuthManager(connector_registry, secret_registry)
policy_store = AdaptivePolicyStore(POLICY_PATH)
strategy_store = StrategyRuntimeStore(STRATEGY_PATH)
experiment_registry = ExperimentRegistry(EXPERIMENTS_PATH)
skills = SkillMemoryStore(SKILLS_PATH)
lessons = LessonStore(LESSONS_PATH)
lifecycle = LifecycleManager(BASE)
ingestor = KnowledgeIngestor(
    BASE,
    lesson_store=lessons,
    policy_store=policy_store,
    strategy_store=strategy_store,
    experiment_registry=experiment_registry,
    auth_manager=auth_manager,
)

sample_text = """
Claude is often strongest for code drafting, refactors and precise implementation work when prompts are explicit.
OpenAI can remain strong for structured planning and QA framing.
n8n should only be promoted to broader live execution after configuration and auth are fully verified.
Memory/context support should stay enabled so the system can reuse prior lessons and patterns.
"""

result = ingestor.ingest_text(
    title="V11 sample knowledge ingest",
    text=sample_text,
    source_type="article",
    category="agent_guidance",
    auto_apply=True,
)

print(json.dumps({
    "ingest_result": result,
    "lifecycle_health": lifecycle.health(),
    "skills_summary": skills.summary(),
    "lessons_summary": lessons.summary(),
}, ensure_ascii=False, indent=2))