# -*- coding: utf-8 -*-
"""Vizir Hermes knee #2 — Docker execution backend wiring ($0, TDD, NO model, NO real docker).

Задача 3: opt-in ``docker_exec=True`` makes Hermes run terminal/code_execution INSIDE an
ephemeral Docker container (``TERMINAL_ENV=docker``) reached via ``DOCKER_HOST`` over the
Ubuntu-WSL dockerd. knee #1 (generation, ``docker_exec=False``) stays the DEFAULT and MUST
NOT change. These tests spy on the injected ``run_fn`` (no model, no container) and exercise
``_stream_subprocess`` env-forwarding against a tiny fake child (no Hermes), so the bridge is
proven at $0 with the money-safety teeth (cap → kill child) intact.
"""
import asyncio
import json
import os
import sys
import time

from app.services.vizir.handlers_hermes import (
    make_hermes_handler,
    _stream_subprocess,
    DEFAULT_ENABLED,
    DEFAULT_DISABLED,
    DEFAULT_DOCKER_BINARY,
)
from app.services.vizir.models import Step
from app.services.vizir.coordinator import StepBudgetExceeded


def _run(coro):
    return asyncio.run(coro)


def _spy_run_fn(record):
    """A run_fn stand-in that records every kwarg the handler passes it
    (so we can assert env_overlay + toolsets without a model or a container)."""
    def rfn(prompt, *, model, enabled_toolsets, disabled_toolsets,
            max_iterations, on_step, env_overlay=None):
        record.update(dict(
            prompt=prompt, model=model,
            enabled=list(enabled_toolsets), disabled=list(disabled_toolsets),
            max_iterations=max_iterations, env_overlay=env_overlay,
        ))
        return dict(final_response="ok", cost_usd=0.0, iterations=1,
                    stopped_reason="completed", tokens=0, artifact_path=None)
    return rfn


def _invoke(handler, prompt="do x", ctx=None):
    step = Step(kind="hermes", params={"prompt": prompt})
    return _run(handler(step, ctx or {}))


# ---------------------------------------------------------------- env overlay

def test_docker_exec_builds_env_overlay():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True)
    _invoke(handler)
    env = rec["env_overlay"]
    assert env is not None, "docker_exec=True must pass an env_overlay to the child"
    assert env["TERMINAL_ENV"] == "docker"
    assert env["TERMINAL_DOCKER_IMAGE"] == "python:3.11-slim"
    assert env["TERMINAL_CONTAINER_MEMORY"] == "1024"     # under the 2.5GB WSL cap
    assert env["DOCKER_HOST"] == "tcp://127.0.0.1:2375"   # Ubuntu dockerd, localhost-only
    assert env["HERMES_DOCKER_BINARY"] == DEFAULT_DOCKER_BINARY
    assert env["HERMES_DOCKER_PERSIST_ACROSS_PROCESSES"] == "false"   # ephemeral


def test_docker_exec_overrides_are_honored():
    rec = {}
    handler = make_hermes_handler(
        run_fn=_spy_run_fn(rec), docker_exec=True,
        docker_image="python:3.12-slim", container_memory_mb=512,
        docker_host="tcp://127.0.0.1:9999", docker_binary="C:/x/docker.exe",
    )
    _invoke(handler)
    env = rec["env_overlay"]
    assert env["TERMINAL_DOCKER_IMAGE"] == "python:3.12-slim"
    assert env["TERMINAL_CONTAINER_MEMORY"] == "512"
    assert env["DOCKER_HOST"] == "tcp://127.0.0.1:9999"
    assert env["HERMES_DOCKER_BINARY"] == "C:/x/docker.exe"


# ---------------------------------------------------------- knee #1 untouched

def test_default_knee1_has_no_docker_env_and_keeps_toolsets():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec))   # docker_exec defaults False
    _invoke(handler)
    assert rec["env_overlay"] is None, "knee #1 must NOT inject a docker env"
    assert rec["enabled"] == list(DEFAULT_ENABLED)           # file/web/search unchanged
    assert "terminal" in rec["disabled"]
    assert "code_execution" in rec["disabled"]


# ----------------------------------------------------------- toolset flip

