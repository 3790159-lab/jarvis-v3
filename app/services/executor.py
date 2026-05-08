from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from app.schemas.execution import (
    MissionExecutionRequest,
    MissionExecutionResult,
    MissionStatus,
    TaskDefinition,
    TaskResult,
    TaskStatus,
    TaskType,
)
from app.services.shell_guard import validate_shell_command
from app.settings import get_settings

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutorService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.semaphore = asyncio.Semaphore(self.settings.MAX_CONCURRENT_TASKS)

    async def execute_mission(
        self,
        request: MissionExecutionRequest,
    ) -> MissionExecutionResult:
        task_map = {task.task_id: task for task in request.tasks}
        completed: dict[str, TaskResult] = {}
        pending = {task.task_id for task in request.tasks if task.enabled}

        while pending:
            ready_batch = [
                task_map[task_id]
                for task_id in list(pending)
                if self._dependencies_satisfied(task_map[task_id], completed)
            ]

            if not ready_batch:
                unresolved = ", ".join(sorted(pending))
                logger.error(
                    "Dependency deadlock in mission %s: %s",
                    request.mission_id,
                    unresolved,
                )

                for task_id in list(pending):
                    task = task_map[task_id]
                    completed[task_id] = TaskResult(
                        task_id=task.task_id,
                        title=task.title,
                        type=task.type,
                        status=TaskStatus.SKIPPED,
                        message="Skipped due to unresolved dependencies",
                        error="Dependency deadlock",
                        started_at=utc_now_iso(),
                        finished_at=utc_now_iso(),
                        duration_ms=0,
                    )
                    pending.discard(task_id)
                break

            results = await asyncio.gather(
                *(
                    self._execute_single_task(task, request.context, completed)
                    for task in ready_batch
                )
            )

            for result in results:
                completed[result.task_id] = result
                pending.discard(result.task_id)

                original_task = task_map[result.task_id]
                if result.status == TaskStatus.FAILED and not original_task.continue_on_error:
                    for task_id in list(pending):
                        pending_task = task_map[task_id]
                        if result.task_id in pending_task.depends_on:
                            completed[task_id] = TaskResult(
                                task_id=pending_task.task_id,
                                title=pending_task.title,
                                type=pending_task.type,
                                status=TaskStatus.SKIPPED,
                                message=f"Skipped because dependency '{result.task_id}' failed",
                                error="Dependency failed",
                                started_at=utc_now_iso(),
                                finished_at=utc_now_iso(),
                                duration_ms=0,
                            )
                            pending.discard(task_id)

        ordered_results = [
            completed[task.task_id]
            for task in request.tasks
            if task.task_id in completed
        ]

        mission_status = self._calculate_mission_status(ordered_results)
        summary = self._build_summary(mission_status, ordered_results)

        return MissionExecutionResult(
            mission_id=request.mission_id,
            status=mission_status,
            summary=summary,
            task_results=ordered_results,
        )

    def _dependencies_satisfied(
        self,
        task: TaskDefinition,
        completed: dict[str, TaskResult],
    ) -> bool:
        for dep in task.depends_on:
            if dep not in completed:
                return False
            if completed[dep].status not in {TaskStatus.COMPLETED, TaskStatus.SKIPPED}:
                return False
        return True

    async def _execute_single_task(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
        completed: dict[str, TaskResult],
    ) -> TaskResult:
        async with self.semaphore:
            started_at = utc_now_iso()
            started_monotonic = time.perf_counter()

            result = TaskResult(
                task_id=task.task_id,
                title=task.title,
                type=task.type,
                status=TaskStatus.RUNNING,
                started_at=started_at,
            )

            execution_mode = self.settings.EXECUTION_MODE.lower()

            if execution_mode in {"safe", "dry_run"}:
                await asyncio.sleep(0.05)
                result.status = TaskStatus.COMPLETED
                result.message = f"Task simulated in {execution_mode} mode"
                result.output = {
                    "mode": execution_mode,
                    "payload_preview": task.payload,
                }
                result.finished_at = utc_now_iso()
                result.duration_ms = int((time.perf_counter() - started_monotonic) * 1000)
                return result

            retries = (
                task.max_retries
                if task.max_retries is not None
                else self.settings.TASK_MAX_RETRIES
            )
            timeout = task.timeout_seconds or self.settings.TASK_DEFAULT_TIMEOUT_SECONDS

            last_error: str | None = None

            for attempt in range(1, retries + 2):
                result.attempt_count = attempt

                try:
                    execution = await asyncio.wait_for(
                        self._dispatch_task(task, context, completed),
                        timeout=timeout,
                    )
                    result.status = TaskStatus.COMPLETED
                    result.message = execution.get("message", "Task completed")
                    result.output = execution
                    result.error = None
                    break
                except Exception as exc:
                    last_error = str(exc)
                    logger.exception(
                        "Task %s failed on attempt %s",
                        task.task_id,
                        attempt,
                    )

                    if attempt <= retries:
                        await asyncio.sleep(self.settings.TASK_RETRY_BACKOFF_SECONDS * attempt)
                        continue

                    result.status = TaskStatus.FAILED
                    result.message = "Task failed"
                    result.error = last_error

            result.finished_at = utc_now_iso()
            result.duration_ms = int((time.perf_counter() - started_monotonic) * 1000)
            return result

    async def _dispatch_task(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
        completed: dict[str, TaskResult],
    ) -> dict[str, Any]:
        if task.type == TaskType.HTTP_REQUEST:
            return await self._execute_http_request(task, context)

        if task.type == TaskType.N8N_WEBHOOK:
            return await self._execute_n8n_webhook(task, context)

        if task.type == TaskType.SHELL_COMMAND:
            return await self._execute_shell_command(task, context)

        if task.type == TaskType.FILE_WRITE:
            return await self._execute_file_write(task, context)

        if task.type == TaskType.LLM_TASK:
            return await self._execute_llm_task(task, context, completed)

        raise ValueError(f"Unsupported task type: {task.type}")

    async def _execute_http_request(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = task.payload
        method = str(payload.get("method", "POST")).upper()
        url = str(payload["url"])
        headers = payload.get("headers", {})
        body = payload.get("body", {})

        timeout = aiohttp.ClientTimeout(total=self.settings.HTTP_DEFAULT_TIMEOUT_SECONDS)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
                ssl=self.settings.HTTP_VERIFY_SSL,
            ) as response:
                text = await response.text()
                return {
                    "message": f"HTTP {method} completed with status {response.status}",
                    "status_code": response.status,
                    "response_text": text[:4000],
                    "url": url,
                }

    async def _execute_n8n_webhook(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = task.payload
        path = payload.get("path") or self.settings.N8N_DEFAULT_WEBHOOK_PATH
        url = payload.get("url") or f"{self.settings.N8N_BASE_URL.rstrip('/')}{path}"
        body = payload.get("body", {})

        enriched_body = {
            "mission_context": context,
            "task_payload": body,
        }

        http_task = TaskDefinition(
            task_id=task.task_id,
            title=task.title,
            type=TaskType.HTTP_REQUEST,
            payload={
                "method": "POST",
                "url": url,
                "headers": {"Content-Type": "application/json"},
                "body": enriched_body,
            },
        )

        result = await self._execute_http_request(http_task, context)
        result["message"] = f"n8n webhook triggered successfully ({url})"
        return result

    async def _execute_shell_command(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = task.payload
        command = str(payload["command"])
        working_dir = str(payload.get("cwd") or self.settings.default_project_root)

        validate_shell_command(command)

        cwd_path = Path(working_dir).resolve()
        base_path = self.settings.default_project_root.resolve()

        if base_path not in cwd_path.parents and cwd_path != base_path:
            raise ValueError(
                f"Working directory '{cwd_path}' is outside DEFAULT_PROJECT_ROOT"
            )

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        stdout_text = stdout.decode(errors="replace")[:6000]
        stderr_text = stderr.decode(errors="replace")[:6000]

        if process.returncode != 0:
            raise RuntimeError(
                f"Shell command failed with exit code {process.returncode}. stderr: {stderr_text}"
            )

        return {
            "message": "Shell command completed successfully",
            "command": command,
            "cwd": str(cwd_path),
            "returncode": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }

    async def _execute_file_write(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = task.payload
        relative_path = str(payload["path"])
        content = str(payload.get("content", ""))
        mode = str(payload.get("mode", "w"))
        encoding = str(payload.get("encoding", "utf-8"))

        full_path = (self.settings.default_project_root / relative_path).resolve()
        base_path = self.settings.default_project_root.resolve()

        if base_path not in full_path.parents and full_path != base_path:
            raise ValueError(f"Write path '{full_path}' is outside DEFAULT_PROJECT_ROOT")

        full_path.parent.mkdir(parents=True, exist_ok=True)

        if "b" in mode:
            raise ValueError("Binary file writes are not allowed in this stage")

        with open(full_path, mode, encoding=encoding) as f:
            f.write(content)

        return {
            "message": "File written successfully",
            "path": str(full_path),
            "bytes_written": len(content.encode(encoding)),
        }

    async def _execute_llm_task(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
        completed: dict[str, TaskResult],
    ) -> dict[str, Any]:
        payload = task.payload
        prompt = str(payload.get("prompt", "")).strip()

        if not prompt:
            raise ValueError("LLM task requires 'prompt'")

        if self.settings.LLM_MODE == "disabled":
            raise ValueError("LLM is disabled")

        if self.settings.LLM_MODE == "ollama":
            url = f"{self.settings.LLM_BASE_URL.rstrip('/')}/api/generate"
            body = {
                "model": payload.get("model", self.settings.LLM_MODEL),
                "prompt": prompt,
                "stream": False,
            }

            timeout = aiohttp.ClientTimeout(total=self.settings.LLM_TIMEOUT_SECONDS)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return {
                        "message": "LLM task completed through Ollama",
                        "model": body["model"],
                        "response": data.get("response", ""),
                    }

        raise ValueError(f"Unsupported LLM_MODE: {self.settings.LLM_MODE}")

    def _calculate_mission_status(self, results: list[TaskResult]) -> MissionStatus:
        if not results:
            return MissionStatus.FAILED

        statuses = {r.status for r in results}

        if statuses == {TaskStatus.COMPLETED}:
            return MissionStatus.COMPLETED

        if TaskStatus.FAILED in statuses and TaskStatus.COMPLETED in statuses:
            return MissionStatus.PARTIAL

        if TaskStatus.FAILED in statuses:
            return MissionStatus.FAILED

        return MissionStatus.COMPLETED

    def _build_summary(
        self,
        mission_status: MissionStatus,
        results: list[TaskResult],
    ) -> str:
        total = len(results)
        completed = sum(1 for r in results if r.status == TaskStatus.COMPLETED)
        failed = sum(1 for r in results if r.status == TaskStatus.FAILED)
        skipped = sum(1 for r in results if r.status == TaskStatus.SKIPPED)

        return (
            f"Mission finished with status '{mission_status}'. "
            f"Total tasks: {total}. Completed: {completed}. Failed: {failed}. Skipped: {skipped}."
        )
