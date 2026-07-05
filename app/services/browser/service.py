# -*- coding: utf-8 -*-
"""Browser service: single-flight guard, cost estimate, run orchestration.

One browser session at a time (resources + one shared profile). Wraps the engine
with the session-artifact store; the money-gate (guard_spend) lives in the
command layer. See plan §3-4, BU1-T4/T7.
"""
from __future__ import annotations

import threading

from . import engine as _engine
from . import session_store as _ss
from .job import BrowserJob
from .engine import BrowserResult

_LOCK = threading.Lock()
_ACTIVE = {"on": False}

# rough per-step $ by model tier (est only; real cost recorded post-run).
_PER_STEP_USD = {"opus": 0.06, "sonnet": 0.02}


def _tier(model: str) -> str:
    return "opus" if "opus" in (model or "").lower() else "sonnet"


def estimate_usd(job: BrowserJob) -> float:
    """Pre-run estimate = per-step(model) × expected steps. Feeds guard_spend."""
    return round(_PER_STEP_USD[_tier(job.model)] * max(1, job.max_steps), 4)


def claim() -> bool:
    """Take the single-flight slot; False if a session is already running."""
    with _LOCK:
        if _ACTIVE["on"]:
            return False
        _ACTIVE["on"] = True
        return True


def active() -> bool:
    return _ACTIVE["on"]


def release() -> None:
    with _LOCK:
        _ACTIVE["on"] = False


def run_browse(job: BrowserJob, *, sessions_dir=None, run=None, run_agent=None,
               session_id: str = "session") -> BrowserResult:
    """Claim single-flight, open a session dir, run the engine, persist artifacts,
    always release. ``run``/``run_agent`` are injected in tests."""
    run = run or _engine.run_browser
    if not claim():
        raise RuntimeError("browser: другая сессия уже выполняется")
    try:
        kw = {} if sessions_dir is None else {"base_dir": sessions_dir}
        sess = _ss.open_session(session_id, **kw)
        res = run(job, run_agent=run_agent)
        try:
            _ss.write_artifact(sess, "result.json", str(res.__dict__))
            if res.raw_dom:
                _ss.write_artifact(sess, "dom/page.html", res.raw_dom)
            _ss.rotate(**kw)
        except Exception:
            pass                                    # artifacts best-effort
        return res
    finally:
        release()
