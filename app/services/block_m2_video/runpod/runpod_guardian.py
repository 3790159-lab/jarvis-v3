# -*- coding: utf-8 -*-
"""Background guardian that enforces RunPod budget and lifetime limits.

The guardian runs in two modes:

* **continuous** — :meth:`RunpodGuardian.start` launches an asyncio task
  that wakes on ``guardian_check_interval_sec`` and calls
  :meth:`check_once`.
* **one-shot** — :meth:`check_once` is also exposed directly so tests
  (and operator tooling) can run a single pass without spinning up a
  background loop.

Each enforcement decision is appended as a JSON line to
``state/runpod/guardian.jsonl``. Daily spend is read from
``state/runpod/billing.jsonl`` if present.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runpod_client import PodInfo, RunpodApiError, RunpodClient
from .runpod_config import RunpodConfig

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_STATE_DIR = _PROJECT_ROOT / "state" / "runpod"
_GUARDIAN_LOG = _STATE_DIR / "guardian.jsonl"
_BILLING_LOG = _STATE_DIR / "billing.jsonl"


@dataclass
class CheckResult:
    """Outcome of a single guardian pass."""

    timestamp: str
    pods_inspected: int = 0
    stopped_for_lifetime: list[str] = field(default_factory=list)
    emergency_stopped: list[str] = field(default_factory=list)
    daily_spend_usd: float = 0.0
    budget_exceeded: bool = False
    errors: list[str] = field(default_factory=list)


class RunpodGuardian:
    def __init__(
        self,
        client: RunpodClient,
        config: RunpodConfig,
        *,
        state_dir: Path | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._state_dir = state_dir or _STATE_DIR
        self._guardian_log = self._state_dir / "guardian.jsonl"
        self._billing_log = self._state_dir / "billing.jsonl"
        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Start the periodic check loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="runpod-guardian")
        logger.info(
            "RunPod guardian started (interval=%ss, budget=$%.2f, max_lifetime=%dmin)",
            self._config.guardian_check_interval_sec,
            self._config.max_budget_usd_per_day,
            self._config.max_pod_lifetime_min,
        )

    async def stop(self) -> None:
        """Signal the loop to stop and await its completion."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                logger.warning("Guardian did not stop within 10s; cancelling")
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._task = None
        logger.info("RunPod guardian stopped")

    async def _run_loop(self) -> None:
        interval = max(self._config.guardian_check_interval_sec, 1)
        while not self._stop_event.is_set():
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Guardian check_once failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    # -- single pass ----------------------------------------------------------

    async def check_once(self) -> CheckResult:
        now = datetime.now(timezone.utc)
        result = CheckResult(timestamp=now.isoformat())

        try:
            pods = await self._client.list_pods()
        except RunpodApiError as exc:
            logger.error("Guardian: list_pods failed: %s", exc)
            result.errors.append(f"list_pods: {exc}")
            self._record(
                {
                    "ts": result.timestamp,
                    "event": "list_pods_failed",
                    "error": str(exc),
                }
            )
            return result

        result.pods_inspected = len(pods)
        running = [p for p in pods if (p.desired_status or "").upper() == "RUNNING"]

        # 1) Lifetime enforcement
        max_lifetime_sec = self._config.max_pod_lifetime_min * 60
        for pod in running:
            uptime = _pod_uptime_seconds(pod)
            if uptime is None:
                continue
            if uptime > max_lifetime_sec:
                logger.warning(
                    "Guardian: pod %s exceeded lifetime (%.0fs > %ds), stopping",
                    pod.id,
                    uptime,
                    max_lifetime_sec,
                )
                ok = await self._safe_stop(pod.id, reason="lifetime_exceeded")
                if ok:
                    result.stopped_for_lifetime.append(pod.id)
                self._record(
                    {
                        "ts": result.timestamp,
                        "event": "stopped_for_lifetime",
                        "pod_id": pod.id,
                        "uptime_sec": uptime,
                        "limit_sec": max_lifetime_sec,
                        "stopped_ok": ok,
                    }
                )

        # 2) Daily budget enforcement
        spend = self.estimate_daily_spend()
        result.daily_spend_usd = spend
        if spend >= self._config.max_budget_usd_per_day:
            result.budget_exceeded = True
            if self._config.emergency_stop_enabled:
                logger.error(
                    "Guardian: daily budget exceeded ($%.2f >= $%.2f); "
                    "emergency stop",
                    spend,
                    self._config.max_budget_usd_per_day,
                )
                stopped = await self.emergency_stop_all("daily budget exceeded")
                result.emergency_stopped.extend(stopped)
                self._record(
                    {
                        "ts": result.timestamp,
                        "event": "emergency_stop",
                        "reason": "daily_budget_exceeded",
                        "spend_usd": spend,
                        "limit_usd": self._config.max_budget_usd_per_day,
                        "stopped_pod_ids": stopped,
                    }
                )
            else:
                logger.warning(
                    "Guardian: daily budget exceeded but emergency_stop disabled "
                    "(spend=$%.2f, limit=$%.2f)",
                    spend,
                    self._config.max_budget_usd_per_day,
                )
                self._record(
                    {
                        "ts": result.timestamp,
                        "event": "budget_exceeded_no_action",
                        "spend_usd": spend,
                        "limit_usd": self._config.max_budget_usd_per_day,
                    }
                )

        return result

    # -- emergency ------------------------------------------------------------

    async def emergency_stop_all(self, reason: str) -> list[str]:
        try:
            pods = await self._client.list_pods()
        except RunpodApiError as exc:
            logger.error("emergency_stop_all: list_pods failed: %s", exc)
            return []

        stopped: list[str] = []
        for pod in pods:
            if (pod.desired_status or "").upper() != "RUNNING":
                continue
            ok = await self._safe_stop(pod.id, reason=reason)
            if ok:
                stopped.append(pod.id)
        logger.error(
            "emergency_stop_all(reason=%r) stopped %d pod(s): %s",
            reason,
            len(stopped),
            stopped,
        )
        return stopped

    # -- billing --------------------------------------------------------------

    def estimate_daily_spend(self) -> float:
        """Sum entries from ``state/runpod/billing.jsonl`` for today (UTC)."""
        if not self._billing_log.exists():
            return 0.0
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        try:
            with self._billing_log.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("ts") or ""
                    if not ts.startswith(today):
                        continue
                    cost = entry.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        total += float(cost)
        except OSError as exc:
            logger.warning("Could not read billing log: %s", exc)
            return 0.0
        return total

    # -- helpers --------------------------------------------------------------

    async def _safe_stop(self, pod_id: str, *, reason: str) -> bool:
        try:
            return await self._client.stop_pod(pod_id)
        except RunpodApiError as exc:
            logger.error(
                "Guardian: stop_pod(%s) failed (reason=%s): %s",
                pod_id,
                reason,
                exc,
            )
            self._record(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "stop_failed",
                    "pod_id": pod_id,
                    "reason": reason,
                    "error": str(exc),
                }
            )
            return False

    def _record(self, entry: dict[str, Any]) -> None:
        try:
            with self._guardian_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write guardian log: %s", exc)


def _pod_uptime_seconds(pod: PodInfo) -> float | None:
    """Best-effort extraction of the pod's uptime in seconds."""
    runtime = pod.runtime or {}
    uptime = runtime.get("uptimeInSeconds") if isinstance(runtime, dict) else None
    if isinstance(uptime, (int, float)):
        return float(uptime)

    # Fallback: derive uptime from lastStatusChange if present.
    iso = pod.last_status_change
    if iso:
        try:
            ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
        except ValueError:
            return None
    return None
