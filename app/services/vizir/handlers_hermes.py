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

import asyncio
import inspect
import json
import os

from .handlers import HandlerResult
from .models import Step

# Leaf-executor defaults for the FIRST knee (Path A — generation, not execution).
# orchestrator_enabled:false (no Docker subagents yet) is set in Hermes config.
# enabled_toolsets is a WHITELIST -> only these are on; the disabled list is
# defence-in-depth. Names verified against Hermes toolsets.py (TOOLSETS keys).
DEFAULT_MODEL = "claude-sonnet-4-6"                  # Claude brain (stronger at code)
DEFAULT_ENABLED = ["file", "web", "search"]          # write artifact + look at refs
#   file -> read_file/write_file/patch/search_files ; web ; search -> web_search
DEFAULT_DISABLED = ["terminal", "code_execution", "delegation", "computer_use", "cronjob"]
#   terminal/process, execute_code, delegate_task, computer_use, cron — all OFF
DEFAULT_MAX_ITERATIONS = 30          # conservative ceiling for knee #1 (was 90)


def _derive_provider(model):
    """Hermes resolves cost pricing only when the provider is explicit (verified:
    estimate_usage_cost returns 'unknown' with provider=None for claude-sonnet-4-6).
    Map the model name to its native provider so the cost-cap is never blind."""
    m = (model or "").lower()
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "grok" in m or "xai" in m:
        return "xai"
    return None


def _hermes_paths():
    """Locate the installed Hermes venv python + our child entry point.
    Validated on disk (STEP 2): HERMES_HOME=%LOCALAPPDATA%\\hermes, venv python at
    hermes-agent\\venv\\Scripts\\python.exe, run_agent.AIAgent importable there."""
    home = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "hermes")
    py = os.path.join(home, "hermes-agent", "venv", "Scripts", "python.exe")
    child = os.path.join(os.path.dirname(__file__), "hermes_child.py")
    cwd = os.path.join(home, "hermes-agent")
    return py, child, cwd


async def _stream_subprocess(py_exe, args, cfg, on_step):
    """Run a child process that streams JSON cost lines then a result line, and
    translate the result to our normalized shape. The PARENT enforces the
    cost-cap: ``on_step(cumulative_cost, note)`` may RAISE StepBudgetExceeded;
    when it (or a cancellation, or any error) propagates, the child is KILLED in
    ``finally`` so Hermes can never keep spending after the cap. A child that
    self-exits without a result line (e.g. os._exit) is reported as
    ``child_no_result`` — Vizir survives regardless (#8049 defence)."""
    proc = await asyncio.create_subprocess_exec(
        py_exe, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cfg.get("cwd") or None,
    )
    result_obj = None
    err_chunks: list[str] = []

    async def _drain_err():
        # Drain stderr concurrently so a chatty child can't fill the pipe buffer
        # and deadlock our stdout read; also captures tracebacks for diagnostics.
        try:
            async for eraw in proc.stderr:
                err_chunks.append(eraw.decode("utf-8", errors="replace"))
        except Exception:
            pass

    err_task = asyncio.ensure_future(_drain_err())
    try:
        proc.stdin.write(json.dumps(cfg).encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue                      # ignore non-JSON noise from the child
            if msg.get("type") == "cost":
                cum = float(msg.get("cost") or 0.0)
                on_step(cum, note=f"hermes iter {msg.get('iter')}: ${cum:.4f}")
            elif msg.get("type") == "result":
                result_obj = msg
        await proc.wait()
    finally:
        if proc.returncode is None:           # cap breach / cancel / error / hang
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        err_task.cancel()
        try:
            await err_task
        except (asyncio.CancelledError, Exception):
            pass

    if result_obj is None:
        # Child died/exited without a result line — Vizir survives (#8049 defence).
        # Surface a stderr tail so a live operator can see WHY (auth, import, crash).
        tail = ("".join(err_chunks))[-1500:]
        return dict(final_response="", cost_usd=0.0, iterations=0,
                    stopped_reason="child_no_result", tokens=0,
                    artifact_path=cfg.get("artifact_path"),
                    stderr_tail=tail)
    completed = bool(result_obj.get("completed"))
    stopped = "completed" if completed else (
        result_obj.get("turn_exit_reason") or "incomplete")
    return dict(
        final_response=result_obj.get("final_response") or "",
        cost_usd=float(result_obj.get("cost") or 0.0),
        iterations=result_obj.get("iterations"),
        stopped_reason=stopped,
        tokens=result_obj.get("tokens"),
        artifact_path=cfg.get("artifact_path"),
    )


async def _default_run(prompt, *, model, enabled_toolsets, disabled_toolsets,
                       max_iterations, on_step):
    """Real Hermes adapter — SUBPROCESS-ISOLATED (lazy; NEVER runs in mocks -> $0).

    Spawns ``hermes_child.py`` in the installed Hermes venv; the child constructs
    ``AIAgent`` (confirmed kwargs: model/enabled_toolsets/disabled_toolsets/
    max_iterations/quiet_mode/step_callback) and runs ``run_conversation``,
    streaming cumulative ``session_estimated_cost_usd`` per iteration. The parent
    (``_stream_subprocess``) applies the cost-cap and kills the child on breach."""
    py, child, cwd = _hermes_paths()
    cfg = {
        "prompt": prompt,
        "model": model,
        "provider": _derive_provider(model),   # REQUIRED for cost tracking / cap
        "enabled_toolsets": enabled_toolsets,
        "disabled_toolsets": disabled_toolsets,
        "max_iterations": max_iterations,
        "cwd": cwd,
        # api creds via inherited env (ANTHROPIC_API_KEY/XAI_API_KEY); artifact_path
        # set by the live caller via a custom run_fn when a file output is needed.
    }
    return await _stream_subprocess(py, [child], cfg, on_step)


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
        if inspect.isawaitable(out):          # real subprocess adapter is async
            out = await out                   # mocks return a plain dict (sync)

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
