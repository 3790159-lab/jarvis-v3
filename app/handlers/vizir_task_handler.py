# app/handlers/vizir_task_handler.py
# -*- coding: utf-8 -*-
"""Vizir /task handler — runs the autonomous LOOP for one bot task.

Transport-agnostic (no Telegram imports): builds a real LoopController over a
real Coordinator with the money hooks wired to the bot's access_control +
cost_tracker, runs the loop, maps core events to a progress callback, and
returns a VizirTaskReply (summary + artifact, or an honest escalation). Mirrors
FaceSwapHandler's long-running shape. See docs/specs/2026-07-02-vizir-bot-integration-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.vizir.hermes_acceptance import AcceptanceResult


@dataclass
class VizirTaskReply:
    text: str
    document_path: Path | None = None
    escalated: bool = False


def accept_generic(value: dict) -> AcceptanceResult:
    """v1 acceptance for arbitrary tasks: accept iff Hermes completed AND produced
    non-empty output. Mirrors accept_hermes_chat's stopped_reason check so a
    truncated run (max_iterations) is rejected and retried with directed feedback."""
    reasons: list[str] = []
    stopped = value.get("stopped_reason")
    if stopped and stopped != "completed":
        reasons.append(f"run did not complete (stopped_reason={stopped})")
    output = value.get("final_response") or value.get("artifact_path")
    if not output:
        reasons.append("empty output (no final_response/artifact produced)")
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
