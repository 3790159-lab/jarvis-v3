from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .adaptive_policy import AdaptivePolicyStore
from .auth_manager import AuthManager
from .experiment_registry import ExperimentRegistry, ExperimentRun, StrategyVariant, VariantStatus
from .provider_health import ProviderHealthRegistry
from .strategy_runtime import StrategyRuntimeStore


class ImprovementManager:
    def __init__(
        self,
        base_dir: Path,
        policy_store: AdaptivePolicyStore,
        strategy_store: StrategyRuntimeStore,
        experiment_registry: ExperimentRegistry,
        auth_manager: AuthManager,
        provider_health: ProviderHealthRegistry,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.policy_store = policy_store
        self.strategy_store = strategy_store
        self.experiment_registry = experiment_registry
        self.auth_manager = auth_manager
        self.provider_health = provider_health

    def _provider_snapshot(self) -> dict[str, Any]:
        providers = self.provider_health.summary().get("providers", [])
        return {p["provider"]: p for p in providers}

    def _auth_snapshot(self) -> dict[str, Any]:
        return {x["connector_id"]: x for x in self.auth_manager.list_connector_health()}

    def evaluate_variant(self, variant: StrategyVariant, scenario: str = "system_eval") -> ExperimentRun:
        auth = self._auth_snapshot()
        providers = self._provider_snapshot()
        knobs = dict(variant.knobs)

        score = 5.0
        breakdown: dict[str, float] = {}
        notes: list[str] = []

        cloud_healthy = providers.get("cloud", {}).get("status", "healthy") == "healthy"
        if cloud_healthy:
            score += 1.0
            breakdown["cloud_health"] = 1.0
        else:
            breakdown["cloud_health"] = -0.5
            score -= 0.5

        if knobs.get("prefer_claude_codegen", False) and auth.get("claude_bridge_primary", {}).get("configured", False):
            score += 1.2
            breakdown["claude_codegen_fit"] = 1.2
        elif knobs.get("prefer_claude_codegen", False):
            score -= 0.4
            breakdown["claude_codegen_fit"] = -0.4
            notes.append("Claude preference set but connector is not fully configured.")

        if knobs.get("prefer_openai_planning", False) and auth.get("openai_primary", {}).get("configured", False):
            score += 0.9
            breakdown["openai_planning_fit"] = 0.9

        qa_strictness = str(knobs.get("qa_strictness", "medium")).lower()
        if qa_strictness == "high":
            score += 0.6
            breakdown["qa_strictness"] = 0.6
        elif qa_strictness == "low":
            score -= 0.2
            breakdown["qa_strictness"] = -0.2
        else:
            breakdown["qa_strictness"] = 0.2
            score += 0.2

        consultation_bonus = int(knobs.get("consultation_bonus", 0) or 0)
        if consultation_bonus == 1:
            score += 0.4
            breakdown["consultation_bonus"] = 0.4
        elif consultation_bonus >= 2:
            score -= 0.3
            breakdown["consultation_bonus"] = -0.3
            notes.append("Very high consultation can slow the system.")

        if knobs.get("encourage_memory_support", False):
            score += 0.4
            breakdown["memory_support"] = 0.4

        integration_mode = str(knobs.get("integration_mode", "guarded_native")).lower()
        n8n_ok = auth.get("n8n_primary", {}).get("configured", False)
        if integration_mode == "guarded_native":
            score += 0.6
            breakdown["integration_mode"] = 0.6
        elif integration_mode == "live_expansion":
            if n8n_ok:
                score += 0.8
                breakdown["integration_mode"] = 0.8
            else:
                score -= 0.8
                breakdown["integration_mode"] = -0.8
                notes.append("Live expansion requested while n8n is not fully configured.")

        score = max(0.0, min(10.0, round(score, 2)))

        run = ExperimentRun(
            run_id=f"run_{uuid.uuid4().hex[:10]}",
            scenario=scenario,
            variant_id=variant.variant_id,
            total_score=score,
            score_breakdown=breakdown,
            notes=notes,
            created_ts=time.time(),
        )
        return run

    def run_experiment_sweep(self, scenario: str = "system_eval") -> dict[str, Any]:
        variants = self.experiment_registry.list_variants()
        runs: list[dict[str, Any]] = []

        for variant in variants:
            if variant.status == VariantStatus.REJECTED:
                continue
            run = self.evaluate_variant(variant, scenario=scenario)
            self.experiment_registry.append_run(run)
            variant.last_score = run.total_score
            runs.append(run.model_dump(mode="json"))

        self.experiment_registry.save()
        return {
            "scenario": scenario,
            "runs": runs,
        }

    def recommend_best_variant(self) -> dict[str, Any]:
        variants = [
            v for v in self.experiment_registry.list_variants()
            if v.status == VariantStatus.APPROVED
        ]
        if not variants:
            return {"ok": False, "message": "No approved variants available."}

        best = sorted(variants, key=lambda v: (-v.last_score, v.name))[0]
        return {
            "ok": True,
            "variant": best.model_dump(mode="json"),
        }

    def apply_variant(self, variant_id: str) -> dict[str, Any]:
        variant = self.experiment_registry.get_variant(variant_id)
        if not variant:
            return {"ok": False, "message": f"Variant not found: {variant_id}"}
        if variant.status != VariantStatus.APPROVED:
            return {"ok": False, "message": f"Variant is not approved: {variant_id}"}

        knobs = dict(variant.knobs)
        self.strategy_store.activate(variant.variant_id, variant.name, knobs)

        self.policy_store.load()

        if knobs.get("prefer_claude_codegen", False):
            self.policy_store.set_connector_preference("coding_agent_main", "codegen", "claude_bridge")
            self.policy_store.set_connector_preference("research_agent_main", "analyze", "claude_bridge")

        if knobs.get("prefer_openai_planning", False):
            self.policy_store.set_connector_preference("planner_main", "plan", "openai")
            self.policy_store.set_connector_preference("qa_agent_main", "validate", "openai")

        note = f"Active strategy variant applied: {variant.variant_id}"
        notes = [n for n in list(self.policy_store.policy.notes) if n != note]
        notes.append(note)

        self.policy_store.set_notes(notes)
        self.policy_store.set_learning_summary({
            **self.policy_store.policy.learning_summary,
            "active_variant": variant.variant_id,
            "active_variant_name": variant.name,
            "active_knobs": knobs,
        })
        self.policy_store.save()

        return {
            "ok": True,
            "variant": variant.model_dump(mode="json"),
            "strategy_runtime": self.strategy_store.dump(),
            "adaptive_policy": self.policy_store.dump(),
        }