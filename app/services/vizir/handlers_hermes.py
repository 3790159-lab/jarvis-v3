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

# --- knee #2 (docker_exec) = Variant A: Hermes EXECUTES inside an ephemeral
# Docker container, and Hermes ITSELF runs INSIDE WSL Ubuntu (Linux venv) so the
# docker client+daemon+paths+mounts are all Linux (unix socket, no tcp, no Win→
# /mnt/c path translation). This removes the whole Win↔Linux failure class that
# broke live #1. Applies ONLY when make_hermes_handler(docker_exec=True); knee #1
# (generation, Windows-native Hermes) is untouched.
DOCKER_EXEC_TOOLSETS = ["terminal", "code_execution"]   # lifted from DISABLED, added to ENABLED
DEFAULT_DOCKER_IMAGE = "python:3.11-slim"               # pre-pulled Linux-side (no cred vault)
DEFAULT_CONTAINER_MEMORY_MB = 1024                      # container RAM under the 2.5GB WSL cap
# Variant A spawn target: the child runs in the Ubuntu Hermes venv over `wsl`.
WSL_DISTRO = "Ubuntu"
WSL_USER = "root"
WSL_HERMES_PYTHON = "/root/hermes-agent/.venv/bin/python"   # uv-provisioned CPython 3.11
WSL_HERMES_CWD = "/root/hermes-agent"                       # Hermes import root (Linux)


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


def _win_to_wsl_path(p):
    """``C:\\a\\b`` -> ``/mnt/c/a/b`` so a WSL child can read a Windows-side file."""
    p = str(p)
    if len(p) >= 2 and p[1] == ":":
        rest = p[2:].replace("\\", "/")
        if not rest.startswith("/"):
            rest = "/" + rest
        return "/mnt/" + p[0].lower() + rest
    return p.replace("\\", "/")


async def _default_guard(*, image, distro=WSL_DISTRO, user=WSL_USER):
    """ISOLATION-SAFETY GUARD (Variant A) — REALLY run a throwaway container in
    WSL and prove isolation BEFORE Hermes is allowed to spawn. Returns
    ``{ok, reason, container_host, host_host}``. ``ok=False`` => the handler MUST
    block (Hermes never spawns, never falls back to the host — the live #1 lesson).
    This is a real ``docker run``, not just ``docker version``: it proves the
    daemon is up AND that code runs in a container with a distinct hostname.
    NEVER exercised in unit tests (guard_fn is injected there) -> $0."""
    async def _wsl(*argv):
        proc = await asyncio.create_subprocess_exec(
            "wsl", "-d", distro, "-u", user, "--", *argv,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace").strip(), \
            err.decode("utf-8", "replace").strip()

    try:
        hrc, host_host, _ = await _wsl("hostname")
        crc, cont_host, cerr = await _wsl(
            "docker", "run", "--rm", image, "hostname")
    except OSError as exc:
        return {"ok": False, "reason": f"wsl/docker not launchable: {exc}"}
    if crc != 0 or not cont_host:
        return {"ok": False,
                "reason": f"throwaway container failed (rc={crc}): {cerr[:300]}"}
    if cont_host == host_host:
        return {"ok": False,
                "reason": "container hostname == host hostname (no isolation)"}
    return {"ok": True, "reason": "", "container_host": cont_host,
            "host_host": host_host}


async def _stream_subprocess(py_exe, args, cfg, on_step, env_overlay=None):
    """Run a child process that streams JSON cost lines then a result line, and
    translate the result to our normalized shape. The PARENT enforces the
    cost-cap: ``on_step(cumulative_cost, note)`` may RAISE StepBudgetExceeded;
    when it (or a cancellation, or any error) propagates, the child is KILLED in
    ``finally`` so Hermes can never keep spending after the cap. A child that
    self-exits without a result line (e.g. os._exit) is reported as
    ``child_no_result`` — Vizir survives regardless (#8049 defence)."""
    # env_overlay (knee #2) selects the Docker terminal backend for the child
    # (TERMINAL_ENV=docker + DOCKER_HOST + image/caps). When None (knee #1) we
    # pass env=None so the child INHERITS our environment unchanged — the cost
    # cap and api creds (ANTHROPIC_API_KEY) ride that inheritance.
    child_env = {**os.environ, **env_overlay} if env_overlay else None
    proc = await asyncio.create_subprocess_exec(
        py_exe, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cfg.get("cwd") or None,
        env=child_env,
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
                       max_iterations, on_step, env_overlay=None):
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
    }
    if env_overlay and env_overlay.get("TERMINAL_ENV") == "docker":
        # Variant A: spawn the child INSIDE WSL Ubuntu (Linux Hermes venv). A WSL
        # child cannot inherit the Windows parent env, so the overlay rides inside
        # cfg (the child applies it before importing Hermes) and the api key is
        # injected explicitly. require_docker makes the child refuse to run on host.
        linux_child = _win_to_wsl_path(child)
        cfg["child_cwd"] = WSL_HERMES_CWD
        cfg["require_docker"] = True
        cfg["env_overlay"] = dict(env_overlay)
        for key in ("ANTHROPIC_API_KEY", "XAI_API_KEY"):
            val = os.environ.get(key)
            if val:
                cfg["env_overlay"][key] = val
        args = ["-d", WSL_DISTRO, "-u", WSL_USER, WSL_HERMES_PYTHON, linux_child]
        return await _stream_subprocess("wsl", args, cfg, on_step, env_overlay=None)
    # knee #1 (Windows-native Hermes): inherited env carries ANTHROPIC_API_KEY etc.
    cfg["cwd"] = cwd
    return await _stream_subprocess(py, [child], cfg, on_step, env_overlay=env_overlay)


