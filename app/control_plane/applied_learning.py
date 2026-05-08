from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from .adaptive_policy import AdaptivePolicyStore
from .knowledge_domains import KnowledgeGraphStore
from .knowledge_governance import NegativeKnowledgeStore
from .lesson_store import LessonStore
from .skill_memory import SkillMemoryStore
from .strategy_runtime import StrategyRuntimeStore


class LearningGuidance(BaseModel):
    suggested_service: str = ""
    needs_memory_support: bool = False
    recommended_actions: list[str] = Field(default_factory=list)
    caution_notes: list[str] = Field(default_factory=list)
    lesson_ids: list[str] = Field(default_factory=list)
    lesson_summaries: list[str] = Field(default_factory=list)
    knowledge_item_ids: list[str] = Field(default_factory=list)
    knowledge_summaries: list[str] = Field(default_factory=list)
    domain_paths: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class AppliedLearningEngine:
    CAPABILITY_FAMILIES = {
        "plan": "planning",
        "analyze": "analysis",
        "compare": "analysis",
        "summarize": "analysis",
        "codegen": "engineering",
        "patch": "engineering",
        "refactor": "engineering",
        "debug": "engineering",
        "validate": "quality",
        "smoke_test": "quality",
        "review": "quality",
        "workflow_run": "automation",
        "automation": "automation",
        "memory_write": "memory",
        "context_bundle": "memory",
        "obsidian_sync": "memory",
    }

    TASK_FAMILIES = {
        "planning": "planning",
        "research": "analysis",
        "validation": "quality",
        "codegen": "engineering",
        "integration": "integration",
        "connector_execution": "integration",
        "service_probe": "integration",
        "memory_write": "memory",
        "knowledge_ingest": "knowledge",
    }

    SERVICE_FAMILIES = {
        "claude_bridge": "reasoning_cloud",
        "openai": "reasoning_cloud",
        "ollama": "local_ai",
        "n8n": "automation",
        "google_workspace": "workspace",
        "telegram": "messaging",
    }

    DOMAIN_BY_FAMILY = {
        "engineering": ["Programming and Codegen"],
        "planning": ["Planning and Quality"],
        "analysis": ["Planning and Quality", "Memory and Knowledge"],
        "quality": ["Planning and Quality", "Safety and Reliability"],
        "automation": ["Automation and Integrations", "Safety and Reliability"],
        "integration": ["Automation and Integrations", "Safety and Reliability"],
        "memory": ["Memory and Knowledge"],
        "knowledge": ["Memory and Knowledge", "Agent Communication and Coordination"],
    }

    def __init__(
        self,
        policy_path: str | Path,
        strategy_path: str | Path,
        skill_memory: SkillMemoryStore,
        lesson_store: LessonStore,
        graph_path: str | Path = "state/agent_mesh/knowledge_graph.json",
        negative_path: str | Path = "state/agent_mesh/negative_knowledge.json",
    ) -> None:
        self.policy = AdaptivePolicyStore(Path(policy_path))
        self.strategy = StrategyRuntimeStore(Path(strategy_path))
        self.skill_memory = skill_memory
        self.lesson_store = lesson_store
        self.graph = KnowledgeGraphStore(Path(graph_path))
        self.negative = NegativeKnowledgeStore(Path(negative_path))

    def _parse_service(self, result) -> str:
        for item in list(result.handoff_notes or []):
            if isinstance(item, str) and item.startswith("service="):
                return item.split("=", 1)[1].strip()
        return ""

    def _qa_avg(self, qa_dict: dict | None) -> float:
        if not qa_dict:
            return 0.0
        scores = qa_dict.get("scores", {})
        vals = [float(v) for v in scores.values()]
        if not vals:
            return 0.0
        return round(sum(vals) / len(vals), 3)

    def _cap_family(self, capability: str) -> str:
        return self.CAPABILITY_FAMILIES.get(capability, capability or "unknown")

    def _task_family(self, task_type: str) -> str:
        return self.TASK_FAMILIES.get(task_type, task_type or "unknown")

    def _svc_family(self, service: str) -> str:
        return self.SERVICE_FAMILIES.get(service, service or "")

    def _recency_bonus(self, ts: float) -> float:
        if not ts:
            return 0.0
        age_hours = max(0.0, (time.time() - float(ts)) / 3600.0)
        if age_hours <= 24:
            return 0.12
        if age_hours <= 72:
            return 0.08
        if age_hours <= 168:
            return 0.04
        return 0.0

    def _domains_for_task(self, task) -> list[str]:
        family = self._task_family(task.task_type)
        if family == "integration":
            return self.DOMAIN_BY_FAMILY["integration"]
        cap_family = self._cap_family(task.required_capability)
        return self.DOMAIN_BY_FAMILY.get(cap_family, ["Memory and Knowledge"])

    def _lesson_score(self, task, lesson, preferred_service: str) -> float:
        score = float(getattr(lesson, "confidence", 0.0) or 0.0)
        if lesson.capability == task.required_capability:
            score += 0.35
        elif self._cap_family(lesson.capability) == self._cap_family(task.required_capability):
            score += 0.18

        if self._task_family(lesson.task_type) == self._task_family(task.task_type):
            score += 0.14

        lesson_service = str(lesson.preferred_service or "")
        if preferred_service and lesson_service == preferred_service:
            score += 0.22
        elif preferred_service and lesson_service and self._svc_family(preferred_service) == self._svc_family(lesson_service):
            score += 0.08

        score += self._recency_bonus(getattr(lesson, "created_ts", 0.0))
        return round(score, 3)

    def _item_score(self, item, preferred_service: str) -> float:
        score = float(getattr(item, "confidence", 0.0) or 0.0)
        if preferred_service and getattr(item, "preferred_service", "") == preferred_service:
            score += 0.20
        score += self._recency_bonus(getattr(item, "updated_ts", 0.0))
        return round(score, 3)

    def preview(self, task, agent_id: str) -> LearningGuidance:
        policy = self.policy.dump()
        strategy = self.strategy.dump()
        explicit_preferred = str(task.metadata.get("preferred_service") or "").strip()

        connector_prefs = policy.get("connector_preferences", {}).get(agent_id, {})
        suggested_service = explicit_preferred or str(connector_prefs.get(task.required_capability) or "")

        if not suggested_service and task.task_type != "integration":
            top = self.skill_memory.top_services(agent_id, task.required_capability, limit=1)
            if top:
                suggested_service = top[0]

        lessons = self.lesson_store.list_lessons(limit=400)
        scored_lessons = []
        for lesson in lessons:
            ls = self._lesson_score(task, lesson, explicit_preferred or suggested_service)
            if ls >= 0.72:
                scored_lessons.append((ls, lesson))
        scored_lessons.sort(key=lambda x: x[0], reverse=True)
        top_lessons = [x[1] for x in scored_lessons[:5]]

        domain_names = self._domains_for_task(task)
        graph_items = self.graph.find_relevant(
            domain_names=domain_names,
            preferred_service=(explicit_preferred or suggested_service),
            limit=20,
        )
        scored_items = []
        for item in graph_items:
            scored_items.append((self._item_score(item, explicit_preferred or suggested_service), item))
        scored_items.sort(key=lambda x: x[0], reverse=True)
        top_items = [x[1] for x in scored_items[:5]]

        encourage_memory_support = bool(strategy.get("knobs", {}).get("encourage_memory_support", True))
        needs_memory_support = False
        if task.task_type in {"research", "codegen", "validation"} and encourage_memory_support:
            needs_memory_support = True
        if task.metadata.get("needs_memory_support"):
            needs_memory_support = True
        if task.task_type == "integration":
            action = str(task.metadata.get("connector_action") or "").strip().lower()
            if action in {"config_check", "health"}:
                needs_memory_support = False

        recommended_actions: list[str] = []
        caution_notes: list[str] = []
        lesson_ids: list[str] = []
        lesson_summaries: list[str] = []
        knowledge_item_ids: list[str] = []
        knowledge_summaries: list[str] = []
        domain_paths: list[str] = []

        def add_unique(target: list[str], value: str):
            value = str(value or "").strip()
            if value and value not in target:
                target.append(value)

        for lesson in top_lessons:
            lesson_ids.append(lesson.lesson_id)
            add_unique(lesson_summaries, lesson.summary)
            for item in list(lesson.recommended_actions or [])[:3]:
                add_unique(recommended_actions, item)
            for item in list(lesson.caution_flags or [])[:3]:
                add_unique(caution_notes, item)

        for item in top_items:
            knowledge_item_ids.append(item.item_id)
            add_unique(knowledge_summaries, item.summary)
            path = f"{item.domain_id}/{item.block_id}"
            add_unique(domain_paths, path)
            if len(item.summary.split()) <= 24:
                add_unique(recommended_actions, item.summary)

        negative_rules = self.negative.match(
            service=(explicit_preferred or suggested_service),
            task_family=self._task_family(task.task_type),
        )
        for rule in negative_rules[:4]:
            add_unique(caution_notes, rule.get("text", ""))

        confidence = 0.22
        if explicit_preferred:
            confidence += 0.22
        elif suggested_service:
            confidence += 0.16
        confidence += min(0.24, 0.05 * len(top_lessons))
        confidence += min(0.22, 0.05 * len(top_items))
        if self.skill_memory.get_profile(agent_id, task.required_capability):
            confidence += 0.08
        if negative_rules and not bool(task.metadata.get("dry_run", False)):
            confidence -= 0.10
        confidence = round(max(0.15, min(0.95, confidence)), 2)

        if task.task_type == "integration" and (explicit_preferred or suggested_service) == "n8n" and bool(task.metadata.get("dry_run", False)):
            add_unique(caution_notes, "n8n is currently being used in dry-run oriented mode.")

        return LearningGuidance(
            suggested_service=(explicit_preferred or suggested_service),
            needs_memory_support=needs_memory_support,
            recommended_actions=recommended_actions[:10],
            caution_notes=caution_notes[:10],
            lesson_ids=lesson_ids,
            lesson_summaries=lesson_summaries[:6],
            knowledge_item_ids=knowledge_item_ids,
            knowledge_summaries=knowledge_summaries[:6],
            domain_paths=domain_paths,
            confidence=confidence,
        )

    def apply(self, task, agent_id: str) -> LearningGuidance:
        guidance = self.preview(task, agent_id)

        if guidance.suggested_service and not task.metadata.get("preferred_service"):
            task.metadata["preferred_service"] = guidance.suggested_service

        if guidance.needs_memory_support and not task.metadata.get("needs_memory_support") and task.task_type != "integration":
            task.metadata["needs_memory_support"] = True

        task.metadata["learning_guidance"] = guidance.model_dump(mode="json")
        return guidance

    def learn_from_result(
        self,
        task,
        result,
        qa_dict: dict | None,
        mission_id: str,
        provider: str,
        passed: bool,
    ) -> dict:
        service = self._parse_service(result)
        avg_score = self._qa_avg(qa_dict)

        skill_info = self.skill_memory.add_evidence(
            agent_id=result.agent_id,
            capability=task.required_capability,
            task_type=task.task_type,
            provider=provider or "",
            service=service or "",
            success=passed,
            score=avg_score,
            mission_id=mission_id,
            task_id=task.task_id,
        )

        lesson_id = ""
        if passed:
            lesson = self.lesson_store.add_lesson(
                agent_id=result.agent_id,
                capability=task.required_capability,
                task_type=task.task_type,
                title=task.title,
                summary=(result.summary or result.normalized_text[:300]),
                preferred_service=service or str(task.metadata.get("preferred_service") or ""),
                recommended_actions=list(result.next_actions or []),
                caution_flags=list(result.validation_hints or []),
                tags=[
                    task.required_capability,
                    task.task_type,
                    provider or "",
                    service or "",
                ],
                confidence=float(avg_score or 0.0),
                source_mission_id=mission_id,
                source_task_id=task.task_id,
            )
            lesson_id = lesson.lesson_id

        return {
            "skill_info": skill_info,
            "lesson_id": lesson_id,
            "service": service,
            "passed": passed,
            "avg_score": avg_score,
        }