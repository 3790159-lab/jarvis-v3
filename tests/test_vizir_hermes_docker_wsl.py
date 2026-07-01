# -*- coding: utf-8 -*-
"""Vizir Hermes knee #2 · Variant A — Hermes executes inside Docker, driven from
WSL Ubuntu ($0, TDD, NO model, NO real Hermes).

Variant A (chosen 2026-07-01, supersedes the tcp/DOCKER_HOST bridge): the parent
spawns the child via ``wsl -d Ubuntu … python3 hermes_child.py`` so client+daemon+
paths+mounts are all Linux (unix socket, no path translation). env_overlay rides
INSIDE the cfg JSON (a WSL child cannot inherit the Windows parent env). The
ISOLATION GUARD is the teeth: it REALLY runs a throwaway container before Hermes,
and BLOCKS (Hermes never spawns, never host) if that fails — the lesson of live #1.

Pure units here are $0 and need neither WSL nor a model. Real-WSL de-risk lives in
``test_vizir_hermes_docker_wsl_live.py``.
"""
import asyncio

from app.services.vizir.handlers_hermes import make_hermes_handler, DEFAULT_ENABLED
from app.services.vizir.hermes_child import _require_docker_backend
from app.services.vizir.models import Step


def _run(coro):
    return asyncio.run(coro)


def _spy_run_fn(record):
    def rfn(prompt, *, model, enabled_toolsets, disabled_toolsets,
            max_iterations, on_step, env_overlay=None):
        record["called"] = True
        record["env_overlay"] = env_overlay
        record["enabled"] = list(enabled_toolsets)
        record["disabled"] = list(disabled_toolsets)
        return dict(final_response="ok", cost_usd=0.0, iterations=1,
                    stopped_reason="completed", tokens=0, artifact_path=None)
    return rfn


async def _ok_guard(**kw):
    return {"ok": True, "reason": "", "container_host": "abc123", "host_host": "PC-LOE"}


async def _fail_guard(**kw):
    return {"ok": False, "reason": "docker daemon unreachable"}


def _invoke(handler, prompt="compute primes", ctx=None):
    step = Step(kind="hermes", params={"prompt": prompt})
    return _run(handler(step, ctx or {}))


# ---------------------------------------------------- Variant A env overlay shape

def test_variant_a_env_overlay_is_wsl_shaped():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True,
                                  guard_fn=_ok_guard)
    _invoke(handler)
    env = rec["env_overlay"]
    assert env is not None
    assert env["TERMINAL_ENV"] == "docker"
    assert env["TERMINAL_DOCKER_IMAGE"] == "python:3.11-slim"
    assert env["TERMINAL_CONTAINER_MEMORY"] == "1024"
    # FIX of the mock-phase bug: Hermes reads TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES
    # (NOT HERMES_DOCKER_…); otherwise persist silently stayed true (non-ephemeral).
    assert env["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"] == "false"
    assert "HERMES_DOCKER_PERSIST_ACROSS_PROCESSES" not in env
    # Variant A uses the unix socket + docker on PATH — NO tcp host, NO binary path.
    assert "DOCKER_HOST" not in env
    assert "HERMES_DOCKER_BINARY" not in env


def test_variant_a_overrides_image_and_memory():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True,
                                  guard_fn=_ok_guard,
                                  docker_image="python:3.12-slim",
                                  container_memory_mb=512)
    _invoke(handler)
    env = rec["env_overlay"]
    assert env["TERMINAL_DOCKER_IMAGE"] == "python:3.12-slim"
    assert env["TERMINAL_CONTAINER_MEMORY"] == "512"


# ------------------------------------------------- ISOLATION GUARD teeth (the point)

def test_guard_block_means_hermes_never_runs_and_not_charged():
    # When the pre-flight throwaway container fails, the handler MUST block:
    # Hermes is never spawned (rfn not called → can't fall back to host) and the
    # step is not charged. This is the isolation analog of the cost-cap teeth and
    # the direct fix for live #1 (silent host fallback).
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True,
                                  guard_fn=_fail_guard)
    result = _invoke(handler)
    assert result.ok is False, "guard failure must block the step"
    assert not rec.get("called"), "Hermes run_fn MUST NOT run when the guard blocks"
    assert result.cost_usd == 0.0
    assert "isolation" in (result.error or "").lower()


def test_guard_pass_allows_hermes_to_run():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True,
                                  guard_fn=_ok_guard)
    result = _invoke(handler)
    assert result.ok is True
    assert rec.get("called") is True, "guard pass must let Hermes run"


def test_knee1_never_calls_the_guard():
    # docker_exec=False (knee #1, generation) must be COMPLETELY untouched: no
    # env overlay, no guard call, original toolsets. A guard that explodes proves
    # it is never invoked on the knee #1 path.
    rec = {}
    called = {"guard": False}

    async def exploding_guard(**kw):
        called["guard"] = True
        raise AssertionError("guard must not run for knee #1")

    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), guard_fn=exploding_guard)
    result = _invoke(handler)
    assert result.ok is True
    assert called["guard"] is False
    assert rec["env_overlay"] is None
    assert rec["enabled"] == list(DEFAULT_ENABLED)
    assert "terminal" in rec["disabled"] and "code_execution" in rec["disabled"]


# --------------------------------------- child-side isolation assertion (S6 plug)

def test_child_require_docker_backend_refuses_host():
    # Defence in depth: even if the env somehow did not select docker, the child
    # refuses to run (never on host). TERMINAL_ENV is exactly what Hermes'
    # _get_env_config reads (terminal_tool.py:1244), so asserting it is equivalent.
    _require_docker_backend({"TERMINAL_ENV": "docker"})   # must NOT raise

    for bad in ({"TERMINAL_ENV": "local"}, {"TERMINAL_ENV": ""}, {}):
        raised = False
        try:
            _require_docker_backend(bad)
        except RuntimeError:
            raised = True
        assert raised, f"child must refuse to run on host for env={bad!r}"