def make_hermes_handler(run_fn=None, model=None, enabled_toolsets=None,
                        disabled_toolsets=None, max_iterations=None,
                        docker_exec=False, docker_image=None,
                        container_memory_mb=None, guard_fn=None):
    """Return an async StepHandler that runs Hermes for one task and normalizes
    its result. Per-step overrides may come via ``step.params``.

    ``docker_exec`` (knee #2, OPT-IN, Variant A) makes Hermes run terminal/
    code_execution INSIDE an ephemeral Docker container, with Hermes itself running
    inside WSL Ubuntu (all-Linux: unix socket, no tcp/path-translation). It ADDS
    terminal+code_execution to the whitelist, lifts them from the blocklist, builds
    the Variant A ``env_overlay`` (carried to the WSL child via cfg), and — before
    Hermes is EVER spawned — runs an ISOLATION GUARD that really starts a throwaway
    container; if that fails the step is BLOCKED (Hermes never runs, never on host).
    When False (the DEFAULT) NOTHING about knee #1 (generation) changes — no guard,
    no overlay, unchanged run_fn call shape — so existing handlers/mocks are
    untouched. The mid-flight contract (cost-cap / timeout / acceptance) is held by
    Vizir either way. ``guard_fn`` is injectable for $0 tests."""
    rfn = run_fn or _default_run
    gfn = guard_fn or _default_guard
    default_model = model or DEFAULT_MODEL
    default_max_iter = max_iterations if max_iterations is not None else DEFAULT_MAX_ITERATIONS
    guard_image = docker_image or DEFAULT_DOCKER_IMAGE

    if docker_exec:
        # ADD (not replace) the exec toolsets to knee #1's whitelist, and lift
        # them from the blocklist (a toolset can't be both enabled and disabled).
        base_enabled = list(DEFAULT_ENABLED) + [
            t for t in DOCKER_EXEC_TOOLSETS if t not in DEFAULT_ENABLED]
        base_disabled = [t for t in DEFAULT_DISABLED if t not in DOCKER_EXEC_TOOLSETS]
        env_overlay = {
            "TERMINAL_ENV": "docker",
            "TERMINAL_DOCKER_IMAGE": docker_image or DEFAULT_DOCKER_IMAGE,
            "TERMINAL_CONTAINER_MEMORY": str(container_memory_mb or DEFAULT_CONTAINER_MEMORY_MB),
            # FIX (mock-phase bug): Hermes reads TERMINAL_DOCKER_PERSIST_ACROSS_
            # PROCESSES (terminal_tool.py:1351); the old HERMES_DOCKER_… name was
            # ignored so containers silently persisted. "false" => ephemeral.
            "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES": "false",
            # No DOCKER_HOST (unix socket) / no HERMES_DOCKER_BINARY (docker on the
            # Linux PATH) — Variant A is all-Linux.
        }
    else:
        base_enabled = list(DEFAULT_ENABLED)
        base_disabled = list(DEFAULT_DISABLED)
        env_overlay = None

    default_enabled = enabled_toolsets if enabled_toolsets is not None else base_enabled
    default_disabled = disabled_toolsets if disabled_toolsets is not None else base_disabled

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

        # ISOLATION GUARD (knee #2 only, BEFORE Hermes is spawned): prove a real
        # container starts and is isolated. If not, BLOCK — return ok=False so the
        # step is not charged and, crucially, rfn is NEVER called (Hermes can never
        # fall back to host execution). This is the direct fix for live #1.
        if docker_exec:
            guard = await gfn(image=guard_image, distro=WSL_DISTRO, user=WSL_USER)
            if not guard.get("ok"):
                return HandlerResult(
                    ok=False, cost_usd=0.0,
                    error="isolation guard blocked (Hermes not spawned): %s"
                          % guard.get("reason"))

        # knee #1 keeps the exact pre-existing call shape (no env_overlay kwarg)
        # so existing run_fns/mocks are untouched; only knee #2 passes the overlay.
        call_kwargs = dict(model=p_model, enabled_toolsets=p_enabled,
                           disabled_toolsets=p_disabled, max_iterations=p_max_iter,
                           on_step=on_step)
        if env_overlay is not None:
            call_kwargs["env_overlay"] = env_overlay
        out = rfn(prompt, **call_kwargs)
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
