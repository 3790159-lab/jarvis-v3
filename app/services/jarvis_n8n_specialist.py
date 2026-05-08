from __future__ import annotations

import json
import os
import socket
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.jarvis_operator_task_center import read_env_file


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class N8nReadiness:
    status: str
    base_url: str
    api_key_present: bool
    tcp_5678_open: bool
    http_root_ok: bool
    public_api_ok: bool
    notes: List[str] = field(default_factory=list)
    next_best_action: str = ""


@dataclass
class N8nWorkflowBlueprint:
    blueprint_id: str
    name: str
    workflow_type: str
    webhook_path: str
    workflow_json: Dict[str, Any]
    production_webhook_url: str
    test_webhook_url: str
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class N8nSpecialistResult:
    result_id: str
    task: str
    status: str
    readiness: Dict[str, Any]
    blueprint: Optional[Dict[str, Any]] = None
    deploy_result: Optional[Dict[str, Any]] = None
    webhook_test: Optional[Dict[str, Any]] = None
    recommendations: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


class JarvisN8nSpecialist:
    """
    n8n Specialist Agent v1.

    Capabilities:
    - readiness checks
    - workflow blueprint generation
    - safe artifact export
    - optional public API deployment if N8N_API_KEY is present
    - webhook test calls
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "n8n_specialist")
        self.blueprints_dir = ensure_dir(self.root / "blueprints")
        self.results_dir = ensure_dir(self.root / "results")
        self.logs_dir = ensure_dir(self.root / "logs")
        self.runtime_dir = ensure_dir(self.root / "runtime")

        env = read_env_file(self.project_root)
        self.base_url = (
            os.environ.get("N8N_BASE_URL")
            or env.get("N8N_BASE_URL")
            or "http://127.0.0.1:5678"
        ).rstrip("/")
        self.api_key = os.environ.get("N8N_API_KEY") or env.get("N8N_API_KEY")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "n8n_specialist.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _tcp_check(self, host: str = "127.0.0.1", port: int = 5678, timeout: float = 1.5) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _http_request(
        self,
        url: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 20,
    ) -> Dict[str, Any]:
        data = None
        final_headers = headers or {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            final_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(body) if body else None
                except Exception:
                    parsed = body[:1000]
                return {
                    "ok": 200 <= resp.status < 300,
                    "status": resp.status,
                    "body": parsed,
                }
        except Exception as exc:
            error_body = None
            status_code = None
            try:
                status_code = getattr(exc, "code", None)
                if hasattr(exc, "read"):
                    raw_error = exc.read().decode("utf-8", errors="replace")
                    try:
                        error_body = json.loads(raw_error)
                    except Exception:
                        error_body = raw_error[:2000]
            except Exception:
                pass

            return {
                "ok": False,
                "status": status_code,
                "error": str(exc),
                "error_body": error_body,
            }

    def readiness(self) -> N8nReadiness:
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        tcp_open = self._tcp_check(host=host, port=port)
        root = self._http_request(self.base_url, timeout=10)

        public_api_ok = False
        notes: List[str] = []

        if self.api_key:
            api = self._http_request(
                f"{self.base_url}/api/v1/workflows",
                headers={"X-N8N-API-KEY": self.api_key},
                timeout=15,
            )
            public_api_ok = bool(api.get("ok"))
            if not public_api_ok:
                notes.append("N8N_API_KEY present, but public API check failed.")
        else:
            notes.append("N8N_API_KEY missing: deploy will stay artifact-only.")

        if not tcp_open:
            notes.append("n8n TCP port/base URL is not reachable.")
        if not root.get("ok"):
            notes.append("n8n root HTTP check failed.")

        if tcp_open and root.get("ok") and self.api_key and public_api_ok:
            status = "ready_for_deploy"
            next_best_action = "You can create workflows through the n8n public API."
        elif tcp_open and root.get("ok"):
            status = "ready_for_blueprints"
            next_best_action = "Generate workflow blueprints and add N8N_API_KEY to enable deploy."
        else:
            status = "not_ready"
            next_best_action = "Start n8n and verify N8N_BASE_URL."

        readiness = N8nReadiness(
            status=status,
            base_url=self.base_url,
            api_key_present=bool(self.api_key),
            tcp_5678_open=tcp_open,
            http_root_ok=bool(root.get("ok")),
            public_api_ok=public_api_ok,
            notes=notes,
            next_best_action=next_best_action,
        )
        self._write_json(self.runtime_dir / "readiness.json", asdict(readiness))
        return readiness

    def build_webhook_logger_blueprint(self, name: str = "Jarvis Night Report Webhook") -> N8nWorkflowBlueprint:
        blueprint_id = "n8nblue_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        path = "jarvis-night-report-" + blueprint_id[-8:]

        webhook_node_id = str(uuid.uuid4())
        code_node_id = str(uuid.uuid4())

        workflow = {
            "name": name,
            "nodes": [
                {
                    "id": webhook_node_id,
                    "name": "Jarvis Webhook",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2,
                    "position": [240, 300],
                    "parameters": {
                        "httpMethod": "POST",
                        "path": path,
                        "responseMode": "lastNode",
                        "options": {},
                    },
                },
                {
                    "id": code_node_id,
                    "name": "Normalize Payload",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [520, 300],
                    "parameters": {
                        "jsCode": "const now = new Date().toISOString();\nreturn [{ json: { ok: true, receivedAt: now, source: 'jarvis', payload: $json } }];"
                    },
                },
            ],
            "connections": {
                "Jarvis Webhook": {
                    "main": [
                        [
                            {
                                "node": "Normalize Payload",
                                "type": "main",
                                "index": 0,
                            }
                        ]
                    ]
                }
            },
            "settings": {},
        }

        bp = N8nWorkflowBlueprint(
            blueprint_id=blueprint_id,
            name=name,
            workflow_type="webhook_logger",
            webhook_path=path,
            workflow_json=workflow,
            production_webhook_url=f"{self.base_url}/webhook/{path}",
            test_webhook_url=f"{self.base_url}/webhook-test/{path}",
        )

        self._write_json(self.blueprints_dir / f"{blueprint_id}.json", asdict(bp))
        self._write_json(self.blueprints_dir / f"{blueprint_id}_workflow_import.json", workflow)
        return bp

    def deploy_blueprint(self, blueprint: N8nWorkflowBlueprint) -> Dict[str, Any]:
        if not self.api_key:
            return {
                "ok": False,
                "mode": "artifact_only",
                "reason": "N8N_API_KEY missing",
                "next_best_action": "Create an n8n API key and set N8N_API_KEY in .env.",
            }

        result = self._http_request(
            f"{self.base_url}/api/v1/workflows",
            method="POST",
            payload=blueprint.workflow_json,
            headers={"X-N8N-API-KEY": self.api_key},
            timeout=30,
        )
        result["mode"] = "public_api_create_workflow"
        return result

    def test_webhook(self, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {
            "source": "jarvis",
            "kind": "n8n_specialist_test",
            "sent_at": utc_now_iso(),
        }
        return self._http_request(url, method="POST", payload=payload, timeout=20)

    def handle_task(self, task: str, deploy: bool = False, test_webhook: bool = False) -> N8nSpecialistResult:
        readiness = self.readiness()
        blueprint = self.build_webhook_logger_blueprint()

        deploy_result = None
        webhook_test = None
        recommendations: List[str] = []

        if deploy:
            deploy_result = self.deploy_blueprint(blueprint)
            if not deploy_result.get("ok"):
                recommendations.append("Deploy did not complete. Use exported workflow JSON or configure N8N_API_KEY.")

        if test_webhook:
            # Test webhooks in n8n require editor/test listener to be active.
            webhook_test = self.test_webhook(blueprint.test_webhook_url)
            if not webhook_test.get("ok"):
                recommendations.append(
                    "Webhook test failed. In n8n editor, open workflow and click 'Listen for test event', or deploy/activate workflow and use production URL."
                )

        if readiness.status == "ready_for_blueprints":
            recommendations.append("n8n is reachable. Add N8N_API_KEY for automatic workflow creation.")
        elif readiness.status == "ready_for_deploy":
            recommendations.append("n8n API is ready. Next step: deploy blueprint and activate workflow.")
        else:
            recommendations.append("n8n is not fully reachable. Start n8n and check N8N_BASE_URL.")

        recommendations.append("You can import the exported *_workflow_import.json file into n8n manually.")

        status = "completed"
        if readiness.status == "not_ready":
            status = "degraded"

        result = N8nSpecialistResult(
            result_id="n8nres_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            task=task,
            status=status,
            readiness=asdict(readiness),
            blueprint=asdict(blueprint),
            deploy_result=deploy_result,
            webhook_test=webhook_test,
            recommendations=recommendations,
        )

        self._write_json(self.results_dir / f"{result.result_id}.json", asdict(result))
        self._write_json(self.runtime_dir / "latest_result.json", asdict(result))
        return result

    def latest_result(self) -> Dict[str, Any]:
        path = self.runtime_dir / "latest_result.json"
        if not path.exists():
            return {"found": False}
        return {"found": True, "result": json.loads(path.read_text(encoding="utf-8"))}

    def format_human_summary(self, result: N8nSpecialistResult | Dict[str, Any]) -> str:
        if isinstance(result, N8nSpecialistResult):
            data = asdict(result)
        else:
            data = result

        readiness = data.get("readiness", {})
        bp = data.get("blueprint") or {}

        lines = [
            "🧩 n8n Specialist Report",
            "",
            f"Статус: {data.get('status')}",
            f"n8n readiness: {readiness.get('status')}",
            f"Base URL: {readiness.get('base_url')}",
            f"API key: {'есть' if readiness.get('api_key_present') else 'нет'}",
            f"HTTP root: {'ok' if readiness.get('http_root_ok') else 'fail'}",
            f"Public API: {'ok' if readiness.get('public_api_ok') else 'not ready'}",
            "",
        ]

        if bp:
            lines.extend([
                "Workflow blueprint:",
                f"Название: {bp.get('name')}",
                f"Webhook path: {bp.get('webhook_path')}",
                f"Test URL: {bp.get('test_webhook_url')}",
                f"Production URL: {bp.get('production_webhook_url')}",
                "",
            ])

        if data.get("deploy_result"):
            lines.append(f"Deploy: {data['deploy_result']}")

        if data.get("webhook_test"):
            lines.append(f"Webhook test: {data['webhook_test']}")

        recs = data.get("recommendations") or []
        if recs:
            lines.append("Что дальше:")
            for r in recs[:6]:
                lines.append(f"• {r}")

        return "\n".join(lines)