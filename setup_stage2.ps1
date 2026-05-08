$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path "app" | Out-Null
New-Item -ItemType Directory -Force -Path "app\schemas" | Out-Null
New-Item -ItemType Directory -Force -Path "app\core" | Out-Null
New-Item -ItemType Directory -Force -Path "app\services" | Out-Null
New-Item -ItemType Directory -Force -Path "app\api" | Out-Null
New-Item -ItemType Directory -Force -Path "app\api\routes" | Out-Null

@'
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MissionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskType(str, Enum):
    HTTP_REQUEST = "http_request"
    N8N_WEBHOOK = "n8n_webhook"
    SHELL_COMMAND = "shell_command"
    FILE_WRITE = "file_write"
    LLM_TASK = "llm_task"


class TaskDefinition(BaseModel):
    task_id: str
    title: str
    type: TaskType
    enabled: bool = True
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = None
    max_retries: int | None = None
    continue_on_error: bool = False
    parallel_group: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    title: str
    type: TaskType
    status: TaskStatus
    attempt_count: int = 0
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None


class MissionExecutionRequest(BaseModel):
    mission_id: str
    objective: str
    tasks: list[TaskDefinition]
    context: dict[str, Any] = Field(default_factory=dict)


class MissionExecutionResult(BaseModel):
    mission_id: str
    status: MissionStatus
    summary: str
    task_results: list[TaskResult] = Field(default_factory=list)
'@ | Set-Content -Path "app\schemas\execution.py" -Encoding UTF8

@'
from __future__ import annotations

import logging
import sys

from app.settings import get_settings


def setup_logging() -> None:
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
'@ | Set-Content -Path "app\core\logger.py" -Encoding UTF8

@'
from __future__ import annotations

import asyncio


class MissionLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, mission_id: str) -> asyncio.Lock:
        if mission_id not in self._locks:
            self._locks[mission_id] = asyncio.Lock()
        return self._locks[mission_id]


mission_lock_manager = MissionLockManager()
'@ | Set-Content -Path "app\services\mission_lock.py" -Encoding UTF8

@'
from __future__ import annotations

import shlex

from app.settings import get_settings


class ShellSecurityError(ValueError):
    pass


def validate_shell_command(command: str) -> None:
    settings = get_settings()
    lowered = command.strip().lower()

    for blocked in settings.blocked_shell_patterns:
        if blocked and blocked in lowered:
            raise ShellSecurityError(f"Blocked shell pattern detected: {blocked}")

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ShellSecurityError(f"Invalid shell command syntax: {exc}") from exc

    if not parts:
        raise ShellSecurityError("Empty shell command")

    executable = parts[0].lower()
    if executable not in settings.allowed_shell_commands:
        allowed = ", ".join(settings.allowed_shell_commands)
        raise ShellSecurityError(
            f"Command '{executable}' is not allowed. Allowed commands: {allowed}"
        )
'@ | Set-Content -Path "app\services\shell_guard.py" -Encoding UTF8

@'
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
'@ | Set-Content -Path "app\services\executor.py" -Encoding UTF8

@'
from __future__ import annotations

from typing import Any

from app.schemas.execution import MissionExecutionRequest, MissionExecutionResult
from app.services.executor import ExecutorService
from app.services.mission_lock import mission_lock_manager


class MissionRepositoryProtocol:
    async def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        ...

    async def update_mission_status(
        self,
        mission_id: str,
        status: str,
        summary: str | None = None,
    ) -> None:
        ...

    async def append_log(
        self,
        mission_id: str,
        level: str,
        message: str,
    ) -> None:
        ...

    async def save_task_results(
        self,
        mission_id: str,
        task_results: list[dict[str, Any]],
    ) -> None:
        ...


class MissionRunnerService:
    def __init__(self, repository: MissionRepositoryProtocol) -> None:
        self.repository = repository
        self.executor = ExecutorService()

    async def run_mission(self, mission_id: str) -> MissionExecutionResult:
        lock = mission_lock_manager.get_lock(mission_id)

        if lock.locked():
            raise RuntimeError(f"Mission '{mission_id}' is already running")

        async with lock:
            mission = await self.repository.get_mission(mission_id)
            if not mission:
                raise ValueError(f"Mission '{mission_id}' not found")

            await self.repository.append_log(mission_id, "INFO", "Mission run requested.")
            await self.repository.update_mission_status(mission_id, "running")
            await self.repository.append_log(mission_id, "INFO", "Mission status changed to running.")

            request = MissionExecutionRequest(
                mission_id=mission["mission_id"],
                objective=mission["objective"],
                tasks=mission["tasks"],
                context=mission.get("context", {}),
            )

            result = await self.executor.execute_mission(request)

            await self.repository.save_task_results(
                mission_id,
                [item.model_dump() for item in result.task_results],
            )

            for task_result in result.task_results:
                level = "INFO" if task_result.status.value == "completed" else "ERROR"
                await self.repository.append_log(
                    mission_id,
                    level,
                    f"Task {task_result.task_id} finished with status {task_result.status.value}. {task_result.message}",
                )

            await self.repository.update_mission_status(
                mission_id,
                result.status.value,
                result.summary,
            )
            await self.repository.append_log(
                mission_id,
                "INFO",
                f"Mission status changed to {result.status.value}.",
            )

            return result
