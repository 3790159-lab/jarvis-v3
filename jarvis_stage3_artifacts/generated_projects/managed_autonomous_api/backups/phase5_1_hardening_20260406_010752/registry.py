from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.agents.schemas import AgentRegistryDocument, AgentRegistryEntry, AgentPolicy


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
        doc.updated_at = datetime.utcnow()
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
                entry.updated_at = datetime.utcnow()
                new_agents.append(entry)
                replaced = True
            else:
                new_agents.append(agent)

        if not replaced:
            new_agents.append(entry)

        doc.agents = new_agents
        self.save(doc)
        return entry

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
                allowed_tools=["registry", "mission_state", "task_state", "contracts"]
            ),
            AgentRegistryEntry(
                agent_id="planner_core",
                name="Planner Core",
                role="planner",
                description="Builds plans and task graphs",
                model_profile="reasoning_primary",
                policy=self._policy("dev", 20, 300, "medium", True, True, True, True, True, True),
                allowed_tools=["registry", "contracts"]
            ),
            AgentRegistryEntry(
                agent_id="executor_core",
                name="Executor Core",
                role="executor",
                description="Executes allowed tool actions",
                model_profile="fast_ops",
                policy=self._policy("dev", 20, 300, "medium", True, True, True, True, True, True),
                allowed_tools=["shell", "python", "filesystem", "http"]
            ),
            AgentRegistryEntry(
                agent_id="qa_core",
                name="QA Core",
                role="qa",
                description="Validates outputs, contracts and execution state",
                model_profile="reasoning_primary",
                policy=self._policy("safe", 8, 60, "low", False, False, True, False, False, False),
                allowed_tools=["registry", "contracts", "filesystem_read"]
            ),
            AgentRegistryEntry(
                agent_id="code_core",
                name="Code Core",
                role="code",
                description="Creates and updates code safely",
                model_profile="code_specialist",
                policy=self._policy("dev", 20, 300, "medium", True, True, True, True, True, True),
                allowed_tools=["filesystem", "python", "registry"]
            )
        ]

        seeded = []
        for entry in defaults:
            seeded.append(self.upsert(entry))

        return seeded
