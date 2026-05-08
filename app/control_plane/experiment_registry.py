from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class VariantStatus(str, Enum):
    APPROVED = "approved"
    PROPOSED = "proposed"
    REJECTED = "rejected"


class StrategyVariant(BaseModel):
    variant_id: str
    name: str
    description: str = ""
    status: VariantStatus = VariantStatus.APPROVED
    knobs: dict[str, Any] = Field(default_factory=dict)
    created_ts: float = 0.0
    approved_ts: float = 0.0
    last_score: float = 0.0


class ExperimentRun(BaseModel):
    run_id: str
    scenario: str
    variant_id: str
    total_score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    created_ts: float = 0.0


class ExperimentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.variants: dict[str, StrategyVariant] = {}
        self.runs: list[ExperimentRun] = []
        self.load()
        self.ensure_defaults()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"variants": [], "runs": []}, ensure_ascii=False, indent=2), encoding="utf-8")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.variants = {}
        self.runs = []

        for item in raw.get("variants", []):
            v = StrategyVariant.model_validate(item)
            self.variants[v.variant_id] = v

        for item in raw.get("runs", []):
            self.runs.append(ExperimentRun.model_validate(item))

    def save(self) -> None:
        data = {
            "variants": [v.model_dump(mode="json") for v in self.variants.values()],
            "runs": [r.model_dump(mode="json") for r in self.runs[-300:]],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_defaults(self) -> None:
        defaults = [
            StrategyVariant(
                variant_id="balanced_cloud_primary",
                name="Balanced Cloud Primary",
                description="Balanced default with cloud-first reasoning and guarded integrations.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 0,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="strict_reliability",
                name="Strict Reliability",
                description="More validation and tighter recovery bias.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 1,
                    "qa_strictness": "high",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="fast_iteration",
                name="Fast Iteration",
                description="Lower friction for faster cycles while preserving cloud-first routing.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 0,
                    "qa_strictness": "low",
                    "encourage_memory_support": False,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="research_heavy",
                name="Research Heavy",
                description="Encourages extra support and analysis before generation.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 1,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="integration_expansion_guarded",
                name="Integration Expansion Guarded",
                description="More emphasis on native integration tasks with guarded execution.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 0,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="privacy_locality_focus",
                name="Privacy Locality Focus",
                description="Keeps locality-sensitive behavior stronger while preserving cloud for reasoning.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 0,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": False,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="planner_research_balance",
                name="Planner Research Balance",
                description="Slightly stronger planning-research coordination.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 1,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": False,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="memory_amplified",
                name="Memory Amplified",
                description="Uses memory support more aggressively for reusable knowledge.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 1,
                    "qa_strictness": "high",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "guarded_native",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
            StrategyVariant(
                variant_id="connector_staging",
                name="Connector Staging",
                description="Prepared for broader live integrations when connectors become fully configured.",
                status=VariantStatus.APPROVED,
                knobs={
                    "consultation_bonus": 0,
                    "qa_strictness": "medium",
                    "encourage_memory_support": True,
                    "prefer_claude_codegen": True,
                    "prefer_openai_planning": True,
                    "integration_mode": "live_expansion",
                },
                created_ts=time.time(),
                approved_ts=time.time(),
            ),
        ]

        changed = False
        for item in defaults:
            if item.variant_id not in self.variants:
                self.variants[item.variant_id] = item
                changed = True

        if changed:
            self.save()

    def list_variants(self) -> list[StrategyVariant]:
        return list(self.variants.values())

    def get_variant(self, variant_id: str) -> StrategyVariant | None:
        return self.variants.get(variant_id)

    def propose_variant(self, name: str, description: str, knobs: dict[str, Any]) -> StrategyVariant:
        variant = StrategyVariant(
            variant_id=f"variant_{uuid.uuid4().hex[:10]}",
            name=name,
            description=description,
            status=VariantStatus.PROPOSED,
            knobs=dict(knobs),
            created_ts=time.time(),
            approved_ts=0.0,
            last_score=0.0,
        )
        self.variants[variant.variant_id] = variant
        self.save()
        return variant

    def approve_variant(self, variant_id: str) -> bool:
        variant = self.variants.get(variant_id)
        if not variant:
            return False
        variant.status = VariantStatus.APPROVED
        variant.approved_ts = time.time()
        self.save()
        return True

    def reject_variant(self, variant_id: str) -> bool:
        variant = self.variants.get(variant_id)
        if not variant:
            return False
        variant.status = VariantStatus.REJECTED
        self.save()
        return True

    def append_run(self, run: ExperimentRun) -> None:
        self.runs.append(run)
        self.save()

    def list_runs(self, limit: int = 50) -> list[ExperimentRun]:
        return self.runs[-max(1, limit):]