'@ | Set-Content -Path "app\services\mission_runner.py" -Encoding UTF8

@'
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dependencies import get_mission_repository
from app.services.mission_runner import MissionRunnerService

router = APIRouter(prefix="/api/missions", tags=["missions"])


@router.post("/{mission_id}/run")
async def run_mission(mission_id: str):
    repository = get_mission_repository()
    service = MissionRunnerService(repository)

    try:
        result = await service.run_mission(mission_id)
        return result.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Mission run failed: {exc}") from exc
'@ | Set-Content -Path "app\api\routes\missions.py" -Encoding UTF8

@'
from __future__ import annotations

from typing import Any


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._missions: dict[str, dict[str, Any]] = {}

    async def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        return self._missions.get(mission_id)

    async def update_mission_status(
        self,
        mission_id: str,
        status: str,
        summary: str | None = None,
    ) -> None:
        mission = self._missions.get(mission_id)
        if not mission:
            return

        mission["status"] = status
        if summary is not None:
            mission["summary"] = summary

    async def append_log(
        self,
        mission_id: str,
        level: str,
        message: str,
    ) -> None:
        mission = self._missions.setdefault(mission_id, {})
        mission.setdefault("logs", [])
        mission["logs"].append(f"[{level}] {message}")

    async def save_task_results(
        self,
        mission_id: str,
        task_results: list[dict[str, Any]],
    ) -> None:
        mission = self._missions.setdefault(mission_id, {})
        mission["task_results"] = task_results

    def seed_mission(self, mission: dict[str, Any]) -> None:
        self._missions[mission["mission_id"]] = mission


_repository = InMemoryMissionRepository()


def get_mission_repository() -> InMemoryMissionRepository:
    return _repository
'@ | Set-Content -Path "app\dependencies.py" -Encoding UTF8

@'
from __future__ import annotations

from app.dependencies import get_mission_repository
from app.schemas.execution import TaskType


def seed_demo_mission() -> None:
    repository = get_mission_repository()

    mission = {
        "mission_id": "mission_demo_001",
        "objective": "Test execution pipeline",
        "status": "pending",
        "summary": "",
        "context": {
            "source": "demo",
            "project": "jarvis_v3",
        },
        "tasks": [
            {
                "task_id": "health_check",
                "title": "Check local backend health",
                "type": TaskType.SHELL_COMMAND.value,
                "enabled": True,
                "depends_on": [],
                "timeout_seconds": 20,
                "max_retries": 1,
                "continue_on_error": True,
                "parallel_group": None,
                "payload": {
                    "command": "powershell -Command ""Write-Output Hello_From_Jarvis"""
                },
            },
            {
                "task_id": "write_file",
                "title": "Write test artifact",
                "type": TaskType.FILE_WRITE.value,
                "enabled": True,
                "depends_on": ["health_check"],
                "timeout_seconds": 20,
                "max_retries": 1,
                "continue_on_error": False,
                "parallel_group": None,
                "payload": {
                    "path": "artifacts/mission_demo.txt",
                    "content": "Mission demo file created successfully."
                },
            },
        ],
    }

    repository.seed_mission(mission)
'@ | Set-Content -Path "app\seed_demo.py" -Encoding UTF8

@'
from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.missions import router as missions_router
from app.core.logger import setup_logging
from app.seed_demo import seed_demo_mission

setup_logging()
seed_demo_mission()

app = FastAPI(title="Jarvis V3 Supervisor")

app.include_router(missions_router)


@app.get("/health")
async def health():
    return {"status": "healthy"}
'@ | Set-Content -Path "app\main.py" -Encoding UTF8

Write-Host ""
Write-Host "Files created successfully."
Write-Host "Next:"
Write-Host "1) pip install fastapi uvicorn pydantic pydantic-settings aiohttp"
Write-Host "2) python -m uvicorn app.main:app --host 127.0.0.1 --port 8015"
Write-Host "3) Open http://127.0.0.1:8015/health"
Write-Host ""