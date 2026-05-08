from __future__ import annotations

import json
import threading
import traceback
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/jarvis/v5/content-factory", tags=["jarvis-v5-async"])

ROOT = Path.cwd()
JOBS_DIR = ROOT / "jarvis_stage3_artifacts" / "jarvis_v5_content_factory_async_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_RUN_URL = "http://127.0.0.1:8015/api/jarvis/v5/content-factory/run"


class SubmitRequest(BaseModel):
    prompt: str = Field(default="")
    style_mode: str = Field(default="luxury_safe")
    image_batch: int = Field(default=1)
    video_enabled: bool = Field(default=False)
    quality_target: str = Field(default="high")
    destination: str = Field(default="google_drive")
    run_meta: Dict[str, Any] = Field(default_factory=dict)
    policy: Dict[str, Any] = Field(default_factory=dict)


def _job_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in ("_", "-"))
    return JOBS_DIR / f"{safe}.json"


def _write_job(job_id: str, data: Dict[str, Any]) -> None:
    data["job_id"] = job_id
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _job_path(job_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_job(job_id: str) -> Dict[str, Any]:
    path = _job_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _post_json(url: str, payload: Dict[str, Any], timeout: int = 900) -> Dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


def _worker(job_id: str, payload: Dict[str, Any]) -> None:
    _write_job(job_id, {
        "ok": True,
        "status": "running",
        "started_at": datetime.utcnow().isoformat() + "Z",
        "payload": payload,
    })

    try:
        result = _post_json(LOCAL_RUN_URL, payload, timeout=900)
        _write_job(job_id, {
            "ok": True,
            "status": "done",
            "finished_at": datetime.utcnow().isoformat() + "Z",
            "payload": payload,
            "result": result,
        })
    except Exception as e:
        _write_job(job_id, {
            "ok": False,
            "status": "error",
            "finished_at": datetime.utcnow().isoformat() + "Z",
            "payload": payload,
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


@router.get("/async-health")
def async_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_v5_content_factory_async_bridge",
        "jobs_dir": str(JOBS_DIR),
        "local_run_url": LOCAL_RUN_URL,
    }


@router.post("/submit")
def submit(req: SubmitRequest) -> Dict[str, Any]:
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")

    job_id = "job_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")

    payload = req.model_dump()
    payload["run_meta"] = payload.get("run_meta") or {}
    payload["run_meta"]["async_job_id"] = job_id
    payload["run_meta"]["async_bridge"] = True

    _write_job(job_id, {
        "ok": True,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "payload": payload,
    })

    thread = threading.Thread(target=_worker, args=(job_id, payload), daemon=True)
    thread.start()

    return {
        "ok": True,
        "status": "queued",
        "job_id": job_id,
        "message": "Jarvis accepted the task and is generating in background.",
        "status_url": f"/api/jarvis/v5/content-factory/jobs/{job_id}",
    }


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    return _read_job(job_id)