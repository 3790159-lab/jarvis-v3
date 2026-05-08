from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import APIRouter

from app.runtime_policy import POLICY_PROFILES

router = APIRouter()


@router.get("/diag")
def diag() -> dict:
    state_dir = Path("state")
    runtime_log = state_dir / "runtime.log"
    agents_file = state_dir / "agents.json"
    repair_report = state_dir / "repair_report.json"

    return {
        "status": "ok",
        "service": "managed_autonomous_api",
        "pid": os.getpid(),
        "time": time.time(),
        "files": {
            "runtime_log_exists": runtime_log.exists(),
            "agents_file_exists": agents_file.exists(),
            "repair_report_exists": repair_report.exists(),
        },
        "policy_profiles": list(POLICY_PROFILES.keys()),
        "cwd": str(Path.cwd()),
    }


@router.get("/policy/profiles")
def get_policy_profiles() -> dict:
    return {
        "status": "ok",
        "profiles": POLICY_PROFILES,
    }
