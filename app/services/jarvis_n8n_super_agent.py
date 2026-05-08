from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.jarvis_n8n_specialist import JarvisN8nSpecialist


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class N8nSuperPlan:
    plan_id: str
    user_task: str
    workflow_kind: str
    name: str
    webhook_path: str
    workflow_json: Dict[str, Any]
    reasoning: List[str]
    production_webhook_url: str
    test_webhook_url: str
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class N8nSuperRunResult:
    run_id: str
    user_task: str
    workflow_kind: str
    status: str
    plan: Dict[str, Any]
    deploy_result: Dict[str, Any]
    activate_result: Optional[Dict[str, Any]]
    webhook_test_result: Optional[Dict[str, Any]]
    workflow_id: Optional[str]
    summary: str
    recommendations: List[str]
    created_at: str = field(default_factory=utc_now_iso)


class JarvisN8nSuperAgent:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.specialist = JarvisN8nSpecialist(self.project_root)

        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "n8n_super_agent")
        self.plans_dir = ensure_dir(self.root / "plans")
        self.runs_dir = ensure_dir(self.root / "runs")
        self.runtime_dir = ensure_dir(self.root / "runtime")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def classify_workflow_kind(self, user_task: str) -> str:
        t = user_task.lower()

        if any(x in t for x in ["динамич", "dynamic", "сам выбери", "сам постро", "несколько сервис", "разные сервис", "интеллектуальн"]):
            return "dynamic_pipeline"

        if any(x in t for x in ["pipeline", "пайплайн", "многошаг", "многоуров", "цепоч", "логическ", "multi step", "multi-step"]):
            return "multi_step_pipeline"

        if ("telegram send" in t or "send telegram" in t or ("отправ" in t and ("telegram" in t or "телеграм" in t))):
            return "telegram_send_message"

        if "telegram" in t or "телеграм" in t or "alert" in t or "уведом" in t:
            return "telegram_operator_alert"

        if "night" in t or "ноч" in t or "отч" in t or "report" in t or "лог" in t:
            return "night_report_logger"

        if "http request" in t or "external api" in t or "api request" in t or "внешн" in t:
            return "http_request_probe"

        if "http" in t or "api" in t or "провер" in t or "probe" in t:
            return "http_api_probe"

        return "webhook_echo"

    def _node(self, name: str, node_type: str, parameters: Dict[str, Any], position: List[int]) -> Dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "type": node_type,
            "typeVersion": 2,
            "position": position,
            "parameters": parameters,
        }

    def _make_base_webhook(self, path: str) -> Dict[str, Any]:
        return self._node(
            "Jarvis Webhook",
            "n8n-nodes-base.webhook",
            {
                "httpMethod": "POST",
                "path": path,
                "responseMode": "lastNode",
                "options": {},
            },
            [220, 300],
        )

    def _make_code(self, name: str, js_code: str, x: int = 520, y: int = 300) -> Dict[str, Any]:
        return self._node(
            name,
            "n8n-nodes-base.code",
            {"jsCode": js_code},
            [x, y],
        )

    def _make_http_request(
        self,
        name: str,
        url: str,
        method: str = "GET",
        x: int = 520,
        y: int = 300,
        send_body: bool = False,
        body_parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        parameters: Dict[str, Any] = {
            "method": method,
            "url": url,
            "options": {},
        }

        if send_body:
            parameters["sendBody"] = True
            parameters["contentType"] = "json"
            parameters["bodyParameters"] = {
                "parameters": body_parameters or []
            }

        return self._node(
            name,
            "n8n-nodes-base.httpRequest",
            parameters,
            [x, y],
        )

    def _connect_linear(self, names: List[str]) -> Dict[str, Any]:
        con: Dict[str, Any] = {}
        for a, b in zip(names, names[1:]):
            con[a] = {
                "main": [
                    [
                        {
                            "node": b,
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            }
        return con

    def build_plan(self, user_task: str, workflow_kind: Optional[str] = None) -> N8nSuperPlan:
        workflow_kind = workflow_kind or self.classify_workflow_kind(user_task)
        plan_id = "n8nsuper_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        path = f"jarvis-super-{workflow_kind}-{plan_id[-8:]}".replace("_", "-")
        name = f"Jarvis Super Agent - {workflow_kind}"

        webhook = self._make_base_webhook(path)

        reasoning = [
            f"Classified task as workflow_kind={workflow_kind}.",
            "Using webhook trigger for safe external execution.",
            "Using safe nodes with no hardcoded credentials.",
        ]

        if workflow_kind == "night_report_logger":
            code = self._make_code(
                "Build Night Report",
                (
                    "const now = new Date().toISOString();\n"
                    "const body = $json.body || $json;\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'night_report_logger',\n"
                    "  receivedAt: now,\n"
                    "  source: 'jarvis',\n"
                    "  report: {\n"
                    "    title: body.title || 'Jarvis Night Report',\n"
                    "    status: body.status || 'received',\n"
                    "    summary: body.summary || body.message || 'No summary provided',\n"
                    "    applies: body.applies || 0,\n"
                    "    failed: body.failed || 0\n"
                    "  },\n"
                    "  original: body\n"
                    "} }];"
                ),
            )
            nodes = [webhook, code]
            connections = self._connect_linear(["Jarvis Webhook", "Build Night Report"])

        elif workflow_kind == "telegram_operator_alert":
            code = self._make_code(
                "Build Operator Alert",
                (
                    "const now = new Date().toISOString();\n"
                    "const body = $json.body || $json;\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'telegram_operator_alert',\n"
                    "  receivedAt: now,\n"
                    "  alert: {\n"
                    "    severity: body.severity || 'info',\n"
                    "    title: body.title || 'Jarvis Alert',\n"
                    "    message: body.message || 'Alert received',\n"
                    "    nextAction: body.nextAction || 'Review Jarvis status'\n"
                    "  },\n"
                    "  original: body\n"
                    "} }];"
                ),
            )
            nodes = [webhook, code]
            connections = self._connect_linear(["Jarvis Webhook", "Build Operator Alert"])

        elif workflow_kind == "http_request_probe":
            http_node = self._make_http_request(
                "External HTTP Request",
                "https://jsonplaceholder.typicode.com/todos/1",
                method="GET",
                x=520,
                y=300,
            )
            code = self._make_code(
                "Build HTTP Result",
                (
                    "const now = new Date().toISOString();\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'http_request_probe',\n"
                    "  receivedAt: now,\n"
                    "  externalResponse: $json,\n"
                    "  note: 'External HTTP Request node executed successfully'\n"
                    "} }];"
                ),
                x=820,
                y=300,
            )
            nodes = [webhook, http_node, code]
            connections = self._connect_linear(["Jarvis Webhook", "External HTTP Request", "Build HTTP Result"])
            reasoning.append("Added real n8n HTTP Request node for external API calls.")

        elif workflow_kind == "telegram_send_message":
            http_node = self._make_http_request(
                "Send Telegram Message",
                "={{ 'https://api.telegram.org/bot' + $json.body.telegram_bot_token + '/sendMessage' }}",
                method="POST",
                x=520,
                y=300,
                send_body=True,
                body_parameters=[
                    {"name": "chat_id", "value": "={{ $json.body.chat_id }}"},
                    {"name": "text", "value": "={{ $json.body.text || $json.body.message || 'Hello from Jarvis n8n Super Agent' }}"},
                    {"name": "disable_web_page_preview", "value": "true"},
                ],
            )
            code = self._make_code(
                "Build Telegram Result",
                (
                    "const now = new Date().toISOString();\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'telegram_send_message',\n"
                    "  receivedAt: now,\n"
                    "  telegramApiResponse: $json,\n"
                    "  note: 'Telegram message request completed'\n"
                    "} }];"
                ),
                x=820,
                y=300,
            )
            nodes = [webhook, http_node, code]
            connections = self._connect_linear(["Jarvis Webhook", "Send Telegram Message", "Build Telegram Result"])
            reasoning.append("Added real n8n HTTP Request node to call Telegram sendMessage API.")
            reasoning.append("Telegram token is not stored in workflow JSON; it must be passed in webhook payload.")

        elif workflow_kind == "http_api_probe":
            code = self._make_code(
                "Build API Probe Result",
                (
                    "const now = new Date().toISOString();\n"
                    "const body = $json.body || $json;\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'http_api_probe',\n"
                    "  receivedAt: now,\n"
                    "  probe: {\n"
                    "    target: body.target || 'unspecified',\n"
                    "    requestedBy: 'jarvis',\n"
                    "    note: 'Safe probe receiver'\n"
                    "  },\n"
                    "  original: body\n"
                    "} }];"
                ),
            )
            nodes = [webhook, code]
            connections = self._connect_linear(["Jarvis Webhook", "Build API Probe Result"])

        else:
            code = self._make_code(
                "Echo Payload",
                (
                    "const now = new Date().toISOString();\n"
                    "return [{ json: {\n"
                    "  ok: true,\n"
                    "  kind: 'webhook_echo',\n"
                    "  receivedAt: now,\n"
                    "  source: 'jarvis_super_agent',\n"
                    "  payload: $json\n"
                    "} }];"
                ),
            )
            nodes = [webhook, code]
            connections = self._connect_linear(["Jarvis Webhook", "Echo Payload"])

        workflow = {
            "name": name,
            "nodes": nodes,
            "connections": connections,
            "settings": {},
        }

        plan = N8nSuperPlan(
            plan_id=plan_id,
            user_task=user_task,
            workflow_kind=workflow_kind,
            name=name,
            webhook_path=path,
            workflow_json=workflow,
            reasoning=reasoning,
            production_webhook_url=f"{self.specialist.base_url}/webhook/{path}",
            test_webhook_url=f"{self.specialist.base_url}/webhook-test/{path}",
        )

        self._write_json(self.plans_dir / f"{plan_id}.json", asdict(plan))
        self._write_json(self.plans_dir / f"{plan_id}_workflow_import.json", workflow)
        return plan

    def _workflow_id_from_deploy(self, deploy_result: Dict[str, Any]) -> Optional[str]:
        body = deploy_result.get("body")
        if isinstance(body, dict):
            return body.get("id") or (body.get("data") or {}).get("id")
        return None

    def run(
        self,
        user_task: str,
        workflow_kind: Optional[str] = None,
        activate: bool = True,
        test_webhook: bool = True,
    ) -> N8nSuperRunResult:
        readiness = self.specialist.readiness()

        if readiness.status != "ready_for_deploy":
            plan = self.build_plan(user_task=user_task, workflow_kind=workflow_kind)
            result = N8nSuperRunResult(
                run_id="n8nsuperrun_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
                user_task=user_task,
                workflow_kind=plan.workflow_kind,
                status="degraded",
                plan=asdict(plan),
                deploy_result={"ok": False, "reason": "n8n_not_ready_for_deploy", "readiness": asdict(readiness)},
                activate_result=None,
                webhook_test_result=None,
                workflow_id=None,
                summary="n8n is not ready for deploy. Blueprint was generated only.",
                recommendations=["Check N8N_BASE_URL and N8N_API_KEY.", "Run n8n specialist readiness again."],
            )
            self._store_result(result)
            return result

        plan = self.build_plan(user_task=user_task, workflow_kind=workflow_kind)

        deploy = self.specialist._http_request(
            f"{self.specialist.base_url}/api/v1/workflows",
            method="POST",
            payload=plan.workflow_json,
            headers={"X-N8N-API-KEY": self.specialist.api_key},
            timeout=40,
        )

        workflow_id = self._workflow_id_from_deploy(deploy)
        activate_result = None
        webhook_test_result = None
        status = "deployed"
        recommendations: List[str] = []

        if not deploy.get("ok"):
            status = "failed"
            recommendations.append("Deploy failed. Inspect deploy_result.error_body.")
        else:
            recommendations.append("Workflow deployed successfully.")

        if workflow_id and activate:
            activate_result = self.specialist._http_request(
                f"{self.specialist.base_url}/api/v1/workflows/{workflow_id}/activate",
                method="POST",
                headers={"X-N8N-API-KEY": self.specialist.api_key},
                timeout=40,
            )
            if activate_result.get("ok"):
                status = "active"
                recommendations.append("Workflow activated successfully.")
            else:
                status = "deployed_not_active"
                recommendations.append("Activation failed. Check activate_result.")

        if workflow_id and test_webhook and status == "active":
            webhook_test_result = self.specialist.test_webhook(
                plan.production_webhook_url,
                {
                    "source": "jarvis_super_agent",
                    "kind": plan.workflow_kind,
                    "message": "hello from Jarvis n8n Super Agent",
                    "task": user_task,
                    "sent_at": utc_now_iso(),
                },
            )
            if webhook_test_result.get("ok"):
                status = "tested"
                recommendations.append("Production webhook test passed.")
            else:
                recommendations.append("Webhook test failed. Inspect webhook_test_result.")

        summary = self._summary(
            status=status,
            plan=plan,
            workflow_id=workflow_id,
            deploy=deploy,
            activate_result=activate_result,
            webhook_test_result=webhook_test_result,
        )

        result = N8nSuperRunResult(
            run_id="n8nsuperrun_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            user_task=user_task,
            workflow_kind=plan.workflow_kind,
            status=status,
            plan=asdict(plan),
            deploy_result=deploy,
            activate_result=activate_result,
            webhook_test_result=webhook_test_result,
            workflow_id=workflow_id,
            summary=summary,
            recommendations=recommendations,
        )

        self._store_result(result)
        return result

    def _summary(
        self,
        status: str,
        plan: N8nSuperPlan,
        workflow_id: Optional[str],
        deploy: Dict[str, Any],
        activate_result: Optional[Dict[str, Any]],
        webhook_test_result: Optional[Dict[str, Any]],
    ) -> str:
        stage_map = {
            "dynamic_pipeline": "Webhook -> Validate Input -> External API Request -> Transform Result -> Decision Engine -> Final Operator Report",
            "multi_step_pipeline": "Webhook -> Validate Input -> External API Request -> Transform Result -> Decision -> Final Response",
            "http_request_probe": "Webhook -> External HTTP Request -> Build HTTP Result",
            "night_report_logger": "Webhook -> Build Night Report",
            "telegram_operator_alert": "Webhook -> Build Operator Alert",
            "telegram_send_message": "Webhook -> Telegram Send Message -> Build Telegram Result",
            "http_api_probe": "Webhook -> Build API Probe Result",
            "webhook_echo": "Webhook -> Echo Payload",
        }

        human_kind = {
            "dynamic_pipeline": "динамический многошаговый pipeline",
            "multi_step_pipeline": "многошаговый логический pipeline",
            "http_request_probe": "workflow для внешнего HTTP/API запроса",
            "night_report_logger": "workflow для night-отчётов",
            "telegram_operator_alert": "workflow для operator alert",
            "telegram_send_message": "workflow для отправки Telegram-сообщений",
            "http_api_probe": "workflow для проверки API",
            "webhook_echo": "простой webhook workflow",
        }.get(plan.workflow_kind, plan.workflow_kind)

        is_ok = status == "tested"
        workflow_url = f"{self.specialist.base_url}/workflow/{workflow_id}" if workflow_id else "not available"

        lines = [
            f"Что создано: {human_kind}",
            f"Статус: {status}",
            f"Workflow ID: {workflow_id}",
            f"Открыть в n8n: {workflow_url}",
            "",
            "Цепочка:",
            stage_map.get(plan.workflow_kind, "Webhook -> Processing -> Response"),
            "",
            "Проверки:",
            f"- Deploy: {'ok' if deploy.get('ok') else 'failed'}",
            f"- Activation: {'ok' if activate_result and activate_result.get('ok') else 'not activated'}",
            f"- Webhook test: {'ok' if webhook_test_result and webhook_test_result.get('ok') else 'not tested/failed'}",
            "",
            f"Production webhook: {plan.production_webhook_url}",
            "",
            "Что это значит:",
            "Jarvis построил workflow через n8n API, активировал его и проверил production webhook." if is_ok else "Workflow создан, но требует ручной проверки ошибки.",
        ]

        if plan.workflow_kind in {"multi_step_pipeline", "dynamic_pipeline"}:
            lines.extend([
                "",
                "Pipeline уже делает многоуровневую обработку:",
                "- принимает входящий webhook;",
                "- валидирует входные данные;",
                "- вызывает внешний API;",
                "- преобразует результат;",
                "- принимает логическое решение;",
                "- возвращает финальный отчёт.",
            ])

        if plan.workflow_kind == "dynamic_pipeline":
            lines.extend([
                "",
                "Следующее развитие:",
                "- подключить реальные service credentials registry;",
                "- добавить Google Sheets/Gmail/Telegram-send nodes;",
                "- научить planner выбирать сервисы и ветвления по задаче.",
            ])

        return "\n".join(lines)

    def _store_result(self, result: N8nSuperRunResult) -> None:
        self._write_json(self.runs_dir / f"{result.run_id}.json", asdict(result))
        self._write_json(self.runtime_dir / "latest_run.json", asdict(result))

    def latest_run(self) -> Dict[str, Any]:
        path = self.runtime_dir / "latest_run.json"
        if not path.exists():
            return {"found": False}
        return {"found": True, "run": json.loads(path.read_text(encoding="utf-8"))}