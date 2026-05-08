from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.jarvis_truth_guard import JarvisTruthGuard


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JarvisExecutionVerifier:
    """
    Real Execution Lock + Auto Retry + Evidence Gate.

    Rule:
    - A task is not "really completed" unless it has evidence.
    - If evidence is missing, mark needs_retry or blocked.
    - Save verification reports for every run.
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "jarvis_stage3_artifacts" / "execution_verifier"
        self.root.mkdir(parents=True, exist_ok=True)
        self.truth = JarvisTruthGuard(self.project_root)

    def _find_recursive(self, obj: Any, keys: List[str]) -> Optional[Any]:
        keys_lower = {str(k).lower() for k in keys}
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower() in keys_lower and v:
                    return v
                found = self._find_recursive(v, keys)
                if found:
                    return found
        elif isinstance(obj, list):
            for x in obj:
                found = self._find_recursive(x, keys)
                if found:
                    return found
        return None

    def _extract_primary_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(result.get("execution"), dict):
            execution = result["execution"]
            if isinstance(execution.get("primary_result"), dict):
                return execution["primary_result"]
            return execution
        if isinstance(result.get("primary_result"), dict):
            return result["primary_result"]
        return result

    def _task_text(self, result: Dict[str, Any], fallback: str = "") -> str:
        candidates = [
            result.get("task"),
            result.get("objective"),
            result.get("raw_task"),
            result.get("user_task"),
        ]
        if isinstance(result.get("execution"), dict):
            candidates += [
                result["execution"].get("task"),
                result["execution"].get("raw_task"),
                result["execution"].get("objective"),
            ]
        if isinstance(result.get("compiled_task"), dict):
            candidates += [
                result["compiled_task"].get("raw_task"),
                result["compiled_task"].get("objective"),
            ]
        for c in candidates:
            if c:
                return str(c)
        return fallback

    def verify(self, task_text: str, result: Dict[str, Any], attempt: int = 1, max_attempts: int = 2) -> Dict[str, Any]:
        primary = self._extract_primary_result(result or {})
        truth = self.truth.guard_summary(task_text, primary)

        lane = self._find_recursive(primary, ["lane"])
        workflow_id = self._find_recursive(primary, ["workflow_id", "workflowId"])
        workflow_status = self._find_recursive(primary, ["status"])
        run_id = self._find_recursive(primary, ["run_id"])
        artifact = self._find_recursive(primary, ["artifact", "artifact_path", "artifacts_dir", "path"])
        spreadsheet_url = truth.get("evidence", {}).get("spreadsheet_url")

        evidence = {
            "lane": lane,
            "workflow_id": workflow_id,
            "workflow_status": workflow_status,
            "run_id": run_id,
            "artifact": artifact,
            "spreadsheet_url": spreadsheet_url,
            "truth_ok": truth.get("ok"),
            "truth_claim_type": truth.get("claim_type"),
            "truth_missing": truth.get("missing_evidence", []),
        }

        has_code_evidence = lane == "code_improvement_lane" and bool(run_id)
        has_n8n_evidence = bool(workflow_id) and bool(workflow_status)
        has_sheet_evidence = bool(spreadsheet_url)
        has_artifact_evidence = bool(artifact)

        ok = bool(truth.get("ok")) and (
            has_code_evidence or has_n8n_evidence or has_sheet_evidence or has_artifact_evidence
        )

        task_lower = (task_text or "").lower()
        external_intent = any(x in task_lower for x in [
            "google", "гугл", "таблиц", "spreadsheet",
            "n8n", "workflow", "webhook", "gmail", "calendar"
        ])

        if external_intent and not truth.get("ok"):
            ok = False

        if ok:
            verdict = "verified_completed"
            next_action = "archive_and_continue"
        elif attempt < max_attempts:
            verdict = "needs_retry"
            next_action = "retry_with_stricter_evidence"
        else:
            verdict = "blocked"
            next_action = "create_fix_task_or_operator_review"

        report = {
            "created_at": utc_now(),
            "task": task_text,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "verdict": verdict,
            "next_action": next_action,
            "evidence": evidence,
            "truth": truth,
        }

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = self.root / f"verify_{stamp}_{attempt}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        report["report_path"] = str(path)

        return report

    def retry_task_text(self, original_task: str, verification: Dict[str, Any]) -> str:
        missing = verification.get("truth", {}).get("missing_evidence", [])
        claim_type = verification.get("truth", {}).get("claim_type", "generic")
        return (
            "Retry with strict evidence gate: "
            f"{original_task}\n\n"
            f"Previous verification failed. Claim type: {claim_type}. "
            f"Missing evidence: {missing}. "
            "Do not claim success unless real API result/artifact is returned. "
            "If impossible, return blocked reason and exact missing connector/credential."
        )