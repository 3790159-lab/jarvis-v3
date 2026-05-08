from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class ContinuationExecutor:
    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus

    async def continue_mission(
        self,
        mission_id: str,
        reason: str = "scheduled_continuation",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)

        if bucket.get("paused"):
            return {
                "ok": False,
                "status": "paused",
                "message": "Mission is paused",
                "mission_id": mission_id,
            }

        started = utc_now_iso()
        bucket["status"] = "running"
        bucket["last_continue_at"] = started
        bucket["continue_count"] = int(bucket.get("continue_count", 0)) + 1
        bucket["updated_at"] = started
        self.store.save_runtime(runtime)

        self.event_bus.publish(
            "mission_continue_started",
            mission_id=mission_id,
            payload={"reason": reason, "input": payload or {}},
            source="continuation_executor",
        )

        try:
            result = await self._run_mission_via_http(mission_id)
            finished = utc_now_iso()

            runtime = self.store.get_runtime()
            bucket = ensure_runtime_bucket(runtime, mission_id)
            bucket["last_run_at"] = finished
            bucket["run_count"] = int(bucket.get("run_count", 0)) + 1
            bucket["status"] = "idle"
            bucket["last_success_at"] = finished
            bucket["consecutive_failures"] = 0
            bucket["updated_at"] = finished
            self.store.save_runtime(runtime)

            self.event_bus.publish(
                "mission_continue_completed",
                mission_id=mission_id,
                payload={"reason": reason, "result": result},
                source="continuation_executor",
            )

            return {
                "ok": True,
                "status": "completed",
                "mission_id": mission_id,
                "reason": reason,
                "result": result,
            }
        except Exception as exc:
            finished = utc_now_iso()

            runtime = self.store.get_runtime()
            bucket = ensure_runtime_bucket(runtime, mission_id)
            bucket["last_run_at"] = finished
            bucket["status"] = "attention"
            bucket["last_failure_at"] = finished
            bucket["failure_count"] = int(bucket.get("failure_count", 0)) + 1
            bucket["consecutive_failures"] = int(bucket.get("consecutive_failures", 0)) + 1
            bucket["updated_at"] = finished
            self.store.save_runtime(runtime)

            self.event_bus.publish(
                "mission_continue_failed",
                mission_id=mission_id,
                payload={"reason": reason, "error": str(exc)},
                severity="warning",
                source="continuation_executor",
            )

            return {
                "ok": False,
                "status": "failed",
                "mission_id": mission_id,
                "reason": reason,
                "error": str(exc),
            }

    async def _run_mission_via_http(self, mission_id: str) -> Dict[str, Any]:
        app_host = os.getenv("APP_HOST", "127.0.0.1")
        app_port = os.getenv("APP_PORT", "8015")

        primary_url = f"http://{app_host}:{app_port}/api/missions/{mission_id}/run"
        fallback_url = f"http://{app_host}:{app_port}/api/autonomy/mission-bridge/run/{mission_id}"

        def _call_url(url: str) -> Dict[str, Any]:
            req = urllib.request.Request(url=url, method="POST")
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return {"status": "ok", "raw": "", "used_url": url}
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        parsed["used_url"] = url
                    return parsed
                except Exception:
                    return {"status": "ok", "raw": body, "used_url": url}

        def _call() -> Dict[str, Any]:
            try:
                return _call_url(primary_url)
            except Exception as primary_exc:
                try:
                    fallback_result = _call_url(fallback_url)
                    if isinstance(fallback_result, dict):
                        fallback_result["fallback_used"] = True
                        fallback_result["primary_error"] = str(primary_exc)
                    return fallback_result
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"Primary continuation failed: {primary_exc}; "
                        f"Fallback bridge failed: {fallback_exc}"
                    )

        return await asyncio.to_thread(_call)


class ContinuationEvaluator:
    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus

    def evaluate(self, mission_id: str) -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        events = self.store.read_events(mission_id=mission_id, limit=20)

        if bucket.get("paused"):
            return {
                "mission_id": mission_id,
                "decision": "noop",
                "reason": "mission_paused",
                "recommended_delay_seconds": None,
            }

        consecutive_failures = int(bucket.get("consecutive_failures", 0))
        last_success_at = _parse_iso(bucket.get("last_success_at"))
        last_event_at = _parse_iso(bucket.get("last_event_at"))
        now = datetime.now(timezone.utc)

        if consecutive_failures >= 2:
            return {
                "mission_id": mission_id,
                "decision": "replan",
                "reason": "repeated_failures",
                "recommended_delay_seconds": 0,
            }

        if last_event_at and now - last_event_at > timedelta(minutes=20):
            return {
                "mission_id": mission_id,
                "decision": "continue",
                "reason": "mission_stale",
                "recommended_delay_seconds": 0,
            }

        if events:
            last_event = events[-1]
            last_type = last_event.get("type")

            if last_type in ("dependency_unblocked", "schedule_due", "external_signal_continue"):
                return {
                    "mission_id": mission_id,
                    "decision": "continue",
                    "reason": f"event_trigger:{last_type}",
                    "recommended_delay_seconds": 0,
                }

            if last_type in ("mission_continue_failed", "mission_run_failed"):
                return {
                    "mission_id": mission_id,
                    "decision": "continue",
                    "reason": "single_failure_retry",
                    "recommended_delay_seconds": 120,
                }

        if last_success_at and now - last_success_at > timedelta(minutes=30):
            return {
                "mission_id": mission_id,
                "decision": "continue",
                "reason": "success_ttl_expired",
                "recommended_delay_seconds": 0,
            }

        return {
            "mission_id": mission_id,
            "decision": "noop",
            "reason": "no_action_needed",
            "recommended_delay_seconds": None,
        }
