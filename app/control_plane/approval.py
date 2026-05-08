from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalKind(str, Enum):
    SCHEMA_EXTENSION = "schema_extension"
    DYNAMIC_CAPABILITY = "dynamic_capability"


class ApprovalRequest(BaseModel):
    approval_id: str
    kind: ApprovalKind
    agent_id: str
    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    risk_level: str = "low"
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_ts: float = 0.0
    resolved_ts: float = 0.0
    resolution_note: str = ""


class ApprovalManager:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.requests: dict[str, ApprovalRequest] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"requests": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.requests = {}
        for item in raw.get("requests", []):
            req = ApprovalRequest.model_validate(item)
            self.requests[req.approval_id] = req

    def save(self) -> None:
        data = {"requests": [r.model_dump(mode="json") for r in self.requests.values()]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _create(
        self,
        kind: ApprovalKind,
        agent_id: str,
        task_id: str,
        payload: dict[str, Any],
        reason: str,
        risk_level: str = "low",
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            approval_id=f"approval_{uuid.uuid4().hex[:10]}",
            kind=kind,
            agent_id=agent_id,
            task_id=task_id,
            payload=payload,
            reason=reason,
            risk_level=risk_level,
            status=ApprovalStatus.PENDING,
            created_ts=time.time(),
        )
        self.requests[req.approval_id] = req
        self.save()
        return req

    def submit_schema_extension(
        self,
        agent_id: str,
        task_id: str,
        payload: dict[str, Any],
        reason: str,
        auto_approve: bool = False,
    ) -> ApprovalRequest:
        req = self._create(
            kind=ApprovalKind.SCHEMA_EXTENSION,
            agent_id=agent_id,
            task_id=task_id,
            payload=payload,
            reason=reason,
            risk_level="low",
        )
        if auto_approve:
            self.approve(req.approval_id, note="Auto-approved mission-scoped schema extension.")
            return self.requests[req.approval_id]
        return req

    def submit_dynamic_capability(
        self,
        agent_id: str,
        task_id: str,
        capability: str,
        reason: str,
        auto_approve: bool = False,
    ) -> ApprovalRequest:
        req = self._create(
            kind=ApprovalKind.DYNAMIC_CAPABILITY,
            agent_id=agent_id,
            task_id=task_id,
            payload={"capability": capability},
            reason=reason,
            risk_level="medium",
        )
        if auto_approve:
            self.approve(req.approval_id, note="Auto-approved dynamic capability.")
            return self.requests[req.approval_id]
        return req

    def approve(self, approval_id: str, note: str = "") -> bool:
        req = self.requests.get(approval_id)
        if not req:
            return False
        req.status = ApprovalStatus.AUTO_APPROVED if "Auto-approved" in note else ApprovalStatus.APPROVED
        req.resolved_ts = time.time()
        req.resolution_note = note
        self.save()
        return True

    def reject(self, approval_id: str, note: str = "") -> bool:
        req = self.requests.get(approval_id)
        if not req:
            return False
        req.status = ApprovalStatus.REJECTED
        req.resolved_ts = time.time()
        req.resolution_note = note
        self.save()
        return True

    def list_requests(self) -> list[ApprovalRequest]:
        return list(self.requests.values())

    def list_pending(self) -> list[ApprovalRequest]:
        return [r for r in self.requests.values() if r.status == ApprovalStatus.PENDING]