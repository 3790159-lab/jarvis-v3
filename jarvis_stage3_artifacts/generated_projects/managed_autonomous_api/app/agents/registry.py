from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.agents.schemas import AgentRegistryDocument, AgentRegistryEntry, AgentPolicy
from app.core.time_utils import utc_now


BASE_DIR = Path(__file__).resolve().parents[2]
REGISTRY_FILE = BASE_DIR / "artifacts" / "agent_registry.json"


class AgentRegistry:
    def __init__(self, registry_file: Optional[Path] = None) -> None:
        self.registry_file = registry_file or REGISTRY_FILE

    def _empty_doc(self) -> AgentRegistryDocument:
        return AgentRegistryDocument(version=1, updated_at=None, agents=[])

    def _ensure_file(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.registry_file.exists():
            self.save(self._empty_doc())

    def load(self) -> AgentRegistryDocument:
        self._ensure_file()
        try:
            raw = self.registry_file.read_text(encoding="utf-8-sig").strip()
            if not raw:
                doc = self._empty_doc()
                self.save(doc)
                return doc

            data = json.loads(raw)
            if not isinstance(data, dict):
                doc = self._empty_doc()
                self.save(doc)
                return doc

            data.setdefault("version", 1)
            data.setdefault("updated_at", None)
            data.setdefault("agents", [])

            return AgentRegistryDocument.model_validate(data)

        except Exception:
            doc = self._empty_doc()
            self.save(doc)
            return doc

    def save(self, doc: AgentRegistryDocument) -> AgentRegistryDocument:
        doc.updated_at = utc_now()
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.registry_file.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        return doc

    def list_agents(self) -> List[AgentRegistryEntry]:
        return self.load().agents

    def get(self, agent_id: str) -> Optional[AgentRegistryEntry]:
        for agent in self.load().agents:
            if agent.agent_id == agent_id:
                return agent
        return None

    def upsert(self, entry: AgentRegistryEntry) -> AgentRegistryEntry:
        doc = self.load()
        replaced = False
        new_agents = []

        for agent in doc.agents:
            if agent.agent_id == entry.agent_id:
                entry.updated_at = utc_now()
                new_agents.append(entry)
                replaced = True
            else:
                new_agents.append(agent)

        if not replaced:
            new_agents.append(entry)

        doc.agents = new_agents
        self.save(doc)
        return entry

    def set_status(self, agent_id: str, status: str) -> AgentRegistryEntry:
        agent = self.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        agent.status = status
        agent.updated_at = utc_now()
        return self.upsert(agent)

    def set_health(self, agent_id: str, health: str) -> AgentRegistryEntry:
        agent = self.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        agent.health = health
        agent.updated_at = utc_now()
        return self.upsert(agent)

    def refresh_health(self) -> Dict[str, Any]:
        agents = self.list_agents()
        updated = []
        for agent in agents:
            if agent.status == "active":
                agent.health = "healthy"
            elif agent.status == "quarantined":
                agent.health = "degraded"
            else:
                agent.health = "unknown"
            agent.updated_at = utc_now()
            self.upsert(agent)
            updated.append({
                "agent_id": agent.agent_id,
                "status": agent.status,
                "health": agent.health
            })
        return {
            "updated_count": len(updated),
            "agents": updated
        }

    def validate_registry(self) -> Dict[str, Any]:
        agents = self.list_agents()
        errors = []
        warnings = []

        seen_ids = set()

        for agent in agents:
            if agent.agent_id in seen_ids:
                errors.append(f"Duplicate agent_id: {agent.agent_id}")
            seen_ids.add(agent.agent_id)

            if not agent.allowed_tools:
                warnings.append(f"Agent has no allowed_tools: {agent.agent_id}")

            if agent.role == "planner":
                risky = {"shell", "python", "http", "filesystem"}
                intersection = risky.intersection(set(agent.allowed_tools))
                if intersection:
                    warnings.append(
                        f"Planner agent has risky tools: {agent.agent_id} -> {sorted(intersection)}"
                    )

            if agent.role == "qa":
                risky = {"shell", "python", "http", "filesystem"}
                intersection = risky.intersection(set(agent.allowed_tools))
                if intersection:
                    errors.append(
                        f"QA agent must not have risky tools: {agent.agent_id} -> {sorted(intersection)}"
                    )

            if agent.status not in {"active", "disabled", "quarantined", "draft"}:
                errors.append(f"Invalid status for {agent.agent_id}: {agent.status}")

            if agent.health not in {"unknown", "healthy", "degraded", "unhealthy"}:
                errors.append(f"Invalid health for {agent.agent_id}: {agent.health}")

        return {
            "ok": len(errors) == 0,
            "agents_count": len(agents),
            "errors": errors,
            "warnings": warnings
        }

    def _policy(self, profile: str, max_steps: int, timeout_seconds: int, risk_level: str,
                allow_shell: bool, allow_python: bool, allow_file_read: bool,
                allow_file_write: bool, allow_http: bool, allow_registry_write: bool) -> AgentPolicy:
        return AgentPolicy(
            profile=profile,
            allow_shell=allow_shell,
            allow_python=allow_python,
            allow_file_read=allow_file_read,
            allow_file_write=allow_file_write,
            allow_http=allow_http,
            allow_registry_write=allow_registry_write,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            risk_level=risk_level
        )

    def seed_defaults(self) -> List[AgentRegistryEntry]:
        defaults = [
            AgentRegistryEntry(
                agent_id="supervisor_core",
                name="Supervisor Core",
                role="supervisor",
                description="Main orchestration and mission control agent",
                model_profile="reasoning_primary",
                policy=self._policy("admin", 40, 900, "high", True, True, True, True, True, True),
                allowed_tools=["registry", "mission_state", "task_state", "contracts"],
                created_by="phase6_1_stability"
            ),
            AgentRegistryEntry(
                agent_id="planner_core",
                name="Planner Core",
                role="planner",
                description="Builds plans and task graphs",
                model_profile="reasoning_primary",
                policy=self._policy("safe", 12, 120, "low", False, False, True, False, False, False),
                allowed_tools=["registry", "contracts"],
                created_by="phase6_1_stability"
            ),
            AgentRegistryEntry(
                agent_id="executor_core",
                name="Executor Core",
                role="executor",
                description="Executes allowed tool actions",
                model_profile="fast_ops",
                policy=self._policy("dev", 20, 300, "medium", True, True, True, True, True, False),
                allowed_tools=["shell", "python", "filesystem", "http"],
                created_by="phase6_1_stability"
            ),
            AgentRegistryEntry(
                agent_id="qa_core",
                name="QA Core",
                role="qa",
                description="Validates outputs, contracts and execution state",
                model_profile="reasoning_primary",
                policy=self._policy("safe", 8, 60, "low", False, False, True, False, False, False),
                allowed_tools=["registry", "contracts", "filesystem_read"],
                created_by="phase6_1_stability"
            ),
            AgentRegistryEntry(
                agent_id="code_core",
                name="Code Core",
                role="code",
                description="Creates and updates code safely",
                model_profile="code_specialist",
                policy=self._policy("dev", 20, 300, "medium", False, True, True, True, False, False),
                allowed_tools=["filesystem", "python", "registry"],
                created_by="phase6_1_stability"
            )
        ]

        seeded = []
        for entry in defaults:
            seeded.append(self.upsert(entry))

        return seeded
