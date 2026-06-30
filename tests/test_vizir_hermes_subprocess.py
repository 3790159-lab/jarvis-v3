# -*- coding: utf-8 -*-
"""Vizir Hermes subprocess plumbing — Phase L STEP 2 ($0, TDD, NO model).

The real Hermes adapter runs in a CHILD process (defence-in-depth: Hermes is a
huge agent loop with a history of process-killing bugs — #8049 os._exit on
max_iterations, patched in our pinned commit but a child guarantees Vizir lives
regardless; and step_callback exceptions are swallowed inside Hermes
[conversation_loop.py:664], so the cost-cap MUST be enforced by the PARENT killing
the child). These tests exercise ``_stream_subprocess`` against tiny FAKE child
scripts (plain Python, no Hermes, no model) so the money-safety mechanism — parent
reads streamed cost, parent KILLS child on cap breach — is proven at $0."""
import asyncio
import json
import os
import sys
import time

from app.services.vizir.coordinator import StepBudgetExceeded
from app.services.vizir.handlers_hermes import _stream_subprocess


def _run(coro):
    return asyncio.run(coro)


def _write_child(tmp_path, body):
    p = tmp_path / "fake_child.py"
    p.write_text("import sys, json, os, time\n"
                 "cfg = json.loads(sys.stdin.read())\n"
                 "def emit(o):\n"
                 "    sys.stdout.write(json.dumps(o) + '\\n'); sys.stdout.flush()\n"
                 + body, encoding="utf-8")
    return str(p)


def _stream(child, cfg, on_step):
    return _run(_stream_subprocess(sys.executable, [child], cfg, on_step))


def test_streams_cost_and_returns_translated_result(tmp_path):
    child = _write_child(tmp_path, (
        "for c in (0.01, 0.03, 0.06):\n"
        "    emit({'type':'cost','cost':c,'iter':1})\n"
        "emit({'type':'result','final_response':'<html>ok</html>','completed':True,\n"
        "      'turn_exit_reason':'text_response(finish_reason=stop)','partial':False,\n"
        "      'failed':False,'cost':0.06,'tokens':1234,'iterations':3})\n"
    ))
    seen = []
    out = _stream(child, {"prompt": "x", "artifact_path": "/tmp/a.html"},
                  lambda cum, note=None: seen.append(round(cum, 4)))

    assert seen == [0.01, 0.03, 0.06]                 # cumulative cost streamed
    assert out["final_response"] == "<html>ok</html>"
    assert abs(out["cost_usd"] - 0.06) < 1e-9
    assert out["iterations"] == 3
    assert out["stopped_reason"] == "completed"        # completed=True -> "completed"
    assert out["tokens"] == 1234
    assert out["artifact_path"] == "/tmp/a.html"


def test_incomplete_run_maps_turn_exit_reason(tmp_path):
    child = _write_child(tmp_path, (
        "emit({'type':'result','final_response':'partial','completed':False,\n"
        "      'turn_exit_reason':'max_iterations_reached(30/30)','partial':True,\n"
        "      'failed':False,'cost':0.04,'tokens':9,'iterations':30})\n"
    ))
    out = _stream(child, {"prompt": "x"}, lambda cum, note=None: None)
    assert out["stopped_reason"] == "max_iterations_reached(30/30)"   # not "completed"
    assert abs(out["cost_usd"] - 0.04) < 1e-9


def test_cap_breach_kills_child_and_raises(tmp_path):
    # Child writes its PID, streams escalating cost, then HANGS. The parent's
    # on_step raises StepBudgetExceeded once cumulative crosses the cap -> the
    # parent must KILL the child and propagate. Proves money-safety teeth.
    pidfile = tmp_path / "child.pid"
    child = _write_child(tmp_path, (
        "open(cfg['pidfile'],'w').write(str(os.getpid()))\n"
        "for c in (0.10, 0.30, 0.90):\n"
        "    emit({'type':'cost','cost':c,'iter':1}); time.sleep(0.05)\n"
        "time.sleep(60)\n"                               # hang AFTER overspending
        "emit({'type':'result','final_response':'late','completed':True,'cost':0.9})\n"
    ))

    def capping_on_step(cum, note=None):
        if cum > 0.20:                                  # cap = $0.20
            raise StepBudgetExceeded(f"cap exceeded at ${cum}")

    t0 = time.time()
    raised = False
    try:
        _stream(child, {"prompt": "x", "pidfile": str(pidfile)}, capping_on_step)
    except StepBudgetExceeded:
        raised = True
    elapsed = time.time() - t0

    assert raised                                        # cap propagated
    assert elapsed < 30                                  # did NOT wait the 60s hang
    # the child process is actually dead (parent killed it)
    child_pid = int(pidfile.read_text())
    assert not _pid_alive(child_pid), f"child {child_pid} still alive — not killed"


def test_child_self_exits_without_result_is_reported_not_fatal(tmp_path):
    # #8049 defence: even if the child os._exit(0)s without a result line, the
    # PARENT (Vizir) survives and reports a clean 'child_no_result' instead of dying.
    child = _write_child(tmp_path, (
        "emit({'type':'cost','cost':0.02,'iter':1})\n"
        "os._exit(0)\n"                                  # silent kill, no result line
    ))
    out = _stream(child, {"prompt": "x"}, lambda cum, note=None: None)
    assert out["stopped_reason"] == "child_no_result"
    assert out["final_response"] == ""


def _pid_alive(pid):
    # Windows-safe liveness: os.kill(pid, 0) is unreliable on Windows (sig 0 is
    # treated as TerminateProcess), so query tasklist instead.
    import subprocess
    for _ in range(10):
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True).stdout
        if str(pid) not in out:
            return False
        time.sleep(0.2)            # allow the OS a moment to reap after kill
    return True
