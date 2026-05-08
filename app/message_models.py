from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanMode(str, Enum):
    CHAT = "chat"
    TASK = "task"
    HYBRID = "hybrid"
    MISSION = "mission"


class ToolName(str, Enum):
    FS_LIST = "fs_list"
    FS_READ = "fs_read"
    FS_SEARCH = "fs_search"
    GIT_STATUS = "git_status"
    RUN_PYTHON = "run_python"
    RUN_SHELL = "run_shell"
    AGENT_CALL = "agent_call"


class PlannedTask(BaseModel):
    tool: ToolName
    reason: str
    args: dict[str, Any] = Field(default_factory=dict)


class MessagePlan(BaseModel):
    mode: Literal["chat", "task", "hybrid", "mission"]
    reply_text: str
    needs_worker: bool = False
    approval_required: bool = False
    tasks: list[PlannedTask] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    ok: bool
    tool: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConversationContext(BaseModel):
    chat_id: str | int
    user_text: str
    project_root: str
    user_name: str | None = None
    memory_summary: str | None = None
