from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerService:
    ACTIVE_PENDING_STATES = {"pending", "approved_ready"}

    def __init__(
        self,
        store: JsonStore,
        event_bus: Any,
        executor: Any,
        evaluator: Any,
        replanner: Any,
        guard_manager: Any = None,
        memory_manager: Any = None,
        mode_manager: Any = None,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.executor = executor
        self.evaluator = evaluator
        self.replanner = replanner
        self.guard_manager = guard_manager
        self.memory_manager = memory_manager
        self.mode_manager = mode_manager
        self._loop_task: Optional[asyncio.Task] = None
        self._stopping = False
        self.poll_interval_seconds = int(os.getenv("AUTONOMY_SCHEDULER_POLL_SECONDS", "5"))

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._stopping = False
        self._loop_task = asyncio.create_task(self._loop(), name="autonomy_scheduler_loop")

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.execute_due_jobs()
            except Exception as exc:
                self.event_bus.publish(
                    "scheduler_error",
                    payload={"error": str(exc)},
                    severity="warning",
                    source="scheduler_service",
                )
            await asyncio.sleep(self.poll_interval_seconds)

    def list_jobs(self) -> List[Dict[str, Any]]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        for job in self.store.list_jobs():
            if job.get("job_id") == job_id:
                return job
        return None

    def _save_job_list(self, jobs: List[Dict[str, Any]]) -> None:
        jobs.sort(key=lambda x: (x.get("run_at") or "", x.get("created_at") or ""))
        self.store.save_jobs(jobs)

    def schedule_continuation(
        self,
        mission_id: str,
        run_at_iso: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        reason: str = "scheduled_continuation",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base = now_utc()
        if run_at_iso:
            run_at = parse_iso(run_at_iso)
        else:
            run_at = base + timedelta(seconds=int(delay_seconds or 0))

        job = {
            "job_id": f"job_{uuid.uuid4().hex[:12]}",
            "kind": "mission_continuation",
            "mission_id": mission_id,
            "status": "pending",
            "reason": reason,
            "payload": payload or {},
            "run_at": run_at.isoformat(),
            "created_at": utc_now_iso(),
            "started_at": None,
            "finished_at": None,
            "last_error": None,
            "attempt_count": 0,
            "approval_id": None,
        }

        jobs = self.store.list_jobs()
        jobs.append(job)
        self._save_job_list(jobs)

        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        bucket["scheduled_count"] = int(bucket.get("scheduled_count", 0)) + 1
        bucket["updated_at"] = utc_now_iso()
        self.store.save_runtime(runtime)

        self.event_bus.publish(
            "schedule_created",
            mission_id=mission_id,
            payload={"job_id": job["job_id"], "run_at": job["run_at"], "reason": reason},
            source="scheduler_service",
        )
        return job

    def pause_mission(self, mission_id: str, reason: str = "operator_pause") -> Dict[str, Any]:
        pauses = self.store.get_pauses()
        pauses[mission_id] = {
            "paused": True,
            "reason": reason,
            "changed_at": utc_now_iso(),
        }
        self.store.save_pauses(pauses)

        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        bucket["paused"] = True
        bucket["pause_reason"] = reason
        bucket["status"] = "paused"
        bucket["updated_at"] = utc_now_iso()
        self.store.save_runtime(runtime)

        self.event_bus.publish(
            "mission_paused",
            mission_id=mission_id,
            payload={"reason": reason},
            source="scheduler_service",
        )

        if self.memory_manager:
            try:
                self.memory_manager.create_snapshot(mission_id=mission_id, trigger="pause")
            except Exception:
                pass

        return {"mission_id": mission_id, "paused": True, "reason": reason}

    def resume_mission(self, mission_id: str, reason: str = "operator_resume", auto_continue: bool = True) -> Dict[str, Any]:
        pauses = self.store.get_pauses()
        if mission_id in pauses:
            pauses.pop(mission_id, None)
            self.store.save_pauses(pauses)

        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        bucket["paused"] = False
        bucket["pause_reason"] = None
        bucket["status"] = "idle"
        bucket["updated_at"] = utc_now_iso()
        self.store.save_runtime(runtime)

        self.event_bus.publish(
            "mission_resumed",
            mission_id=mission_id,
            payload={"reason": reason, "auto_continue": auto_continue},
            source="scheduler_service",
        )

        response: Dict[str, Any] = {
            "mission_id": mission_id,
            "paused": False,
            "reason": reason,
            "scheduled_job": None,
        }

        if auto_continue:
            response["scheduled_job"] = self.schedule_continuation(
                mission_id=mission_id,
                delay_seconds=0,
                reason="resume_triggered_continuation",
            )
        return response

    def mark_job_awaiting_approval(self, job_id: str, approval_id: str, reason: str) -> Optional[Dict[str, Any]]:
        jobs = self.store.list_jobs()
        updated_job = None
        for job in jobs:
            if job.get("job_id") == job_id:
                job["status"] = "awaiting_approval"
                job["approval_id"] = approval_id
                job["last_error"] = reason
                updated_job = job
                break
        self._save_job_list(jobs)
        return updated_job

    def approve_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        jobs = self.store.list_jobs()
        updated_job = None
        for job in jobs:
            if job.get("job_id") == job_id:
                job["status"] = "approved_ready"
                job["run_at"] = now_utc().isoformat()
                job["last_error"] = None
                updated_job = job
                break
        self._save_job_list(jobs)
        return updated_job

    def cancel_job(self, job_id: str, note: str = "cancelled") -> Optional[Dict[str, Any]]:
        jobs = self.store.list_jobs()
        updated_job = None
        for job in jobs:
            if job.get("job_id") == job_id:
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = note
                updated_job = job
                break
        self._save_job_list(jobs)
        return updated_job

    async def execute_due_jobs(self) -> List[Dict[str, Any]]:
        jobs = self.store.list_jobs()
        pauses = self.store.get_pauses()
        current = now_utc()
        updated = []
        results: List[Dict[str, Any]] = []

        for job in jobs:
            if job.get("status") not in self.ACTIVE_PENDING_STATES:
                updated.append(job)
                continue

            run_at_raw = job.get("run_at")
            if not run_at_raw:
                updated.append(job)
                continue

            run_at = parse_iso(run_at_raw)
            if run_at > current:
                updated.append(job)
                continue

            mission_id = job.get("mission_id")
            if mission_id and pauses.get(mission_id, {}).get("paused"):
                job["run_at"] = (current + timedelta(seconds=60)).isoformat()
                job["last_error"] = "Mission paused; continuation deferred by 60 seconds"
                updated.append(job)
                continue

            if mission_id and self.guard_manager:
                guard_result = self.guard_manager.enforce(mission_id, self)
                if guard_result.get("action") in ("auto_paused", "deny_without_pause"):
                    job["run_at"] = (current + timedelta(seconds=180)).isoformat()
                    job["last_error"] = f"Guard blocked execution: {guard_result.get('action')}"
                    updated.append(job)
                    continue

            if mission_id and self.mode_manager and job.get("status") == "pending":
                mode_result = self.mode_manager.evaluate_execution(
                    mission_id=mission_id,
                    reason=job.get("reason", "scheduled_continuation"),
                    origin="scheduled",
                    paused=False,
                    payload={"job_id": job.get("job_id")},
                )
                if not mode_result.get("allowed"):
                    approval = mode_result.get("approval")
                    if approval:
                        job["status"] = "awaiting_approval"
                        job["approval_id"] = approval.get("approval_id")
                        job["last_error"] = f"Mode blocked execution: {mode_result.get('reason')}"
                        updated.append(job)
                        continue

                    job["run_at"] = (current + timedelta(seconds=300)).isoformat()
                    job["last_error"] = f"Mode blocked execution: {mode_result.get('reason')}"
                    updated.append(job)
                    continue

            job["status"] = "running"
            job["started_at"] = utc_now_iso()
            job["attempt_count"] = int(job.get("attempt_count", 0)) + 1

            self.event_bus.publish(
                "schedule_due",
                mission_id=mission_id,
                payload={"job_id": job.get("job_id"), "reason": job.get("reason")},
                source="scheduler_service",
            )

            result = await self.executor.continue_mission(
                mission_id=mission_id,
                reason=job.get("reason", "scheduled_continuation"),
                payload=job.get("payload") or {},
            )

            if result.get("ok"):
                job["status"] = "completed"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = None

                if self.memory_manager:
                    try:
                        self.memory_manager.create_snapshot(mission_id=mission_id, trigger="scheduled_success")
                    except Exception:
                        pass
            else:
                decision = self.evaluator.evaluate(mission_id)
                if decision.get("decision") == "replan":
                    revision = self.replanner.create_revision(
                        mission_id=mission_id,
                        trigger=decision.get("reason", "scheduler_failure"),
                    )
                    self.schedule_continuation(
                        mission_id=mission_id,
                        delay_seconds=180,
                        reason=f"post_replan:{revision['revision_id']}",
                    )
                elif decision.get("decision") == "continue":
                    retry_delay = int(decision.get("recommended_delay_seconds") or 120)
                    self.schedule_continuation(
                        mission_id=mission_id,
                        delay_seconds=retry_delay,
                        reason=f"retry_after:{decision.get('reason', 'unknown')}",
                    )

                if mission_id and self.guard_manager:
                    self.guard_manager.enforce(mission_id, self)

                if self.memory_manager:
                    try:
                        self.memory_manager.create_snapshot(mission_id=mission_id, trigger="scheduled_failure")
                    except Exception:
                        pass

                job["status"] = "failed"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = result.get("error", "Unknown continuation error")

            updated.append(job)
            results.append({"job": job, "result": result})

        self._save_job_list(updated)
        return results
