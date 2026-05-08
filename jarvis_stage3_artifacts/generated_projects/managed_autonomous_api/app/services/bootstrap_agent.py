from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from app.agents.registry import AgentRegistry
from app.agents.schemas import AgentRegistryEntry
from app.agents.policies import build_policy
from app.core.time_utils import utc_now


BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_FILE = BASE_DIR / "config" / "bootstrap_templates.json"
AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _load_templates() -> Dict[str, Any]:
    raw = TEMPLATES_FILE.read_text(encoding="utf-8-sig").strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bootstrap templates must be a JSON object")
    return data


class BootstrapAgentService:
    def __init__(self) -> None:
        self.registry = AgentRegistry()
        self.templates_doc = _load_templates()

    def list_templates(self) -> Dict[str, Any]:
        return self.templates_doc

    def validate_request(self, template_name: str, agent_id: str, name: str) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []

        templates = self.templates_doc.get("templates", {})
        if template_name not in templates:
            errors.append(f"unknown template: {template_name}")

        agent_id = (agent_id or "").strip()
        name = (name or "").strip()

        if not agent_id:
            errors.append("agent_id must not be empty")
        if not name:
            errors.append("name must not be empty")
        if " " in agent_id:
            errors.append("agent_id must not contain spaces")
        if len(agent_id) > 64:
            errors.append("agent_id is too long")
        if agent_id and not AGENT_ID_PATTERN.match(agent_id):
            errors.append("agent_id contains unsupported characters")

        existing = self.registry.get(agent_id) if agent_id else None
        if existing is not None:
            warnings.append(f"agent_id already exists and will be updated: {agent_id}")

        if template_name == "bootstrap_agent":
            warnings.append("bootstrap_agent template is privileged and should be used sparingly")

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

    def create_from_template(self, template_name: str, agent_id: str, name: str, owner: str = "system") -> Dict[str, Any]:
        validation = self.validate_request(template_name, agent_id, name)
        if not validation["ok"]:
            return {
                "status": "error",
                "validation": validation
            }

        template = self.templates_doc["templates"][template_name]

        entry = AgentRegistryEntry(
            agent_id=agent_id,
            name=name,
            role=template["role"],
            description=template["description"],
            model_profile=template["model_profile"],
            policy=build_policy(template["policy_profile"]),
            allowed_tools=template["allowed_tools"],
            owner=owner,
            created_by="bootstrap_agent_service",
            metadata={
                "template_name": template_name,
                "bootstrap_created_at": utc_now().isoformat()
            }
        )

        self.registry.upsert(entry)

        created = self.registry.get(agent_id)

        return {
            "status": "ok",
            "validation": validation,
            "agent": entry.model_dump(mode="json"),
            "post_check": {
                "agent_exists": created is not None,
                "role": None if created is None else created.role
            }
        }

    def self_check(self) -> Dict[str, Any]:
        templates = self.templates_doc.get("templates", {})
        checks = []

        for template_name, template in templates.items():
            checks.append({
                "template_name": template_name,
                "has_role": bool(template.get("role")),
                "has_model_profile": bool(template.get("model_profile")),
                "has_policy_profile": bool(template.get("policy_profile")),
                "has_allowed_tools": isinstance(template.get("allowed_tools"), list)
            })

        ok = all(
            item["has_role"] and
            item["has_model_profile"] and
            item["has_policy_profile"] and
            item["has_allowed_tools"]
            for item in checks
        )

        return {
            "status": "ok" if ok else "error",
            "checks": checks
        }
