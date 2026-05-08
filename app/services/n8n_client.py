from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if load_dotenv is not None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)


class N8nClientError(RuntimeError):
    pass


class N8nClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("N8N_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("N8N_API_KEY", "")
        self.webhook_url = os.getenv("N8N_JARVIS_WEBHOOK_URL", "")
        self.webhook_secret = os.getenv("N8N_JARVIS_WEBHOOK_SECRET", "")
        self.timeout = int(os.getenv("N8N_TIMEOUT_SECONDS", "45"))

        if not self.base_url:
            raise N8nClientError("N8N_BASE_URL is not configured")
        if not self.api_key:
            raise N8nClientError("N8N_API_KEY is not configured")

    def _api_headers(self) -> Dict[str, str]:
        return {
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _webhook_headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-JARVIS-SOURCE": "jarvis_v3",
            "X-IDEMPOTENCY-KEY": idempotency_key or str(uuid.uuid4()),
        }
        if self.webhook_secret:
            headers["X-JARVIS-SECRET"] = self.webhook_secret
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
        retries: int = 3,
        retry_delay_seconds: float = 1.5,
    ) -> Any:
        last_error: Optional[Exception] = None
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}

        for attempt in range(1, retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_body,
                    timeout=self.timeout,
                )

                if response.status_code in (401, 403):
                    raise N8nClientError(
                        f"Authorization failed: {response.status_code} {response.text}"
                    )

                if response.status_code in retryable_statuses:
                    last_error = N8nClientError(
                        f"Retryable HTTP {response.status_code}: {response.text}"
                    )
                    if attempt >= retries:
                        break
                    time.sleep(retry_delay_seconds * attempt)
                    continue

                if response.status_code >= 400:
                    raise N8nClientError(
                        f"HTTP {response.status_code}: {response.text}"
                    )

                if not response.text.strip():
                    return {"status": "ok", "code": response.status_code}

                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type.lower():
                    return response.json()

                return {
                    "status": "ok",
                    "code": response.status_code,
                    "text": response.text,
                }

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(retry_delay_seconds * attempt)

            except Exception as exc:
                last_error = exc
                break

        raise N8nClientError(
            f"Request failed after {retries} attempts: {last_error}"
        )

    def list_workflows(self) -> Any:
        return self._request(
            "GET",
            f"{self.base_url}/api/v1/workflows",
            headers=self._api_headers(),
        )

    def get_workflow(self, workflow_id: str) -> Any:
        return self._request(
            "GET",
            f"{self.base_url}/api/v1/workflows/{workflow_id}",
            headers=self._api_headers(),
        )

    def create_workflow(self, workflow_body: Dict[str, Any]) -> Any:
        return self._request(
            "POST",
            f"{self.base_url}/api/v1/workflows",
            headers=self._api_headers(),
            json_body=workflow_body,
        )

    def update_workflow(self, workflow_id: str, workflow_body: Dict[str, Any]) -> Any:
        return self._request(
            "PUT",
            f"{self.base_url}/api/v1/workflows/{workflow_id}",
            headers=self._api_headers(),
            json_body=workflow_body,
        )

    def activate_workflow(self, workflow_id: str, active: bool = True) -> Any:
        return self._request(
            "PATCH",
            f"{self.base_url}/api/v1/workflows/{workflow_id}",
            headers=self._api_headers(),
            json_body={"active": active},
        )

    def trigger_webhook(
        self,
        payload: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        if not self.webhook_url:
            raise N8nClientError("N8N_JARVIS_WEBHOOK_URL is not configured")

        return self._request(
            "POST",
            self.webhook_url,
            headers=self._webhook_headers(idempotency_key=idempotency_key),
            json_body=payload,
        )