from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.jarvis_operator_task_center import read_env_file


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class ProviderProfile:
    name: str
    configured: bool
    best_for: List[str]
    mode: str
    priority: int


@dataclass
class CompiledTask:
    task_id: str
    raw_task: str
    intent: str
    complexity: str
    risk_level: str
    recommended_mode: str
    selected_provider: str
    selected_agent_roles: List[str]
    tools: List[str]
    execution_blueprint: List[Dict[str, Any]]
    success_criteria: List[str]
    validation_plan: List[str]
    rollback_plan: List[str]
    missing_requirements: List[str]
    notes: List[str]
    created_at: str = field(default_factory=utc_now_iso)


class JarvisBrainFoundation:
    """
    Jarvis Brain Foundation v1:
    - task compiler
    - provider/router decision
    - agent role planner
    - tool selection
    - validation/rollback blueprint
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "brain_foundation")
        self.compiled_dir = ensure_dir(self.root / "compiled_tasks")
        self.runtime_dir = ensure_dir(self.root / "runtime")
        self.env = read_env_file(self.project_root)

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def provider_profiles(self) -> List[ProviderProfile]:
        openai_ok = bool(os.environ.get("OPENAI_API_KEY") or self.env.get("OPENAI_API_KEY"))
        anthropic_ok = bool(os.environ.get("ANTHROPIC_API_KEY") or self.env.get("ANTHROPIC_API_KEY"))
        gemini_ok = bool(os.environ.get("GEMINI_API_KEY") or self.env.get("GEMINI_API_KEY") or self.env.get("GOOGLE_API_KEY"))
        ollama_url = os.environ.get("LLM_BASE_URL") or self.env.get("LLM_BASE_URL") or "http://127.0.0.1:11434"

        return [
            ProviderProfile(
                name="openai",
                configured=openai_ok,
                best_for=["reasoning", "coding", "task_compilation", "n8n_planning", "summaries"],
                mode="cloud",
                priority=10,
            ),
            ProviderProfile(
                name="anthropic",
                configured=anthropic_ok,
                best_for=["large_code_changes", "architecture_review", "safe_refactor", "long_context_code"],
                mode="cloud",
                priority=9,
            ),
            ProviderProfile(
                name="gemini",
                configured=gemini_ok,
                best_for=["multimodal", "long_context", "documents", "web_style_research"],
                mode="cloud",
                priority=7,
            ),
            ProviderProfile(
                name="ollama",
                configured=bool(ollama_url),
                best_for=["local_private_tasks", "fast_drafts", "offline_support", "filesystem_safe"],
                mode="local",
                priority=5,
            ),
            ProviderProfile(
                name="rule_based",
                configured=True,
                best_for=["fallback", "safe_planning", "diagnostics"],
                mode="local",
                priority=1,
            ),
        ]

    def classify_intent(self, task: str) -> str:
        t = task.lower()

        if any(x in t for x in ["n8n", "workflow", "воркфлоу", "пайплайн", "pipeline", "webhook", "автоматизац"]):
            return "automation_pipeline"

        if any(x in t for x in [
            "код", "исправ", "реализ", "добавь", "backend", "api", "router", "compile", "python", "powershell",
            "fix", "improve", "bug", "error", "self-fix", "self fix", "night loop", "utf", "encoding",
            "validation", "rollback", "lessons", "memory learning", "service registry"
        ]):
            return "code_implementation"

        if any(x in t for x in ["проанализ", "исслед", "найди", "сравни", "подбери", "research"]):
            return "research_analysis"

        if any(x in t for x in ["статус", "что происходит", "результат", "как там", "провер"]):
            return "status_or_diagnostics"

        if any(x in t for x in ["план", "стратег", "архитект", "развит"]):
            return "strategy_planning"

        return "general_task"

    def classify_complexity(self, task: str, intent: str) -> str:
        t = task.lower()
        score = 0

        if len(task) > 500:
            score += 2
        if len(task) > 1200:
            score += 2
        if any(x in t for x in ["многошаг", "многоуров", "несколько", "параллель", "автоном", "агент", "оркестр"]):
            score += 2
        if intent in {"automation_pipeline", "code_implementation"}:
            score += 1
        if any(x in t for x in ["google", "gmail", "calendar", "telegram", "docker", "n8n", "api"]):
            score += 1

        if score >= 5:
            return "high"
        if score >= 3:
            return "medium"
        return "low"

    def classify_risk(self, task: str, intent: str) -> str:
        t = task.lower()

        high = ["delete", "удали", "destroy", "wipe", "секрет", "token", "key", "prod", "production", "database", "db migrate"]
        medium = ["deploy", "activate", "перезапусти", "restart", "docker", "env", "credentials", "api key", "webhook"]

        if any(x in t for x in high):
            return "high"
        if any(x in t for x in medium):
            return "medium"
        if intent in {"code_implementation", "automation_pipeline"}:
            return "medium"
        return "low"

    def select_provider(self, intent: str, complexity: str, risk: str) -> str:
        profiles = self.provider_profiles()
        configured = {p.name: p for p in profiles if p.configured}

        if risk == "high":
            if "openai" in configured:
                return "openai"
            if "anthropic" in configured:
                return "anthropic"
            return "rule_based"

        if intent == "code_implementation" and complexity in {"medium", "high"}:
            if "anthropic" in configured:
                return "anthropic"
            if "openai" in configured:
                return "openai"

        if intent in {"automation_pipeline", "strategy_planning", "research_analysis"}:
            if "openai" in configured:
                return "openai"
            if "anthropic" in configured:
                return "anthropic"

        if intent == "status_or_diagnostics":
            if "ollama" in configured:
                return "ollama"
            return "rule_based"

        if "openai" in configured:
            return "openai"
        if "ollama" in configured:
            return "ollama"
        return "rule_based"

    def select_tools(self, task: str, intent: str) -> List[str]:
        t = task.lower()
        tools: List[str] = []

        if intent == "automation_pipeline" or any(x in t for x in ["n8n", "workflow", "пайплайн", "webhook"]):
            tools.append("n8n_super_agent")

        if any(x in t for x in ["telegram", "телеграм", "сообщ", "alert", "уведом"]):
            tools.append("telegram_bridge")

        if any(x in t for x in ["google", "sheet", "таблиц"]):
            tools.append("google_sheets")

        if any(x in t for x in ["gmail", "почт", "email"]):
            tools.append("gmail")

        if any(x in t for x in ["calendar", "календар", "event"]):
            tools.append("google_calendar")

        if intent == "code_implementation" or any(x in t for x in [
            "код", "python", "powershell", "backend", "api", "router",
            "fix", "improve", "bug", "error", "self-fix", "self fix",
            "night loop", "utf", "encoding", "validation", "rollback",
            "lessons", "memory learning", "service registry"
        ]):
            tools.append("code_mutation_runtime")

        if any(x in t for x in ["docker", "container", "n8n local"]):
            tools.append("docker_runtime")

        if any(x in t for x in ["http", "api", "endpoint", "webhook"]):
            tools.append("http_client")

        if not tools:
            tools.append("operator_task_center")

        return list(dict.fromkeys(tools))

    def select_roles(self, intent: str, complexity: str, risk: str, tools: List[str]) -> List[str]:
        roles = ["Supervisor", "Planner"]

        if intent in {"code_implementation", "automation_pipeline"}:
            roles += ["Architect", "Coder", "Validator"]

        if "n8n_super_agent" in tools:
            roles += ["AutomationArchitect", "n8nSpecialist"]

        if intent == "research_analysis":
            roles += ["Researcher", "Synthesizer"]

        if complexity in {"medium", "high"}:
            roles += ["Optimizer"]

        if risk in {"medium", "high"}:
            roles += ["RiskGuardian"]

        roles += ["Archivist"]
        return list(dict.fromkeys(roles))

    def recommended_mode(self, intent: str, complexity: str, risk: str) -> str:
        if risk == "high":
            return "plan_only_requires_approval"
        if risk == "medium":
            return "safe_apply_with_validation"
        if intent in {"status_or_diagnostics", "general_task"}:
            return "direct_answer_or_light_task"
        return "safe_apply"

    def build_execution_blueprint(self, task: str, intent: str, tools: List[str], mode: str) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = [
            {
                "step": 1,
                "name": "Understand objective",
                "agent": "Planner",
                "tool": "task_compiler",
                "output": "structured objective, constraints, success criteria",
            }
        ]

        idx = 2

        if "n8n_super_agent" in tools:
            steps.append({
                "step": idx,
                "name": "Design automation pipeline",
                "agent": "n8nSpecialist",
                "tool": "n8n_super_agent",
                "output": "workflow plan and deployable n8n JSON",
            })
            idx += 1

        if "code_mutation_runtime" in tools:
            steps.append({
                "step": idx,
                "name": "Prepare safe code change",
                "agent": "Coder",
                "tool": "safe_mutation_runtime",
                "output": "patch candidate",
            })
            idx += 1

        if "http_client" in tools:
            steps.append({
                "step": idx,
                "name": "Verify external endpoint",
                "agent": "Validator",
                "tool": "http_client",
                "output": "HTTP validation result",
            })
            idx += 1

        steps.append({
            "step": idx,
            "name": "Validate result",
            "agent": "Validator",
            "tool": "validation_suite",
            "output": "pass/fail report",
        })
        idx += 1

        steps.append({
            "step": idx,
            "name": "Risk and rollback check",
            "agent": "RiskGuardian",
            "tool": "decision_risk_engine",
            "output": "risk score and rollback readiness",
        })
        idx += 1

        steps.append({
            "step": idx,
            "name": "Summarize and archive",
            "agent": "Archivist",
            "tool": "memory_learning",
            "output": "human report and learning event",
        })

        if mode == "plan_only_requires_approval":
            for s in steps:
                s["execution_policy"] = "plan_only"
        else:
            for s in steps:
                s["execution_policy"] = mode

        return steps

    def success_criteria(self, intent: str, tools: List[str]) -> List[str]:
        criteria = ["Task is completed or a clear blocked reason is returned."]

        if "n8n_super_agent" in tools:
            criteria += [
                "n8n workflow is generated.",
                "Workflow deploy result is captured.",
                "Workflow activation and webhook test are validated when safe.",
            ]

        if "code_mutation_runtime" in tools:
            criteria += [
                "Changed files compile successfully.",
                "Smoke tests pass.",
                "Rollback artifact exists.",
            ]

        criteria.append("Human-readable summary is produced.")
        return criteria

    def validation_plan(self, intent: str, tools: List[str]) -> List[str]:
        plan = ["Compile/check affected modules if code changed."]

        if "n8n_super_agent" in tools:
            plan += [
                "Check n8n readiness.",
                "Deploy workflow in safe mode.",
                "Activate workflow only if deploy succeeds.",
                "Run production webhook test.",
            ]

        if "telegram_bridge" in tools:
            plan.append("Restart bridge and verify command/answer path.")

        plan.append("Store result in artifacts and memory.")
        return plan

    def rollback_plan(self, risk: str, tools: List[str]) -> List[str]:
        plan = ["Keep timestamped backups before changes."]

        if "code_mutation_runtime" in tools:
            plan.append("Restore backed up file if compile/smoke fails.")

        if "n8n_super_agent" in tools:
            plan.append("If workflow is bad, deactivate/archive the workflow in n8n.")

        if risk == "high":
            plan.append("Require explicit operator approval before apply.")

        return plan

    def missing_requirements(self, tools: List[str]) -> List[str]:
        missing = []

        if "n8n_super_agent" in tools:
            if not (os.environ.get("N8N_API_KEY") or self.env.get("N8N_API_KEY")):
                missing.append("N8N_API_KEY")
            if not (os.environ.get("N8N_BASE_URL") or self.env.get("N8N_BASE_URL")):
                missing.append("N8N_BASE_URL")

        if "telegram_bridge" in tools:
            if not (os.environ.get("TELEGRAM_BOT_TOKEN") or self.env.get("TELEGRAM_BOT_TOKEN")):
                missing.append("TELEGRAM_BOT_TOKEN")
            if not (os.environ.get("TELEGRAM_ALLOWED_CHAT_ID") or self.env.get("TELEGRAM_ALLOWED_CHAT_ID")):
                missing.append("TELEGRAM_ALLOWED_CHAT_ID")

        if "google_sheets" in tools:
            if not (self.env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or self.env.get("GOOGLE_OAUTH_TOKEN_JSON")):
                missing.append("Google credentials for Sheets")

        return missing

    def compile_task(self, raw_task: str) -> CompiledTask:
        intent = self.classify_intent(raw_task)
        complexity = self.classify_complexity(raw_task, intent)
        risk = self.classify_risk(raw_task, intent)
        mode = self.recommended_mode(intent, complexity, risk)
        provider = self.select_provider(intent, complexity, risk)
        tools = self.select_tools(raw_task, intent)
        roles = self.select_roles(intent, complexity, risk, tools)
        blueprint = self.build_execution_blueprint(raw_task, intent, tools, mode)

        compiled = CompiledTask(
            task_id="brain_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            raw_task=raw_task,
            intent=intent,
            complexity=complexity,
            risk_level=risk,
            recommended_mode=mode,
            selected_provider=provider,
            selected_agent_roles=roles,
            tools=tools,
            execution_blueprint=blueprint,
            success_criteria=self.success_criteria(intent, tools),
            validation_plan=self.validation_plan(intent, tools),
            rollback_plan=self.rollback_plan(risk, tools),
            missing_requirements=self.missing_requirements(tools),
            notes=[
                "This is the foundation router/compiler. Execution is delegated to existing Jarvis subsystems.",
                "Next layer should connect this compiler directly to operator task center and n8n super agent.",
            ],
        )

        self._write_json(self.compiled_dir / f"{compiled.task_id}.json", asdict(compiled))
        self._write_json(self.runtime_dir / "latest_compiled_task.json", asdict(compiled))
        return compiled

    def latest(self) -> Dict[str, Any]:
        path = self.runtime_dir / "latest_compiled_task.json"
        if not path.exists():
            return {"found": False}
        return {"found": True, "compiled_task": json.loads(path.read_text(encoding="utf-8"))}

    def summary(self, compiled: CompiledTask) -> str:
        lines = [
            "🧠 Jarvis Brain compiled task",
            "",
            f"Intent: {compiled.intent}",
            f"Complexity: {compiled.complexity}",
            f"Risk: {compiled.risk_level}",
            f"Mode: {compiled.recommended_mode}",
            f"Provider: {compiled.selected_provider}",
            "",
            "Agent roles:",
            ", ".join(compiled.selected_agent_roles),
            "",
            "Tools:",
            ", ".join(compiled.tools),
            "",
            "Execution blueprint:",
        ]

        for s in compiled.execution_blueprint:
            lines.append(f"- {s['step']}. {s['name']} [{s['agent']} / {s['tool']}]")

        if compiled.missing_requirements:
            lines.extend(["", "Missing requirements:", ", ".join(compiled.missing_requirements)])

        lines.extend([
            "",
            "Success criteria:",
            *[f"- {x}" for x in compiled.success_criteria],
        ])

        return "\n".join(lines)