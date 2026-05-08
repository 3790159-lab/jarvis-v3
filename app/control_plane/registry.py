from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional, Set

from .models import AgentCapabilityProfile, AgentHealthState


class AgentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.agents: dict[str, AgentCapabilityProfile] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"agents": []}, ensure_ascii=False, indent=2), encoding="utf-8")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.agents = {}
        for item in raw.get("agents", []):
            profile = AgentCapabilityProfile.model_validate(item)
            self.agents[profile.agent_id] = profile

    def save(self) -> None:
        data = {
            "agents": [a.model_dump(mode="json") for a in self.agents.values()]
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_agents(self) -> list[AgentCapabilityProfile]:
        return list(self.agents.values())

    def get(self, agent_id: str) -> Optional[AgentCapabilityProfile]:
        return self.agents.get(agent_id)

    def upsert(self, profile: AgentCapabilityProfile) -> None:
        self.agents[profile.agent_id] = profile
        self.save()

    def list_by_capability(self, capability: str) -> list[AgentCapabilityProfile]:
        matches = [a for a in self.agents.values() if capability in a.all_capabilities()]
        return sorted(
            matches,
            key=lambda a: (
                0 if a.health_state == AgentHealthState.HEALTHY else 1,
                -a.quality_weight,
                a.cost_weight,
                a.agent_id,
            ),
        )

    def choose_agent(
        self,
        capability: str,
        exclude: Optional[Iterable[str]] = None,
        preferred_agent_id: Optional[str] = None,
    ) -> Optional[AgentCapabilityProfile]:
        excluded: Set[str] = set(exclude or [])

        if preferred_agent_id and preferred_agent_id not in excluded:
            preferred = self.get(preferred_agent_id)
            if preferred and capability in preferred.all_capabilities():
                return preferred

        matches = self.list_by_capability(capability)
        for item in matches:
            if item.agent_id not in excluded and item.health_state != AgentHealthState.DISABLED:
                return item
        return None

    def propose_capability(self, agent_id: str, capability: str) -> bool:
        agent = self.get(agent_id)
        if not agent:
            return False
        if capability not in agent.dynamic_capability_proposals:
            agent.dynamic_capability_proposals.append(capability)
            self.save()
        return True

    def approve_capability(self, agent_id: str, capability: str) -> bool:
        agent = self.get(agent_id)
        if not agent:
            return False
        if capability in agent.dynamic_capability_proposals:
            agent.dynamic_capability_proposals = [c for c in agent.dynamic_capability_proposals if c != capability]
        if capability not in agent.approved_dynamic_capabilities:
            agent.approved_dynamic_capabilities.append(capability)
        self.save()
        return True