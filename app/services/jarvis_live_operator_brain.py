from __future__ import annotations

import os
import re
import json
import uuid
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class JarvisResult:
    ok: bool
    mode: str
    status: str
    message: str
    action: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    next_steps: Optional[List[str]] = None
    raw: Optional[Dict[str, Any]] = None


class JarvisLiveOperatorBrain:
    """
    Main goals:
    1. Stop fake completion.
    2. Make Jarvis more alive and operator-like.
    3. Convert user text into real actions when possible.
    4. Return proof/evidence for every claimed execution.
    """

    def __init__(self) -> None:
        self.n8n_base_url = (os.getenv("N8N_BASE_URL") or os.getenv("N8N_CLOUD_URL") or "").rstrip("/")
        self.n8n_api_key = os.getenv("N8N_API_KEY") or os.getenv("N8N_CLOUD_API_KEY") or ""
        self.public_base_url = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8015").rstrip("/")

    def handle(self, text: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = (text or "").strip()
        payload = payload or {}

        if not text:
            return asdict(JarvisResult(
                ok=False,
                mode="dialog",
                status="need_input",
                message="Я на связи. Напиши задачу обычным текстом — я определю, это разговор, команда или пайплайн.",
                next_steps=["Например: создай n8n пайплайн для генерации 12 форм контента и выгрузки в Google Drive."]
            ))

        intent = self.detect_intent(text)

        if intent == "n8n_create_pipeline":
            return asdict(self.create_n8n_content_pipeline(text, payload))

        if intent == "n8n_link_or_status":
            return asdict(self.n8n_status_or_link(text))

        if intent == "health":
            return asdict(self.health())

        return asdict(self.alive_dialog(text))

    def detect_intent(self, text: str) -> str:
        t = text.lower()

        if any(x in t for x in ["здоровье", "health", "статус системы", "проверь систему"]):
            return "health"

        if "n8n" in t and any(x in t for x in ["ссылка", "линк", "открой", "workflow", "пайплайн"]):
            return "n8n_link_or_status"

        create_words = ["создай", "сделай", "построй", "собери", "запусти", "настрой", "create", "make", "build", "setup", "generate", "launch"]
        pipeline_words = ["пайплайн", "pipeline", "workflow", "воркфлоу", "автоматизацию", "цепочку", "automation", "flow"]
        n8n_words = ["n8n", "н8н", "webhook", "гугл диск", "google drive", "drive", "content", "asset", "assets", "контент", "генерац"]

        if any(w in t for w in create_words) and any(w in t for w in pipeline_words) and any(w in t for w in n8n_words):
            return "n8n_create_pipeline"

        return "dialog"

    def health(self) -> JarvisResult:
        checks = {
            "n8n_base_url_present": bool(self.n8n_base_url),
            "n8n_api_key_present": bool(self.n8n_api_key),
            "backend_base_url": self.public_base_url,
            "live_brain": True,
            "honesty_guard": True,
        }

        missing = [k for k, v in checks.items() if v is False]

        if missing:
            return JarvisResult(
                ok=False,
                mode="health",
                status="partial",
                action="health_check",
                message=(
                    "Я живой режим включил, но для реального создания workflow не хватает конфигурации: "
                    + ", ".join(missing)
                    + ". Я не буду врать, что создал n8n workflow, пока нет реального ответа от n8n API."
                ),
                evidence=checks,
                next_steps=[
                    "Добавь N8N_BASE_URL и N8N_API_KEY в .env.",
                    "Перезапусти backend.",
                    "Снова отправь команду на создание pipeline."
                ],
            )

        return JarvisResult(
            ok=True,
            mode="health",
            status="ready",
            action="health_check",
            message="Я в живом операторском режиме. n8n API настроен, могу пробовать создавать реальные workflow и отдавать доказательства.",
            evidence=checks,
        )

    def alive_dialog(self, text: str) -> JarvisResult:
        return JarvisResult(
            ok=True,
            mode="dialog",
            status="understood",
            message=(
                "Понял. Я не буду засыпать тебя лишними вопросами. "
                "Сформулирую задачу как оператор: сначала определяю цель, потом проверяю доступные инструменты, "
                "потом либо выполняю, либо честно говорю, чего не хватает."
            ),
            action="dialog_response",
            next_steps=[
                "Для реального действия напиши: создай n8n пайплайн ...",
                "Для проверки напиши: проверь систему",
                "Для ссылки напиши: дай ссылку на последний n8n workflow"
            ],
        )

    def n8n_status_or_link(self, text: str) -> JarvisResult:
        if not self.n8n_base_url:
            return JarvisResult(
                ok=False,
                mode="n8n",
                status="not_configured",
                action="n8n_status",
                message="Я не могу дать честную ссылку на workflow: N8N_BASE_URL не настроен.",
                next_steps=["Добавь N8N_BASE_URL в .env.", "Если есть API ключ — добавь N8N_API_KEY."]
            )

        if not self.n8n_api_key:
            return JarvisResult(
                ok=False,
                mode="n8n",
                status="no_api_key",
                action="n8n_status",
                message=f"n8n адрес есть: {self.n8n_base_url}, но API ключ не настроен. Поэтому я не могу проверить список workflow.",
                evidence={"n8n_base_url": self.n8n_base_url, "n8n_api_key_present": False},
            )

        try:
            data = self._n8n_request("GET", "/api/v1/workflows")
            workflows = data.get("data", data if isinstance(data, list) else [])
            latest = workflows[0] if workflows else None

            if not latest:
                return JarvisResult(
                    ok=False,
                    mode="n8n",
                    status="empty",
                    action="n8n_list_workflows",
                    message="Я подключился к n8n API, но workflow в ответе не нашёл.",
                    evidence={"n8n_base_url": self.n8n_base_url},
                    raw={"response": data},
                )

            wid = latest.get("id")
            name = latest.get("name", "workflow")
            link = f"{self.n8n_base_url}/workflow/{wid}" if wid else self.n8n_base_url

            return JarvisResult(
                ok=True,
                mode="n8n",
                status="verified",
                action="n8n_latest_workflow",
                message=f"Нашёл последний workflow: {name}\nСсылка: {link}",
                evidence={"workflow_id": wid, "workflow_name": name, "workflow_url": link},
            )

        except Exception as e:
            return JarvisResult(
                ok=False,
                mode="n8n",
                status="error",
                action="n8n_list_workflows",
                message=f"Я попытался проверить n8n, но API вернул ошибку: {type(e).__name__}: {e}",
                evidence={"n8n_base_url": self.n8n_base_url, "n8n_api_key_present": bool(self.n8n_api_key)},
            )

    def create_n8n_content_pipeline(self, text: str, payload: Dict[str, Any]) -> JarvisResult:
        if not self.n8n_base_url or not self.n8n_api_key:
            return JarvisResult(
                ok=False,
                mode="n8n_create",
                status="blocked_missing_config",
                action="create_content_pipeline",
                message=(
                    "Я понял задачу: нужен реальный pipeline с коммуникатором, ТЗ через текст/фото, "
                    "созданием 10–12 форм контента и выгрузкой в Google Drive без потери качества. "
                    "Но я не буду писать 'готово': сейчас не хватает N8N_BASE_URL или N8N_API_KEY, "
                    "поэтому я не могу подтвердить создание workflow через n8n API."
                ),
                evidence={
                    "n8n_base_url_present": bool(self.n8n_base_url),
                    "n8n_api_key_present": bool(self.n8n_api_key),
                },
                next_steps=[
                    "Добавь N8N_BASE_URL=https://daniliyc.app.n8n.cloud",
                    "Добавь N8N_API_KEY=твой_ключ_n8n",
                    "Перезапусти backend",
                    "Повтори команду: создай n8n пайплайн для генерации 12 форм контента и Google Drive"
                ],
            )

        workflow_name = "Jarvis Content Factory - Text Photo to 12 Assets"
        webhook_path = "jarvis-content-factory-" + uuid.uuid4().hex[:8]

        workflow = self._build_content_factory_workflow(workflow_name, webhook_path)

        try:
            created = self._n8n_request("POST", "/api/v1/workflows", workflow)
            workflow_id = created.get("id") or created.get("data", {}).get("id")

            if not workflow_id:
                return JarvisResult(
                    ok=False,
                    mode="n8n_create",
                    status="created_but_unverified",
                    action="create_content_pipeline",
                    message="n8n API ответил, но я не нашёл workflow_id. Поэтому не считаю задачу полностью выполненной.",
                    evidence={"n8n_response": created},
                )

            # Try activation, but don't fake it if API differs
            activation_status = "unknown"
            activation_error = None
            try:
                self._n8n_request("POST", f"/api/v1/workflows/{workflow_id}/activate")
                activation_status = "requested"
            except Exception as e:
                activation_error = f"{type(e).__name__}: {e}"

            workflow_url = f"{self.n8n_base_url}/workflow/{workflow_id}"
            webhook_url = f"{self.n8n_base_url}/webhook/{webhook_path}"

            return JarvisResult(
                ok=True,
                mode="n8n_create",
                status="created_verified",
                action="create_content_pipeline",
                message=(
                    "Готово — теперь честно: workflow реально создан через n8n API, потому что я получил workflow_id.\n"
                    f"Workflow: {workflow_url}\n"
                    f"Webhook: {webhook_url}\n"
                    "Важно: узлы генерации и Google Drive могут требовать credentials внутри n8n."
                ),
                evidence={
                    "workflow_id": workflow_id,
                    "workflow_url": workflow_url,
                    "webhook_url": webhook_url,
                    "activation_status": activation_status,
                    "activation_error": activation_error,
                },
                next_steps=[
                    "Открой workflow и подключи credentials к Google Drive / AI generation узлам.",
                    "Отправь тестовый POST на webhook с text_prompt или image_url.",
                    "После теста попроси Jarvis проверить результат."
                ],
                raw={"created": created},
            )

        except Exception as e:
            return JarvisResult(
                ok=False,
                mode="n8n_create",
                status="error",
                action="create_content_pipeline",
                message=f"Я попытался создать workflow в n8n, но получил ошибку: {type(e).__name__}: {e}",
                evidence={"n8n_base_url": self.n8n_base_url, "workflow_name": workflow_name},
            )

    def _build_content_factory_workflow(self, name: str, webhook_path: str) -> Dict[str, Any]:
        # Compatible simple n8n workflow skeleton.
        # It intentionally does not pretend to generate real AI content until credentials/nodes are configured.
        return {
            "name": name,
            "nodes": [
                {
                    "parameters": {
                        "path": webhook_path,
                        "httpMethod": "POST",
                        "responseMode": "lastNode",
                        "options": {}
                    },
                    "id": "Webhook",
                    "name": "Communicator Webhook",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2,
                    "position": [0, 0]
                },
                {
                    "parameters": {
                        "jsCode": """
const input = $json || {};
const text = input.text_prompt || input.text || input.message || "";
const imageUrl = input.image_url || input.photo_url || "";
if (!text && !imageUrl) {
  return [{ json: { ok: false, error: "Need text_prompt or image_url", input } }];
}
const contentTypes = [
  "instagram_post",
  "instagram_story",
  "reels_script",
  "caption",
  "hashtags",
  "bio_variant",
  "carousel_plan",
  "short_video_prompt",
  "long_video_prompt",
  "telegram_post",
  "x_twitter_post",
  "google_drive_manifest"
];
return [{
  json: {
    ok: true,
    source_text: text,
    source_image_url: imageUrl,
    content_types: contentTypes,
    instruction: "Generate 10-12 content assets, preserve quality, upload originals/results to Google Drive.",
    created_by: "Jarvis Live Operator"
  }
}];
"""
                    },
                    "id": "ValidateAndPlan",
                    "name": "Validate Input + Build Content Plan",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [280, 0]
                },
                {
                    "parameters": {
                        "jsCode": """
const j = $json;
if (!j.ok) return [{ json: j }];

const assets = j.content_types.map((kind, i) => ({
  index: i + 1,
  kind,
  status: "planned",
  prompt: `Create ${kind} from task: ${j.source_text || j.source_image_url}`,
  note: "Connect this step to OpenAI/Claude/Runway/SD/Drive nodes in n8n for real generation."
}));

return [{
  json: {
    ok: true,
    pipeline: "content_factory",
    assets_count: assets.length,
    assets,
    google_drive: {
      status: "needs_credentials",
      quality_policy: "upload original files and generated assets without recompression"
    }
  }
}];
"""
                    },
                    "id": "CreateAssetPlan",
                    "name": "Create 12 Content Asset Tasks",
                    "type": "n8n-nodes-base.code",
                    "typeVersion": 2,
                    "position": [560, 0]
                },
                {
                    "parameters": {
                        "respondWith": "json",
                        "responseBody": "={{$json}}",
                        "options": {}
                    },
                    "id": "FinalResponse",
                    "name": "Final Honest Response",
                    "type": "n8n-nodes-base.respondToWebhook",
                    "typeVersion": 1,
                    "position": [840, 0]
                }
            ],
            "connections": {
                "Communicator Webhook": {
                    "main": [[{"node": "Validate Input + Build Content Plan", "type": "main", "index": 0}]]
                },
                "Validate Input + Build Content Plan": {
                    "main": [[{"node": "Create 12 Content Asset Tasks", "type": "main", "index": 0}]]
                },
                "Create 12 Content Asset Tasks": {
                    "main": [[{"node": "Final Honest Response", "type": "main", "index": 0}]]
                }
            },
            "settings": {
                "executionOrder": "v1"
            }
        }

    def _n8n_request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.n8n_base_url:
            raise RuntimeError("N8N_BASE_URL is empty")
        if not self.n8n_api_key:
            raise RuntimeError("N8N_API_KEY is empty")

        url = self.n8n_base_url + path
        data = None
        headers = {
            "Accept": "application/json",
            "X-N8N-API-KEY": self.n8n_api_key,
        }

        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {raw[:1000]}") from e