from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .adaptive_policy import AdaptivePolicyStore
from .auth_manager import AuthManager
from .experiment_registry import ExperimentRegistry
from .knowledge_domains import KnowledgeGraphStore
from .lesson_store import LessonStore
from .memory_mesh import MemoryMeshVault
from .strategy_runtime import StrategyRuntimeStore


class KnowledgeRecord(BaseModel):
    record_id: str
    title: str
    source_type: str = "article"
    category: str = "agent_guidance"
    tags: list[str] = Field(default_factory=list)
    decisions: list[dict] = Field(default_factory=list)
    text_excerpt: str = ""
    created_ts: float = 0.0


class KnowledgeLibraryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: list[KnowledgeRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"records": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.records = [KnowledgeRecord.model_validate(x) for x in raw.get("records", [])]

    def save(self) -> None:
        data = {"records": [x.model_dump(mode="json") for x in self.records[-400:]]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_record(self, record: KnowledgeRecord) -> None:
        self.records.append(record)
        self.save()

    def list_records(self, limit: int = 100) -> list[KnowledgeRecord]:
        self.load()
        return sorted(self.records, key=lambda x: -x.created_ts)[:max(1, limit)]

    def summary(self) -> dict:
        self.load()
        return {"records_count": len(self.records)}


class ReviewItem(BaseModel):
    review_id: str
    title: str
    source_record_id: str
    topic: str
    proposed_changes: list[str] = Field(default_factory=list)
    status: str = "pending"
    created_ts: float = 0.0
    decision_ts: float = 0.0
    reason: str = ""


class ReviewQueueStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = {"items": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.save()
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, title: str, source_record_id: str, topic: str, proposed_changes: list[str], reason: str) -> dict:
        self.load()
        item = ReviewItem(
            review_id=f"review_{uuid.uuid4().hex[:10]}",
            title=title,
            source_record_id=source_record_id,
            topic=topic,
            proposed_changes=list(proposed_changes or []),
            status="pending",
            created_ts=time.time(),
            reason=reason,
        )
        self.data["items"].append(item.model_dump(mode="json"))
        self.save()
        return item.model_dump(mode="json")

    def list_items(self, limit: int = 100) -> list[dict]:
        self.load()
        rows = list(self.data.get("items", []))
        rows.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
        return rows[:max(1, limit)]

    def set_status(self, review_id: str, status: str) -> dict | None:
        self.load()
        for item in self.data.get("items", []):
            if item.get("review_id") == review_id:
                item["status"] = status
                item["decision_ts"] = time.time()
                self.save()
                return item
        return None


class NegativeRule(BaseModel):
    rule_id: str
    text: str
    service: str = ""
    task_family: str = ""
    severity: str = "medium"
    source_record_id: str = ""
    created_ts: float = 0.0


class NegativeKnowledgeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.rules: list[NegativeRule] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.rules = [NegativeRule.model_validate(x) for x in raw.get("rules", [])]

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"rules": [x.model_dump(mode="json") for x in self.rules[-400:]]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_rule(self, text: str, service: str = "", task_family: str = "", severity: str = "medium", source_record_id: str = "") -> dict:
        self.load()
        normalized = (text or "").strip().lower()
        for rule in self.rules:
            if rule.text.strip().lower() == normalized and rule.service == service and rule.task_family == task_family:
                return rule.model_dump(mode="json")

        item = NegativeRule(
            rule_id=f"negative_{uuid.uuid4().hex[:10]}",
            text=text.strip(),
            service=service,
            task_family=task_family,
            severity=severity,
            source_record_id=source_record_id,
            created_ts=time.time(),
        )
        self.rules.append(item)
        self.save()
        return item.model_dump(mode="json")

    def list_rules(self, limit: int = 100) -> list[dict]:
        self.load()
        rows = [x.model_dump(mode="json") for x in self.rules]
        rows.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
        return rows[:max(1, limit)]

    def match(self, service: str = "", task_family: str = "") -> list[dict]:
        self.load()
        matched = []
        for rule in self.rules:
            if rule.service and service and rule.service != service:
                continue
            if rule.task_family and task_family and rule.task_family != task_family:
                continue
            matched.append(rule.model_dump(mode="json"))
        matched.sort(key=lambda x: x.get("severity", "medium"), reverse=True)
        return matched


class KnowledgeGovernanceManager:
    def __init__(
        self,
        base_dir: Path,
        lesson_store: LessonStore,
        policy_store: AdaptivePolicyStore,
        strategy_store: StrategyRuntimeStore,
        experiment_registry: ExperimentRegistry,
        auth_manager: AuthManager,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.lesson_store = lesson_store
        self.policy_store = policy_store
        self.strategy_store = strategy_store
        self.experiment_registry = experiment_registry
        self.auth_manager = auth_manager

        self.library = KnowledgeLibraryStore(self.base_dir / "knowledge_library.json")
        self.graph = KnowledgeGraphStore(self.base_dir / "knowledge_graph.json")
        self.review_queue = ReviewQueueStore(self.base_dir / "knowledge_review_queue.json")
        self.negative = NegativeKnowledgeStore(self.base_dir / "negative_knowledge.json")
        self.vault = MemoryMeshVault(self.base_dir)
        self.raw_dir = self.base_dir / "knowledge_raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        self.import_profiles = {
            "lecture": {"multiplier": 0.74, "review_bias": "medium"},
            "article": {"multiplier": 0.70, "review_bias": "medium"},
            "best_practice": {"multiplier": 0.82, "review_bias": "medium"},
            "agent_manual": {"multiplier": 0.88, "review_bias": "high"},
            "integration_guide": {"multiplier": 0.86, "review_bias": "high"},
            "postmortem": {"multiplier": 0.90, "review_bias": "high"},
        }

    def _profile(self, source_type: str) -> dict:
        return dict(self.import_profiles.get(source_type, {"multiplier": 0.68, "review_bias": "medium"}))

    def _auth(self) -> dict[str, Any]:
        return {x["connector_id"]: x for x in self.auth_manager.list_connector_health()}

    def _extract_points(self, text: str, limit: int = 10) -> list[str]:
        lines = []
        for raw in text.splitlines():
            s = raw.strip(" \t\r\n-*•0123456789.)(")
            if len(s) >= 8:
                lines.append(s)

        bullets = []
        for item in lines:
            if len(item.split()) >= 3:
                bullets.append(item)

        if not bullets:
            parts = re.split(r"(?<=[.!?])\s+", text.strip())
            bullets = [p.strip() for p in parts if len(p.strip().split()) >= 4]

        uniq = []
        seen = set()
        for item in bullets:
            key = item.lower()
            if key not in seen:
                uniq.append(item)
                seen.add(key)
        return uniq[:max(1, limit)]

    def _is_high_impact(self, topic: str, changes: list[str], source_type: str) -> bool:
        if source_type in {"agent_manual", "integration_guide", "postmortem"}:
            return True
        joined = " ".join(changes or []).lower()
        if "connector" in joined or "integration_mode" in joined or "qa_strictness" in joined or "live_expansion" in joined:
            return True
        if topic in {"n8n_live_expansion", "openai_for_planning", "claude_for_codegen"}:
            return True
        return False

    def _queue_or_apply(
        self,
        title: str,
        record_id: str,
        topic: str,
        changes: list[str],
        action_reason: str,
        source_type: str,
        apply_fn,
        auto_apply: bool,
    ) -> dict:
        if auto_apply and not self._is_high_impact(topic, changes, source_type):
            apply_fn()
            return {"action": "adopt", "queued_review": None}
        if auto_apply and self._is_high_impact(topic, changes, source_type):
            queued = self.review_queue.add(
                title=title,
                source_record_id=record_id,
                topic=topic,
                proposed_changes=changes,
                reason=action_reason,
            )
            return {"action": "review_required", "queued_review": queued}
        return {"action": "defer", "queued_review": None}

    def _create_lesson(
        self,
        title: str,
        capability: str,
        preferred_service: str,
        points: list[str],
        confidence: float,
        tags: list[str],
    ) -> str:
        lesson = self.lesson_store.add_lesson(
            agent_id="knowledge_governance",
            capability=capability,
            task_type="knowledge_ingest",
            title=title,
            summary=("; ".join(points[:3]) or title),
            preferred_service=preferred_service,
            recommended_actions=points[:4],
            caution_flags=[],
            tags=tags,
            confidence=confidence,
            source_mission_id="knowledge_ingest",
            source_task_id=title,
        )
        return lesson.lesson_id

    def _extract_negative_rules(self, text: str, source_record_id: str) -> list[dict]:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        out = []

        for part in parts:
            lower = part.lower().strip()
            if not lower:
                continue

            if any(x in lower for x in ["should not", "do not", "avoid", "until", "must not"]):
                service = ""
                task_family = ""

                if "n8n" in lower or "webhook" in lower or "workflow" in lower:
                    service = "n8n"
                    task_family = "integration"
                elif "claude" in lower or "anthropic" in lower:
                    service = "claude_bridge"
                elif "openai" in lower or "gpt" in lower:
                    service = "openai"

                rule = self.negative.add_rule(
                    text=part.strip(),
                    service=service,
                    task_family=task_family,
                    severity="high" if "must not" in lower or "until" in lower else "medium",
                    source_record_id=source_record_id,
                )
                out.append(rule)
        return out

    def ingest_text(
        self,
        title: str,
        text: str,
        source_type: str = "article",
        category: str = "agent_guidance",
        auto_apply: bool = True,
    ) -> dict:
        text = str(text or "").strip()
        lower = text.lower()
        auth = self._auth()
        policy = self.policy_store.dump()
        strategy = self.strategy_store.dump()
        profile = self._profile(source_type)
        multiplier = float(profile.get("multiplier", 0.7))
        points = self._extract_points(text)
        tags: list[str] = []

        record_id = f"knowledge_{uuid.uuid4().hex[:10]}"
        raw_path = self.raw_dir / f"{record_id}.txt"
        raw_path.write_text(text, encoding="utf-8")

        decisions: list[dict] = []
        queued_reviews: list[dict] = []
        lesson_ids: list[str] = []

        def add_decision(topic: str, action: str, reason: str, applied_changes=None, preferred_service="", proposed_variant_id=""):
            decisions.append({
                "topic": topic,
                "action": action,
                "reason": reason,
                "applied_changes": list(applied_changes or []),
                "preferred_service": preferred_service,
                "proposed_variant_id": proposed_variant_id,
            })

        current_connectors = policy.get("connector_preferences", {})

        if "claude" in lower or "anthropic" in lower:
            tags += ["claude", "anthropic"]
            current = str(current_connectors.get("coding_agent_main", {}).get("codegen") or "")
            if current == "claude_bridge":
                add_decision("claude_for_codegen", "keep_current", "Current setup already prefers Claude for codegen.", preferred_service="claude_bridge")
            elif auth.get("claude_bridge_primary", {}).get("configured", False):
                changes = ["coding_agent_main.codegen -> claude_bridge", "research_agent_main.analyze -> claude_bridge"]
                outcome = self._queue_or_apply(
                    title=title,
                    record_id=record_id,
                    topic="claude_for_codegen",
                    changes=changes,
                    action_reason="Knowledge suggests Claude-oriented engineering flow.",
                    source_type=source_type,
                    auto_apply=auto_apply,
                    apply_fn=lambda: (
                        self.policy_store.set_connector_preference("coding_agent_main", "codegen", "claude_bridge"),
                        self.policy_store.set_connector_preference("research_agent_main", "analyze", "claude_bridge"),
                        self.policy_store.save()
                    ),
                )
                add_decision("claude_for_codegen", outcome["action"], "Knowledge suggests Claude-oriented engineering flow.", applied_changes=changes, preferred_service="claude_bridge")
                if outcome["queued_review"]:
                    queued_reviews.append(outcome["queued_review"])

            lesson_ids.append(self._create_lesson(
                title=f"{title} — Claude/codegen guidance",
                capability="codegen",
                preferred_service="claude_bridge",
                points=points,
                confidence=round(0.78 * multiplier, 3),
                tags=["claude", "codegen", "engineering"],
            ))

        if "openai" in lower or "gpt" in lower:
            tags += ["openai", "gpt"]
            current = str(current_connectors.get("planner_main", {}).get("plan") or "")
            if current == "openai":
                add_decision("openai_for_planning", "keep_current", "Current setup already prefers OpenAI for planning/QA.", preferred_service="openai")
            elif auth.get("openai_primary", {}).get("configured", False):
                changes = ["planner_main.plan -> openai", "qa_agent_main.validate -> openai"]
                outcome = self._queue_or_apply(
                    title=title,
                    record_id=record_id,
                    topic="openai_for_planning",
                    changes=changes,
                    action_reason="Knowledge suggests OpenAI-oriented planning and QA flow.",
                    source_type=source_type,
                    auto_apply=auto_apply,
                    apply_fn=lambda: (
                        self.policy_store.set_connector_preference("planner_main", "plan", "openai"),
                        self.policy_store.set_connector_preference("qa_agent_main", "validate", "openai"),
                        self.policy_store.save()
                    ),
                )
                add_decision("openai_for_planning", outcome["action"], "Knowledge suggests OpenAI-oriented planning and QA flow.", applied_changes=changes, preferred_service="openai")
                if outcome["queued_review"]:
                    queued_reviews.append(outcome["queued_review"])

            lesson_ids.append(self._create_lesson(
                title=f"{title} — OpenAI planning guidance",
                capability="plan",
                preferred_service="openai",
                points=points,
                confidence=round(0.75 * multiplier, 3),
                tags=["openai", "planning", "qa"],
            ))

        if any(x in lower for x in ["memory", "context", "obsidian", "knowledge base", "reuse prior lessons", "retention"]):
            tags += ["memory", "context", "obsidian"]
            if bool(strategy.get("knobs", {}).get("encourage_memory_support", True)):
                add_decision("memory_support", "keep_current", "Current strategy already uses strong memory support.")
            else:
                changes = ["strategy.knobs.encourage_memory_support -> true"]
                outcome = self._queue_or_apply(
                    title=title,
                    record_id=record_id,
                    topic="memory_support",
                    changes=changes,
                    action_reason="Knowledge suggests stronger memory/context reuse.",
                    source_type=source_type,
                    auto_apply=auto_apply,
                    apply_fn=lambda: self.strategy_store.activate(
                        strategy.get("active_variant_id", "memory_boost_custom"),
                        strategy.get("active_variant_name", "Memory Boost Custom"),
                        {**dict(strategy.get("knobs", {})), "encourage_memory_support": True},
                    ),
                )
                add_decision("memory_support", outcome["action"], "Knowledge suggests stronger memory/context reuse.", applied_changes=changes)
                if outcome["queued_review"]:
                    queued_reviews.append(outcome["queued_review"])

            lesson_ids.append(self._create_lesson(
                title=f"{title} — Memory/context guidance",
                capability="context_bundle",
                preferred_service="ollama",
                points=points,
                confidence=round(0.76 * multiplier, 3),
                tags=["memory", "context", "retention"],
            ))

        if any(x in lower for x in ["strict", "reliability", "quality gate", "qa", "approval logic"]):
            tags += ["qa", "reliability"]
            if str(strategy.get("knobs", {}).get("qa_strictness", "medium")).lower() == "high":
                add_decision("strict_qa", "keep_current", "Current strategy already uses high QA strictness.")
            else:
                changes = ["strategy.knobs.qa_strictness -> high"]
                outcome = self._queue_or_apply(
                    title=title,
                    record_id=record_id,
                    topic="strict_qa",
                    changes=changes,
                    action_reason="Knowledge suggests stricter QA/reliability checks.",
                    source_type=source_type,
                    auto_apply=auto_apply,
                    apply_fn=lambda: self.strategy_store.activate(
                        strategy.get("active_variant_id", "strict_custom"),
                        strategy.get("active_variant_name", "Strict Custom"),
                        {**dict(strategy.get("knobs", {})), "qa_strictness": "high"},
                    ),
                )
                add_decision("strict_qa", outcome["action"], "Knowledge suggests stricter QA/reliability checks.", applied_changes=changes)
                if outcome["queued_review"]:
                    queued_reviews.append(outcome["queued_review"])

        if any(x in lower for x in ["n8n", "automation", "workflow", "webhook", "integration"]):
            tags += ["n8n", "automation"]
            n8n_ok = auth.get("n8n_primary", {}).get("configured", False)
            if n8n_ok:
                current_mode = str(strategy.get("knobs", {}).get("integration_mode", "guarded_native")).lower()
                if current_mode == "live_expansion":
                    add_decision("n8n_live_expansion", "keep_current", "n8n is configured and live-expansion mode is already active.", preferred_service="n8n")
                else:
                    proposed = self.experiment_registry.propose_variant(
                        name=f"{title} - Integration Expansion Proposal",
                        description="Proposed from ingested knowledge for broader live integrations.",
                        knobs={
                            "consultation_bonus": 0,
                            "qa_strictness": "medium",
                            "encourage_memory_support": True,
                            "prefer_claude_codegen": True,
                            "prefer_openai_planning": True,
                            "integration_mode": "live_expansion",
                        },
                    )
                    add_decision("n8n_live_expansion", "defer_review", "Knowledge suggests broader live integrations; proposed as a variant for review.", preferred_service="n8n", proposed_variant_id=proposed.variant_id)
                    queued_reviews.append(self.review_queue.add(
                        title=title,
                        source_record_id=record_id,
                        topic="n8n_live_expansion",
                        proposed_changes=["variant proposal -> live_expansion", f"variant_id -> {proposed.variant_id}"],
                        reason="Live integration expansion should be human-reviewed.",
                    ))
            else:
                add_decision("n8n_live_expansion", "defer", "Knowledge suggests stronger n8n live usage, but n8n is not fully configured yet.", preferred_service="n8n")

            lesson_ids.append(self._create_lesson(
                title=f"{title} — Automation/integration guidance",
                capability="workflow_run",
                preferred_service="n8n",
                points=points,
                confidence=round(0.74 * multiplier, 3),
                tags=["n8n", "automation", "workflow"],
            ))

        if any(x in lower for x in ["agent", "agents", "communicat", "coordination", "consultation", "delegate", "second opinion", "support agent"]):
            tags += ["agents", "communication", "coordination"]
            add_decision("agent_coordination", "adopt", "Knowledge adds useful coordination patterns and consultation rules.", applied_changes=["knowledge domains updated: Agent Communication and Coordination"])
            lesson_ids.append(self._create_lesson(
                title=f"{title} — Agent coordination guidance",
                capability="context_bundle",
                preferred_service="",
                points=points,
                confidence=round(0.77 * multiplier, 3),
                tags=["agents", "communication", "coordination"],
            ))

        negative_rules = self._extract_negative_rules(text=text, source_record_id=record_id)

        if not decisions:
            add_decision("generic_guidance", "keep_current", "The ingested text did not clearly outperform the current setup, so it was stored as reference knowledge only.")

        record = KnowledgeRecord(
            record_id=record_id,
            title=title,
            source_type=source_type,
            category=category,
            tags=sorted(set(tags)),
            decisions=decisions,
            text_excerpt=text[:1800],
            created_ts=time.time(),
        )
        self.library.add_record(record)

        placements = self.graph.classify_text(title=title, text=text, category=category, tags=tags)
        placement_paths = [f"{p['domain_name']} / {p['block_name']}" for p in placements]

        note_paths = []
        placement_rows = []

        for placement in placements:
            preferred_service = (
                "claude_bridge" if "Claude" in placement["block_name"]
                else "openai" if "OpenAI" in placement["block_name"]
                else "n8n" if "Workflow" in placement["block_name"]
                else "ollama" if "Memory" in placement["domain_name"]
                else ""
            )

            item = self.graph.add_or_merge_item(
                domain_name=placement["domain_name"],
                block_name=placement["block_name"],
                title=title,
                summary=f"{placement['reason']} Key points: " + "; ".join(points[:3]),
                preferred_service=preferred_service,
                tags=sorted(set(tags)),
                source_record_id=record_id,
                links=[x for x in placement_paths if x != f"{placement['domain_name']} / {placement['block_name']}"],
                confidence=round(0.72 * multiplier, 3),
            )

            note_path = self.vault.upsert_note(
                domain_name=placement["domain_name"],
                block_name=placement["block_name"],
                title=title,
                summary=item.summary,
                tags=sorted(set(tags)),
                links=item.links,
                source_type=source_type,
                record_id=record_id,
                extra={
                    "preferred_service": item.preferred_service,
                    "confidence": item.confidence,
                    "source_records": ", ".join(item.source_record_ids),
                },
            )

            note_paths.append(note_path)
            placement_rows.append({
                "domain_name": placement["domain_name"],
                "block_name": placement["block_name"],
                "reason": placement["reason"],
                "item_id": item.item_id,
                "note_path": note_path,
            })

        self.library.load()
        self.graph.load()

        return {
            "record": record.model_dump(mode="json"),
            "lesson_ids": lesson_ids,
            "negative_rules": negative_rules,
            "queued_reviews": queued_reviews,
            "raw_path": str(raw_path),
            "memory_note_paths": note_paths,
            "knowledge_summary": self.library.summary(),
            "graph_summary": self.graph.summary(),
            "placements": placement_rows,
            "review_queue_count": len(self.review_queue.list_items(limit=500)),
        }