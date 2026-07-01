# -*- coding: utf-8 -*-
"""Vizir Hermes knee #2 · Variant A — REAL-WSL transport de-risk ($0, NO model).

These exercise the actual ``wsl -d Ubuntu … python`` spawn transport that Variant A
uses, proving at $0 (no Hermes model call):

  * env_overlay carried inside cfg reaches the WSL child's ``os.environ``;
  * the real ``hermes_child`` REFUSES to run on the host when the docker backend
    is not selected (isolation defence, end-to-end in real WSL);
  * the cost-cap STILL kills the child in the WSL transport — i.e. ``proc.kill()``
    on ``wsl.exe`` actually terminates the Linux-side process (money teeth survive
    the new transport, the concern the WSL indirection raises).

Skipped automatically when WSL/Ubuntu is not available so the suite stays portable.
"""
import asyncio
import subprocess
import sys

import pytest

from app.services.vizir.handlers_hermes import _stream_subprocess, _win_to_wsl_path
from app.services.vizir.coordinator import StepBudgetExceeded

WSL = ["wsl", "-d", "Ubuntu", "-u", "root", "--"]
VENV_PY = "/root/hermes-agent/.venv/bin/python"     # real Hermes venv (CPython 3.11)


def _wsl_ok():
    try:
        r = subprocess.run(WSL + ["true"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _wsl_ok(), reason="WSL/Ubuntu not available")


def _run(coro):
    return asyncio.run(coro)


def _write_linux_child(tmp_path, name, body):
    """Write a tiny Linux python child (stdlib only) and return its /mnt/c path."""
    p = tmp_path / name
    p.write_text(
        "import sys, json, os, time\n"
        "cfg = json.loads(sys.stdin.read())\n"
        "for k, v in (cfg.get('env_overlay') or {}).items():\n"
        "    os.environ[str(k)] = str(v)\n"
        "def emit(o):\n"
        "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
        + body,
        encoding="utf-8", newline="\n")
    return _win_to_wsl_path(str(p))


# --------------------------------------------- env_overlay reaches the WSL child

def test_env_overlay_in_cfg_reaches_wsl_child(tmp_path):
    child = _write_linux_child(tmp_path, "echo_child.py", (
        "emit({'type':'result',"
        " 'final_response': os.environ.get('TERMINAL_ENV','MISSING')"
        "   + '|' + os.environ.get('ANTHROPIC_API_KEY','NOKEY'),"
        " 'completed':True,'cost':0.0,'iterations':1,'tokens':0})\n"
    ))
    out = _run(_stream_subprocess(
        "wsl", ["-d", "Ubuntu", "-u", "root", "python3", child],
        {"prompt": "x",
         "env_overlay": {"TERMINAL_ENV": "docker",
                         "ANTHROPIC_API_KEY": "sk-SENTINEL-777"}},
        lambda cum, note=None: None,
    ))
    assert out["final_response"] == "docker|sk-SENTINEL-777", \
        "env_overlay (incl. api key) must reach the WSL child via cfg"


# ------------------------------ real hermes_child refuses host when not docker

def test_real_hermes_child_refuses_host_when_not_docker(tmp_path):
    # Point the transport at the REAL hermes_child.py running in the Ubuntu Hermes
    # venv, but with TERMINAL_ENV != docker + require_docker: it must short-circuit
    # (isolation_guard_block) BEFORE importing Hermes — so $0, and it never runs on
    # the host. Proves the child-side isolation defence end-to-end in real WSL.
    import app.services.vizir.hermes_child as hc
    linux_child = _win_to_wsl_path(hc.__file__)
    out = _run(_stream_subprocess(
        "wsl", ["-d", "Ubuntu", "-u", "root", VENV_PY, linux_child],
        {"prompt": "compute something",
         "model": "claude-sonnet-4-6",
         "require_docker": True,
         "child_cwd": "/root/hermes-agent",
         "env_overlay": {"TERMINAL_ENV": "local"}},   # NOT docker -> must refuse
        lambda cum, note=None: None,
    ))
    assert out["stopped_reason"] == "isolation_guard_block", \
        f"child must refuse host execution, got {out.get('stopped_reason')!r}"
    assert out["cost_usd"] == 0.0


# ------------------------------------- cost-cap STILL kills the WSL-side child

def _linux_pid_alive(pid):
    r = subprocess.run(WSL + ["kill", "-0", str(pid)],
                       capture_output=True, timeout=30)
    return r.returncode == 0


def test_cost_cap_kills_child_in_wsl_transport(tmp_path):
    # The child records its Linux PID, overspends, then hangs. The parent's cap
    # must raise AND proc.kill() on wsl.exe must actually kill the Linux process —
    # otherwise money teeth are weaker in the WSL transport.
    pidmnt = tmp_path / "linuxpid.txt"
    pid_linux_path = _win_to_wsl_path(str(pidmnt))
    child = _write_linux_child(tmp_path, "hang_child.py", (
        f"open({pid_linux_path!r}, 'w').write(str(os.getpid()))\n"
        "for c in (0.10, 0.30, 0.90):\n"
        "    emit({'type':'cost','cost':c,'iter':1}); time.sleep(0.05)\n"
        "time.sleep(60)\n"
    ))

    def capping(cum, note=None):
        if cum > 0.20:
            raise StepBudgetExceeded(f"cap exceeded at ${cum}")

    import time as _t
    t0 = _t.time()
    raised = False
    try:
        _run(_stream_subprocess(
            "wsl", ["-d", "Ubuntu", "-u", "root", "python3", child],
            {"prompt": "x", "env_overlay": {"TERMINAL_ENV": "docker"}},
            capping,
        ))
    except StepBudgetExceeded:
        raised = True
    elapsed = _t.time() - t0

    assert raised, "cost-cap must raise in the WSL transport"
    assert elapsed < 30
    # Give WSL a beat to tear the process down, then confirm it's gone.
    _t.sleep(1.0)
    child_pid = int(pidmnt.read_text().strip())
    assert not _linux_pid_alive(child_pid), \
        f"Linux child {child_pid} survived parent kill — money teeth weak in WSL"
