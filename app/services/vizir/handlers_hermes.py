# -*- coding: utf-8 -*-
"""Vizir paid StepHandler — Hermes Agent (Nous Research) as a controlled executor.

Hermes is a MULTI-STEP agent: given one prompt it runs its own tool-calling loop
(up to ``max_iterations``) and returns a final result. We clip it onto the Vizir
spine EXACTLY like ``grok_motion`` — a controlled executor, never a parallel brain
(see docs/specs/vizir-arc.md). Multi-step power is preserved INSIDE one Vizir step;
the frame around it (cost-cap, timeout, acceptance) is held by the Coordinator/Vizir.

Bridge to the proven mid-flight contract: as Hermes works it calls our ``on_step``
each iteration with its cumulative ``session_estimated_cost_usd``. We translate that
into ``ctx['report_cost'](delta)`` (reserve-before-next-iteration: it RAISES
``StepBudgetExceeded`` when the cap would be crossed, which propagates up and stops
Hermes before the next iteration) and ``ctx['report_progress'](note)``.

``run_fn`` is injectable for $0 unit tests; the default lazily binds the real
Hermes ``AIAgent`` path so importing this module never pulls heavy deps until used.
Acceptance ("did Hermes do it right?") is Vizir's job, kept SEPARATE — see
``acceptance.py``.
"""
from __future__ import annotations

from .handlers import HandlerResult
from .models import Step

# Leaf-executor defaults for the FIRST knee (Path A — generation, not execution).
# orchestrator_enabled:false (no Docker subagents yet) is set in Hermes config;
# here we additionally lock down the toolset to the minimum the task needs and
# exclude shell/exec/delegation for safety.
DEFAULT_MODEL = "xai/grok-4-fast-reasoning"          # our Grok via XAI_API_KEY
DEFAULT_ENABLED = ["file", "web", "search"]          # write artifact + look at refs
DEFAULT_DISABLED = ["terminal", "code_execution", "delegation", "cronjob", "messaging"]
DEFAULT_MAX_ITERATIONS = 90


def _default_run(prompt, *, model, enabled_toolsets, disabled_toolsets,
                 max_iterations, on_step):
    """Real Hermes adapter (lazy import; NEVER runs in mock tests -> $0).

    NOTE (live phase): the exact AIAgent callback wiring is confirmed against
    run_agent.py source before the first live run. Shape intended:
        from run_agent import AIAgent
        agent = AIAgent(model=model, enabled_toolsets=enabled_toolsets,
                        disabled_toolsets=disabled_toolsets,
                        max_iterations=max_iterations, quiet_mode=True,
                        step_callback=lambda *_: on_step(agent.session_estimated_cost_usd))
        out = agent.run_conversation(user_message=prompt)
        return dict(final_response=out.get("final_response"),
                    cost_usd=agent.session_estimated_cost_usd,
                    iterations=..., stopped_reason=..., tokens=agent.session_total_tokens)
    """
    raise NotImplementedError(
        "real Hermes adapter is wired in the live phase; inject run_fn for tests")


def make_hermes_handler(run_fn=None, model=None, enabled_toolsets=None,
                        disabled_toolsets=None, max_iterations=None):
    """Return an async StepHandler that runs Hermes for one task and normalizes
    its result. Per-step overrides may come via ``step.params``."""
    rfn = run_fn or _default_run
    default_model = model or DEFAULT_MODEL
    default_enabled = enabled_toolsets if enabled_toolsets is not None else list(DEFAULT_ENABLED)
    default_disabled = disabled_toolsets if disabled_toolsets is not None else list(DEFAULT_DISABLED)
    default_max_iter = max_iterations if max_iterations is not None else DEFAULT_MAX_ITERATIONS

    async def hermes(step: Step, ctx: dict) -> HandlerResult:
        prompt = step.params["prompt"]
        p_model = step.params.get("model", default_model)
        p_enabled = step.params.get("enabled_toolsets", default_enabled)
        p_disabled = step.params.get("disabled_toolsets", default_disabled)
        p_max_iter = step.params.get("max_iterations", default_max_iter)

        report_cost = ctx.get("report_cost")
        report_progress = ctx.get("report_progress")
        reported = {"total": 0.0}

        def on_step(cumulative_cost, note=None):
            if report_progress and note:
                report_progress(note)
            if report_cost:
                delta = float(cumulative_cost) - reported["total"]
                if delta > 0:
                    report_cost(delta)            # may RAISE StepBudgetExceeded -> stop
                    reported["total"] = float(cumulative_cost)

        out = rfn(prompt, model=p_model, enabled_toolsets=p_enabled,
                  disabled_toolsets=p_disabled, max_iterations=p_max_iter,
                  on_step=on_step)

        cost = float(out.get("cost_usd") or 0.0)
        final = (out.get("final_response") or "").strip()
        artifact = out.get("artifact_path")
        if not final and not artifact:
            # Nothing produced (refusal/empty) -> ok=False so the Coordinator does
            # NOT charge it (proven rule 'refusal не списан').
            return HandlerResult(ok=False, error="hermes produced no output", cost_usd=cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final,
            "artifact_path": out.get("artifact_path"),
            "iterations": out.get("iterations"),
            "stopped_reason": out.get("stopped_reason"),
            "tokens": out.get("tokens"),
        })

    return hermes
