from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

TRUTH_GUARD_VERSION = "hard_reset_google_v3"


class JarvisTruthGuard:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def _norm(self, text: Any) -> str:
        return str(text or "").lower().replace("ё", "е")

    def _extract_urls(self, value: Any) -> List[str]:
        raw = json.dumps(value, ensure_ascii=False, default=str)
        return re.findall(r"https?://[^\s\"'<>]+", raw)

    def _find_value_recursive(self, obj: Any, keys: List[str]) -> Optional[Any]:
        keys_lower = {str(k).lower() for k in keys}
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower() in keys_lower and v:
                    return v
                found = self._find_value_recursive(v, keys)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_value_recursive(item, keys)
                if found:
                    return found
        return None

    def _is_google_sheet_task(self, text: str) -> bool:
        t = self._norm(text)
        keywords = [
            "гугл", "google", "spreadsheet", "sheet",
            "таблиц", "таблица", "таблицу", "таблицы"
        ]
        return any(k in t for k in keywords)

    def _is_n8n_task(self, text: str) -> bool:
        t = self._norm(text)
        return any(k in t for k in ["n8n", "workflow", "воркфлоу", "пайплайн", "webhook"])

    def guard_summary(self, task_text: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = result or {}
        task_text = task_text or ""
        urls = self._extract_urls(result)
        lane = self._find_value_recursive(result, ["lane"])

        base_evidence = {
            "version": TRUTH_GUARD_VERSION,
            "urls": urls,
            "has_result": bool(result),
            "lane": lane,
            "task_norm": self._norm(task_text),
            "google_detected": self._is_google_sheet_task(task_text),
        }

        if self._is_google_sheet_task(task_text):
            spreadsheet_url = next((u for u in urls if "docs.google.com/spreadsheets" in u), None)
            spreadsheet_id = self._find_value_recursive(result, ["spreadsheet_id", "spreadsheetId"])

            if not spreadsheet_id and spreadsheet_url and "/d/" in spreadsheet_url:
                spreadsheet_id = spreadsheet_url.split("/d/", 1)[1].split("/", 1)[0]

            missing = []
            if not spreadsheet_id:
                missing.append("spreadsheet_id")
            if not spreadsheet_url:
                missing.append("spreadsheet_url")

            return {
                "ok": len(missing) == 0,
                "confidence": "high" if not missing else "low",
                "claim_type": "google_sheet_creation",
                "missing_evidence": missing,
                "safe_message": (
                    f"Google Таблица подтверждена: {spreadsheet_url}"
                    if not missing
                    else "Не могу подтвердить Google Таблицу: нет spreadsheet_id и реальной ссылки docs.google.com/spreadsheets."
                ),
                "evidence": {
                    **base_evidence,
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_url": spreadsheet_url,
                },
            }

        if self._is_n8n_task(task_text):
            if lane == "code_improvement_lane":
                run_id = self._find_value_recursive(result, ["run_id"])
                return {
                    "ok": bool(run_id),
                    "confidence": "high" if run_id else "medium",
                    "claim_type": "code_improvement",
                    "missing_evidence": [] if run_id else ["run_id"],
                    "safe_message": f"Code improvement подтверждён: {run_id or 'run_id missing'}",
                    "evidence": {**base_evidence, "run_id": run_id},
                }

            workflow_id = self._find_value_recursive(result, ["workflow_id", "workflowId"])
            status = self._find_value_recursive(result, ["status"])

            missing = []
            if not workflow_id:
                missing.append("workflow_id")
            if not status:
                missing.append("status")

            return {
                "ok": len(missing) == 0,
                "confidence": "high" if not missing else "medium",
                "claim_type": "n8n_workflow_creation",
                "missing_evidence": missing,
                "safe_message": (
                    f"n8n workflow подтверждён: {workflow_id}, status={status}"
                    if not missing
                    else "Не могу подтвердить n8n workflow: не хватает workflow_id или статуса."
                ),
                "evidence": {**base_evidence, "workflow_id": workflow_id, "workflow_status": status},
            }

        artifact = self._find_value_recursive(result, ["artifact", "artifact_path", "artifacts_dir", "path"])
        run_id = self._find_value_recursive(result, ["run_id"])

        return {
            "ok": True,
            "confidence": "medium" if artifact or run_id else "low",
            "claim_type": "generic_task",
            "missing_evidence": [] if artifact or run_id else ["specific_artifact_or_external_confirmation"],
            "safe_message": "Задача обработана. Для внешних действий проверяется наличие артефакта или API-подтверждения.",
            "evidence": {**base_evidence, "artifact": artifact, "run_id": run_id},
        }
