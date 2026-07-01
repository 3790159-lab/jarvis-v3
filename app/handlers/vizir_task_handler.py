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

import itertools
from dataclasses import dataclass
from pathlib import Path

from app.services.vizir.coordinator import Coordinator
from app.services.vizir.handlers import HandlerRegistry
from app.services.vizir.handlers_hermes import DEFAULT_DISABLED, make_hermes_handler
from app.services.vizir.hermes_acceptance import AcceptanceResult
from app.services.vizir.loop import LoopConfig, LoopController
from app.services.vizir.models import Plan, Step, Task


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


_TASK_COUNTER = itertools.count(1)


class VizirTaskHandler:
    """Runs the autonomous LOOP for one /task. Injectable deps keep it $0-testable
    (real Coordinator + LoopController + a mock Hermes handler)."""

    def __init__(
        self, *, hermes_handler=None, check_limit=None, record_cost=None,
        artifact_dir, budget_usd=0.90, min_attempt_usd=0.20, max_usd=0.40,
        estimated_per_attempt_usd=0.15, max_attempts=3, loop_deadline_s=600.0,
        accept_fn=None,
    ) -> None:
        # default Hermes = knee#1 generation, NO tools -> full artifact inline in
        # final_response (proven live). Tests inject a mock handler.
        self._hermes = hermes_handler or make_hermes_handler(
            docker_exec=False, enabled_toolsets=[],
            disabled_toolsets=list(DEFAULT_DISABLED) + ["file", "web", "search"],
            max_iterations=6)
        self._check_limit = check_limit
        self._record_cost = record_cost
        self._artifact_dir = Path(artifact_dir)
        self._budget_usd = float(budget_usd)
        self._min_attempt_usd = float(min_attempt_usd)
        self._max_usd = float(max_usd)
        self._estimated = float(estimated_per_attempt_usd)
        self._max_attempts = int(max_attempts)
        self._loop_deadline_s = float(loop_deadline_s)
        self._accept_fn = accept_fn or accept_generic

    def _build_loop(self, actor: str, username, on_event):
        reg = HandlerRegistry()
        reg.register("hermes", self._hermes)
        check_limit_fn = None
        if self._check_limit is not None:
            check_limit_fn = (lambda act, est:
                              self._check_limit(int(act), estimated_usd=est))
        charge_logger = None
        if self._record_cost is not None:
            async def charge_logger(act, op, amt):
                self._record_cost(int(act), username, amt)
        coord = Coordinator(
            reg, on_event=on_event, charge_logger=charge_logger,
            check_limit_fn=check_limit_fn, state_dir=self._artifact_dir / "state")
        est, cap, tmo = self._estimated, self._max_usd, self._loop_deadline_s

        def build_plan(prompt):
            return Plan(steps=[Step(kind="hermes", params={"prompt": prompt},
                                    estimated_usd=est, max_usd=cap, timeout_s=180.0)])

        return LoopController(
            coord, build_plan=build_plan, accept_fn=self._accept_fn,
            config=LoopConfig(max_attempts=self._max_attempts,
                              min_attempt_usd=self._min_attempt_usd,
                              loop_deadline_s=tmo),
            on_event=on_event)

    def _bridge(self, progress_cb):
        def on_event(e):
            t = e.get("type")
            if t == "loop_attempt_started":
                progress_cb("attempt_started", {"attempt": e.get("attempt")})
            elif t == "loop_attempt_rejected":
                progress_cb("attempt_rejected",
                            {"attempt": e.get("attempt"), "reasons": e.get("reasons", [])})
            elif t == "progress":
                progress_cb("working", {"note": e.get("note")})
        return on_event

    async def run_task_phase(self, chat_id, base_prompt, progress_cb, *,
                             user_id=None, username=None) -> VizirTaskReply:
        actor = str(chat_id)
        task_id = "task-%s-%d" % (chat_id, next(_TASK_COUNTER))
        on_event = self._bridge(progress_cb)
        loop = self._build_loop(actor, username, on_event=on_event)
        task = Task(task_id=task_id, goal=base_prompt[:80], actor=actor,
                    budget_usd=self._budget_usd)
        rep = await loop.run(task, base_prompt)
        if rep.accepted:
            html = ""
            if isinstance(rep.last_result, dict):
                html = rep.last_result.get("final_response") or ""
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            out = self._artifact_dir / ("%s.html" % task_id)
            out.write_text(html, encoding="utf-8")
            text = ("✅ Готово за %d попыток. Потрачено $%.4f (кап $%.2f). Приёмка пройдена."
                    % (rep.attempts, rep.loop_spent_usd, self._budget_usd))
            return VizirTaskReply(text=text, document_path=out, escalated=False)
        return VizirTaskReply(text="", document_path=None, escalated=True)
