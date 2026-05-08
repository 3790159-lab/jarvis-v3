from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.jarvis_brain_foundation import JarvisBrainFoundation, CompiledTask
from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent
from app.services.jarvis_code_improvement_lane import JarvisCodeImprovementLane


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class BrainExecutionResult:
    execution_id: str
    raw_task: str
    status: str
    compiled_task: Dict[str, Any]
    actions: List[Dict[str, Any]]
    primary_result: Optional[Dict[str, Any]]
    human_summary: str
    next_actions: List[str]
    created_at: str = field(default_factory=utc_now_iso)


class JarvisBrainExecutor:
    """
    Brain Executor v1:
    - compiles task
    - chooses execution lane
    - delegates to n8n_super_agent when automation is needed
    - stores result
    - returns human report
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.brain = JarvisBrainFoundation(self.project_root)
        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "brain_executor")
        self.runs_dir = ensure_dir(self.root / "runs")
        self.runtime_dir = ensure_dir(self.root / "runtime")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _choose_workflow_kind(self, task: str) -> str:
        t = task.lower()
        if any(x in t for x in ["динамич", "dynamic", "сам выбери", "несколько сервис", "разные сервис", "интеллект"]):
            return "dynamic_pipeline"
        if any(x in t for x in ["многошаг", "многоуров", "pipeline", "пайплайн", "цепоч"]):
            return "multi_step_pipeline"
        if any(x in t for x in ["telegram", "телеграм", "alert", "уведом"]):
            return "telegram_operator_alert"
        if any(x in t for x in ["http", "api", "внешн", "endpoint"]):
            return "http_request_probe"
        if any(x in t for x in ["night", "ноч", "отч", "report"]):
            return "night_report_logger"
        return "dynamic_pipeline"

    def execute(self, raw_task: str, dry_run: bool = False) -> BrainExecutionResult:
        compiled = self.brain.compile_task(raw_task)
        tools = set(compiled.tools)
        actions: List[Dict[str, Any]] = []
        primary_result: Optional[Dict[str, Any]] = None
        status = "planned"

        if compiled.risk_level == "high":
            status = "approval_required"
            actions.append({
                "type": "blocked",
                "reason": "high_risk_task_requires_operator_approval",
                "mode": compiled.recommended_mode,
            })
        elif dry_run:
            status = "dry_run_planned"
            actions.append({
                "type": "dry_run",
                "reason": "dry_run=True, execution skipped",
            })
        elif "code_mutation_runtime" in tools and any(x in raw_task.lower() for x in ["fix", "improve", "utf", "encoding", "исправ", "улучш", "ошиб", "bug", "self", "night"]):
            lane = JarvisCodeImprovementLane(self.project_root)
            code_result = lane.run(raw_task)
            primary_result = {
                "lane": "code_improvement_lane",
                "run_id": code_result.run_id,
                "status": code_result.status,
                "changed_files": code_result.changed_files,
                "lessons": code_result.lessons,
                "artifacts_dir": code_result.artifacts_dir,
            }
            actions.append({
                "type": "execute_code_improvement_lane",
                "run_id": code_result.run_id,
                "status": code_result.status,
                "changed_files_count": len(code_result.changed_files),
            })
            status = "completed" if code_result.status == "completed" else "completed_with_warnings"

        elif "n8n_super_agent" in tools:
            workflow_kind = self._choose_workflow_kind(raw_task)
            agent = JarvisN8nSuperAgent(self.project_root)
            n8n_result = agent.run(
                user_task=raw_task,
                workflow_kind=workflow_kind,
                activate=True,
                test_webhook=True,
            )
            primary_result = {
                "lane": "n8n_super_agent",
                "workflow_kind": n8n_result.workflow_kind,
                "workflow_id": n8n_result.workflow_id,
                "status": n8n_result.status,
                "summary": n8n_result.summary,
                "recommendations": n8n_result.recommendations,
            }
            actions.append({
                "type": "execute_n8n_workflow",
                "workflow_kind": n8n_result.workflow_kind,
                "workflow_id": n8n_result.workflow_id,
                "status": n8n_result.status,
            })
            status = "completed" if n8n_result.status == "tested" else "completed_with_warnings"
        else:
            status = "compiled_only"
            actions.append({
                "type": "compiled_only",
                "reason": "No direct execution lane available yet for selected tools",
                "tools": compiled.tools,
            })

        summary = self._human_summary(compiled, status, actions, primary_result)

        next_actions = self._next_actions(compiled, status, primary_result)

        result = BrainExecutionResult(
            execution_id="brainexec_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            raw_task=raw_task,
            status=status,
            compiled_task=asdict(compiled),
            actions=actions,
            primary_result=primary_result,
            human_summary=summary,
            next_actions=next_actions,
        )

        self._write_json(self.runs_dir / f"{result.execution_id}.json", asdict(result))
        self._write_json(self.runtime_dir / "latest_execution.json", asdict(result))
        return result

    def _human_summary(
        self,
        compiled: CompiledTask,
        status: str,
        actions: List[Dict[str, Any]],
        primary: Optional[Dict[str, Any]],
    ) -> str:
        lines = [
            "🧠 Jarvis Brain Executor",
            "",
            f"Статус: {status}",
            f"Интент: {compiled.intent}",
            f"Сложность: {compiled.complexity}",
            f"Риск: {compiled.risk_level}",
            f"Провайдер: {compiled.selected_provider}",
            "",
            "Роли:",
            ", ".join(compiled.selected_agent_roles),
            "",
            "Инструменты:",
            ", ".join(compiled.tools),
        ]

        if primary and primary.get("lane") == "code_improvement_lane":
            lines.extend([
                "",
                "Выполнено через Code Improvement Lane:",
                f"- Run ID: {primary.get('run_id')}",
                f"- Статус: {primary.get('status')}",
                f"- Изменённых/обновлённых файлов: {len(primary.get('changed_files') or [])}",
                f"- Артефакты: {primary.get('artifacts_dir')}",
            ])
        if primary and primary.get("lane") == "n8n_super_agent":
            workflow_id = primary.get("workflow_id")
            workflow_url = f"https://daniliyc.app.n8n.cloud/workflow/{workflow_id}" if workflow_id else "not available"
            lines.extend([
                "",
                "Выполнено через n8n Super Agent:",
                f"- Тип workflow: {primary.get('workflow_kind')}",
                f"- Workflow ID: {workflow_id}",
                f"- Статус workflow: {primary.get('status')}",
                f"- Открыть в n8n: {workflow_url}",
            ])

        lines.extend([
            "",
            "Execution blueprint:",
        ])

        for s in compiled.execution_blueprint:
            lines.append(f"- {s['step']}. {s['name']} [{s['agent']} / {s['tool']}]")

        return "\n".join(lines)

    def _next_actions(self, compiled: CompiledTask, status: str, primary: Optional[Dict[str, Any]]) -> List[str]:
        if status == "completed":
            return [
                "Проверить созданный workflow в n8n UI.",
                "Если всё ок — добавить service connectors: Telegram-send, Google Sheets, Gmail.",
                "Следующий слой: memory learning loop для автоулучшения по результатам задач.",
            ]

        if status == "approval_required":
            return [
                "Подтвердить выполнение вручную.",
                "Снизить риск задачи или запустить в plan-only режиме.",
            ]

        if status == "compiled_only":
            return [
                "Добавить execution adapter для выбранных tools.",
                "Подключить этот тип задачи к Operator Task Center.",
            ]

        return [
            "Проверить warnings.",
            "Запустить валидацию повторно.",
        ]

    def latest(self) -> Dict[str, Any]:
        path = self.runtime_dir / "latest_execution.json"
        if not path.exists():
            return {"found": False}
        return {"found": True, "execution": json.loads(path.read_text(encoding="utf-8"))}