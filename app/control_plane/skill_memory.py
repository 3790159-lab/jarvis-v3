from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field


class SkillEvidence(BaseModel):
    evidence_id: str
    agent_id: str
    capability: str
    task_type: str
    provider: str = ""
    service: str = ""
    success: bool = True
    score: float = 0.0
    mission_id: str = ""
    task_id: str = ""
    created_ts: float = 0.0


class SkillProfile(BaseModel):
    agent_id: str
    capability: str
    total_runs: int = 0
    success_runs: int = 0
    avg_score: float = 0.0
    provider_scores: dict[str, float] = Field(default_factory=dict)
    service_scores: dict[str, float] = Field(default_factory=dict)
    task_type_scores: dict[str, float] = Field(default_factory=dict)
    updated_ts: float = 0.0


class SkillMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.evidence: list[SkillEvidence] = []
        self.profiles: dict[str, SkillProfile] = {}
        self.load()

    def _key(self, agent_id: str, capability: str) -> str:
        return f"{agent_id}::{capability}"

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"evidence": [], "profiles": []}, ensure_ascii=False, indent=2), encoding="utf-8")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.evidence = [SkillEvidence.model_validate(x) for x in raw.get("evidence", [])]
        self.profiles = {}
        for item in raw.get("profiles", []):
            p = SkillProfile.model_validate(item)
            self.profiles[self._key(p.agent_id, p.capability)] = p

    def save(self) -> None:
        data = {
            "evidence": [x.model_dump(mode="json") for x in self.evidence[-600:]],
            "profiles": [x.model_dump(mode="json") for x in self.profiles.values()],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_evidence(
        self,
        agent_id: str,
        capability: str,
        task_type: str,
        provider: str,
        service: str,
        success: bool,
        score: float,
        mission_id: str,
        task_id: str,
    ) -> dict:
        ev = SkillEvidence(
            evidence_id=f"evidence_{uuid.uuid4().hex[:10]}",
            agent_id=agent_id,
            capability=capability,
            task_type=task_type,
            provider=provider or "",
            service=service or "",
            success=success,
            score=float(score or 0.0),
            mission_id=mission_id,
            task_id=task_id,
            created_ts=time.time(),
        )
        self.evidence.append(ev)

        key = self._key(agent_id, capability)
        profile = self.profiles.get(key)
        if not profile:
            profile = SkillProfile(agent_id=agent_id, capability=capability)
            self.profiles[key] = profile

        old_total = profile.total_runs
        old_avg = profile.avg_score
        profile.total_runs += 1
        if success:
            profile.success_runs += 1

        profile.avg_score = round(((old_avg * old_total) + ev.score) / max(1, profile.total_runs), 3)

        if provider:
            profile.provider_scores[provider] = round(profile.provider_scores.get(provider, 0.0) + ev.score, 3)
        if service:
            profile.service_scores[service] = round(profile.service_scores.get(service, 0.0) + ev.score, 3)
        if task_type:
            profile.task_type_scores[task_type] = round(profile.task_type_scores.get(task_type, 0.0) + ev.score, 3)

        profile.updated_ts = time.time()
        self.save()

        return {
            "evidence_id": ev.evidence_id,
            "profile_key": key,
            "avg_score": profile.avg_score,
            "total_runs": profile.total_runs,
            "success_runs": profile.success_runs,
        }

    def get_profile(self, agent_id: str, capability: str) -> SkillProfile | None:
        return self.profiles.get(self._key(agent_id, capability))

    def list_profiles(self, limit: int = 100) -> list[SkillProfile]:
        vals = sorted(self.profiles.values(), key=lambda x: (-x.avg_score, -x.success_runs, x.agent_id, x.capability))
        return vals[:max(1, limit)]

    def top_services(self, agent_id: str, capability: str, limit: int = 3) -> list[str]:
        profile = self.get_profile(agent_id, capability)
        if not profile:
            return []
        ordered = sorted(profile.service_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [k for k, _ in ordered[:max(1, limit)]]

    def summary(self) -> dict:
        return {
            "profiles_count": len(self.profiles),
            "evidence_count": len(self.evidence),
        }