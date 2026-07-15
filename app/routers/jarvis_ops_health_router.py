# -*- coding: utf-8 -*-
"""DEV-17: HTTP surface for Uptime Kuma to poll independently of the bot.

Each endpoint answers plain HTTP 200 (healthy) or 503 (not) — the two states
a stock Kuma "HTTP(s)" monitor needs, no Kuma-specific push protocol
required. Backed by ``app.services.ops_monitor`` (all pure/injectable); this
router just wires real default paths and turns ``ok`` into a status code.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response

from app.services import ops_monitor as om

router = APIRouter(prefix="/api/jarvis/ops", tags=["jarvis-ops-health"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HEARTBEAT_PATH = _PROJECT_ROOT / "state" / "bot_heartbeat.txt"
_BOT_GUARDIAN_LOG = _PROJECT_ROOT / "state" / "logs" / "bot_guardian.stdout.log"


def _respond(response: Response, result: dict) -> dict:
    response.status_code = 200 if result.get("ok") else 503
    return result


@router.get("/heartbeat")
def heartbeat(response: Response) -> dict:
    return _respond(response, om.check_bot_heartbeat(_HEARTBEAT_PATH))


@router.get("/cloudflared")
def cloudflared(response: Response) -> dict:
    return _respond(response, om.check_cloudflared())


@router.get("/disk")
def disk(response: Response) -> dict:
    return _respond(response, om.check_disk())


@router.get("/restarts")
def restarts(response: Response) -> dict:
    return _respond(response, om.check_restart_storm(_BOT_GUARDIAN_LOG))
