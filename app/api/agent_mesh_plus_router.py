from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.control_plane.adaptive_policy import AdaptivePolicyStore
from app.control_plane.auth_manager import AuthManager
from app.control_plane.autonomous_improvement import AutonomousImprovementController
from app.control_plane.communication_policy import CommunicationPolicyStore
from app.control_plane.connector_registry import ConnectorRegistry
from app.control_plane.experiment_registry import ExperimentRegistry
from app.control_plane.knowledge_domains import KnowledgeGraphStore
from app.control_plane.knowledge_governance import (
    KnowledgeGovernanceManager,
    KnowledgeLibraryStore,
    NegativeKnowledgeStore,
    ReviewQueueStore,
)
from app.control_plane.lesson_store import LessonStore
from app.control_plane.lifecycle_manager import LifecycleManager
from app.control_plane.secrets import SecretRegistry
from app.control_plane.service_telemetry import ServiceTelemetryStore
from app.control_plane.service_trace import ServiceTraceStore
from app.control_plane.skill_memory import SkillMemoryStore
from app.control_plane.strategy_runtime import StrategyRuntimeStore

router = APIRouter(prefix="/api/agent-mesh", tags=["agent-mesh-plus"])

BASE = Path("state/agent_mesh")
CONNECTORS_PATH = BASE / "connectors.json"
SECRETS_PATH = BASE / "secrets_registry.json"
POLICY_PATH = BASE / "adaptive_policy.json"
STRATEGY_PATH = BASE / "strategy_runtime.json"
EXPERIMENTS_PATH = BASE / "experiment_registry.json"
SKILLS_PATH = BASE / "skill_memory.json"
LESSONS_PATH = BASE / "lesson_store.json"
KNOWLEDGE_PATH = BASE / "knowledge_library.json"
GRAPH_PATH = BASE / "knowledge_graph.json"
REVIEW_PATH = BASE / "knowledge_review_queue.json"
NEGATIVE_PATH = BASE / "negative_knowledge.json"
TRACE_PATH = BASE / "service_trace.jsonl"
TELEMETRY_PATH = BASE / "service_telemetry.json"
COMM_POLICY_PATH = BASE / "communication_policy.json"

_connector_registry = ConnectorRegistry(CONNECTORS_PATH)
_secret_registry = SecretRegistry(SECRETS_PATH)
_auth_manager = AuthManager(_connector_registry, _secret_registry)
_policy = AdaptivePolicyStore(POLICY_PATH)
_strategy = StrategyRuntimeStore(STRATEGY_PATH)
_experiments = ExperimentRegistry(EXPERIMENTS_PATH)
_skills = SkillMemoryStore(SKILLS_PATH)
_lessons = LessonStore(LESSONS_PATH)
_knowledge = KnowledgeLibraryStore(KNOWLEDGE_PATH)
_graph = KnowledgeGraphStore(GRAPH_PATH)
_review = ReviewQueueStore(REVIEW_PATH)
_negative = NegativeKnowledgeStore(NEGATIVE_PATH)
_trace = ServiceTraceStore(TRACE_PATH)
_telemetry = ServiceTelemetryStore(TELEMETRY_PATH)
_comm_policy = CommunicationPolicyStore(COMM_POLICY_PATH)
_lifecycle = LifecycleManager(BASE)
_governance = KnowledgeGovernanceManager(
    BASE,
    lesson_store=_lessons,
    policy_store=_policy,
    strategy_store=_strategy,
    experiment_registry=_experiments,
    auth_manager=_auth_manager,
)
_autonomy = AutonomousImprovementController(BASE)
_autonomy.start_background()


class KnowledgeIngestRequest(BaseModel):
    title: str
    text: str
    source_type: str = "article"
    category: str = "agent_guidance"
    auto_apply: bool = True


@router.get("/lifecycle/health")
def lifecycle_health():
    return {
        "status": "ok",
        "lifecycle": _lifecycle.health(),
    }


@router.post("/lifecycle/cleanup")
def lifecycle_cleanup(
    retain_recent_completed: int = 10,
    max_journal_lines_per_mission: int = 400,
    max_exec_log_lines: int = 600,
):
    result = _lifecycle.cleanup(
        retain_recent_completed=retain_recent_completed,
        max_journal_lines_per_mission=max_journal_lines_per_mission,
        max_exec_log_lines=max_exec_log_lines,
    )
    return {"status": "ok", "result": result}


@router.post("/knowledge/ingest")
def knowledge_ingest(request: KnowledgeIngestRequest):
    result = _governance.ingest_text(
        title=request.title,
        text=request.text,
        source_type=request.source_type,
        category=request.category,
        auto_apply=request.auto_apply,
    )
    _knowledge.load()
    _graph.load()
    return {
        "status": "ok",
        "result": result,
        "knowledge_summary": _knowledge.summary(),
        "graph_summary": _graph.summary(),
        "review_queue_count": len(_review.list_items(limit=500)),
        "negative_rules_count": len(_negative.list_rules(limit=500)),
        "lessons_summary": _lessons.summary(),
        "skills_summary": _skills.summary(),
    }


@router.get("/knowledge/library")
def knowledge_library(limit: int = 50):
    _knowledge.load()
    return {
        "status": "ok",
        "summary": _knowledge.summary(),
        "records": [x.model_dump(mode="json") for x in _knowledge.list_records(limit=limit)],
    }


@router.get("/knowledge/domains")
def knowledge_domains(include_items: bool = True):
    _graph.load()
    return {
        "status": "ok",
        "summary": _graph.summary(),
        "domains": _graph.list_domains(include_items=include_items),
    }


@router.get("/knowledge/review-queue")
def knowledge_review_queue(limit: int = 100):
    return {
        "status": "ok",
        "items": _review.list_items(limit=limit),
    }


@router.post("/knowledge/review/approve/{review_id}")
def knowledge_review_approve(review_id: str):
    item = _review.set_status(review_id, "approved")
    if not item:
        raise HTTPException(status_code=404, detail={"message": f"Review not found: {review_id}"})
    return {"status": "ok", "item": item}


@router.post("/knowledge/review/reject/{review_id}")
def knowledge_review_reject(review_id: str):
    item = _review.set_status(review_id, "rejected")
    if not item:
        raise HTTPException(status_code=404, detail={"message": f"Review not found: {review_id}"})
    return {"status": "ok", "item": item}


@router.get("/knowledge/negative-rules")
def knowledge_negative_rules(limit: int = 100):
    return {
        "status": "ok",
        "rules": _negative.list_rules(limit=limit),
    }


@router.get("/service-traces")
def service_traces(limit: int = 100):
    return {
        "status": "ok",
        "events": _trace.tail(limit=limit),
    }


@router.get("/service-telemetry")
def service_telemetry():
    return {
        "status": "ok",
        "telemetry": _telemetry.summary(),
    }


@router.get("/communication-policy")
def communication_policy():
    return {
        "status": "ok",
        "policy": _comm_policy.dump(),
    }


@router.get("/autonomy/health")
def autonomy_health():
    return {
        "status": "ok",
        "autonomy": _autonomy.health(),
    }


@router.post("/autonomy/tick")
def autonomy_tick(reason: str = "manual_api"):
    return {
        "status": "ok",
        "report": _autonomy.run_safe_tick(reason=reason),
    }


@router.get("/autonomy/history")
def autonomy_history(limit: int = 50):
    return {
        "status": "ok",
        "items": _autonomy.history(limit=limit),
    }