def test_docker_exec_enables_terminal_and_code_execution_toolsets():
    rec = {}
    handler = make_hermes_handler(run_fn=_spy_run_fn(rec), docker_exec=True)
    _invoke(handler)
    assert "terminal" in rec["enabled"]
    assert "code_execution" in rec["enabled"]
    # and they must be removed from the disabled blocklist (can't be both)
    assert "terminal" not in rec["disabled"]
    assert "code_execution" not in rec["disabled"]
    # knee #1 toolsets are still present (we ADD, not replace)
    for t in DEFAULT_ENABLED:
        assert t in rec["enabled"]


# ------------------------------------------------ env truly reaches the child

def _write_child(tmp_path, body):
    p = tmp_path / "fake_child.py"
    p.write_text("import sys, json, os, time\n"
                 "cfg = json.loads(sys.stdin.read())\n"
                 "def emit(o):\n"
                 "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
                 + body, encoding="utf-8")
    return str(p)


def test_env_overlay_reaches_child_process(tmp_path):
    # The fake child echoes back what TERMINAL_ENV it actually sees in its OS
    # environment. If env-forwarding into create_subprocess_exec is broken, the
    # child sees nothing and this fails — real subprocess, no Hermes, $0.
    child = _write_child(tmp_path, (
        "emit({'type':'result',"
        " 'final_response': os.environ.get('TERMINAL_ENV','MISSING'),"
        " 'completed':True,'cost':0.0,'iterations':1,'tokens':0})\n"
    ))
    out = _run(_stream_subprocess(
        sys.executable, [child], {"prompt": "x"},
        lambda cum, note=None: None,
        env_overlay={"TERMINAL_ENV": "docker", "DOCKER_HOST": "tcp://127.0.0.1:2375"},
    ))
    assert out["final_response"] == "docker", "env_overlay did not reach the child"


def test_stream_subprocess_without_env_overlay_inherits_parent(tmp_path):
    # Backward-compat: omitting env_overlay must NOT wipe the inherited env
    # (knee #1 relies on inherited ANTHROPIC_API_KEY etc.).
    os.environ["_VIZIR_TEST_SENTINEL_"] = "inherited-ok"
    try:
        child = _write_child(tmp_path, (
            "emit({'type':'result',"
            " 'final_response': os.environ.get('_VIZIR_TEST_SENTINEL_','MISSING'),"
            " 'completed':True,'cost':0.0,'iterations':1,'tokens':0})\n"
        ))
        out = _run(_stream_subprocess(
            sys.executable, [child], {"prompt": "x"},
            lambda cum, note=None: None,
        ))
        assert out["final_response"] == "inherited-ok"
    finally:
        del os.environ["_VIZIR_TEST_SENTINEL_"]


# -------------------------------------------- money-safety teeth still bite

def _pid_alive(pid):
    import subprocess
    for _ in range(10):
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        if str(pid) not in out:
            return False
        time.sleep(0.2)
    return True


def test_docker_exec_path_still_kills_child_on_cap_breach(tmp_path):
    # The new env_overlay forwarding must NOT weaken the cap→kill teeth: a child
    # that overspends then hangs is still killed by the parent. Proves money-
    # safety survives the docker_exec code path (env_overlay set).
    pidfile = tmp_path / "child.pid"
    child = _write_child(tmp_path, (
        "open(cfg['pidfile'],'w').write(str(os.getpid()))\n"
        "for c in (0.10, 0.30, 0.90):\n"
        "    emit({'type':'cost','cost':c,'iter':1}); time.sleep(0.05)\n"
        "time.sleep(60)\n"
    ))

    def capping(cum, note=None):
        if cum > 0.20:
            raise StepBudgetExceeded(f"cap exceeded at ${cum}")

    t0 = time.time()
    raised = False
    try:
        _run(_stream_subprocess(
            sys.executable, [child], {"prompt": "x", "pidfile": str(pidfile)},
            capping,
            env_overlay={"TERMINAL_ENV": "docker"},
        ))
    except StepBudgetExceeded:
        raised = True
    elapsed = time.time() - t0

    assert raised
    assert elapsed < 30
    child_pid = int(pidfile.read_text())
    assert not _pid_alive(child_pid), f"child {child_pid} still alive — not killed"
