# -*- coding: utf-8 -*-
"""Browser service: single-flight + estimate + run orchestration. $0, no browser."""
import pytest

from app.services.browser import service as svc
from app.services.browser.job import BrowserJob
from app.services.browser.engine import BrowserResult


def _job(**kw):
    base = dict(urls=["https://ex.com"], task="find price", allowed_domains=["ex.com"])
    base.update(kw)
    return BrowserJob(**base)


def teardown_function(_):
    svc.release()                       # never leak the lock between tests


def test_single_flight_blocks_second_run():
    assert svc.claim() is True
    assert svc.active() is True
    assert svc.claim() is False         # second claim refused while active
    svc.release()
    assert svc.active() is False


def test_estimate_scales_with_steps_and_is_positive():
    cheap = svc.estimate_usd(_job(max_steps=5))
    dear = svc.estimate_usd(_job(max_steps=25))
    assert 0 < cheap < dear             # more steps → higher estimate


def test_run_browse_opens_session_runs_and_persists(tmp_path):
    seen = {}
    def fake_run(job, *, run_agent=None):
        seen["ran"] = True
        return BrowserResult(steps=4, cost_usd=0.05, extracted="1499", stopped_reason="done",
                             raw_dom="<html>x</html>")
    res = svc.run_browse(_job(), sessions_dir=tmp_path, run=fake_run, session_id="s1")
    assert seen["ran"] and res.extracted == "1499"
    assert (tmp_path / "s1").exists()               # session dir created
    assert svc.active() is False                    # lock released after run


def test_run_browse_releases_lock_on_error(tmp_path):
    def boom(job, *, run_agent=None):
        raise RuntimeError("browser died")
    with pytest.raises(RuntimeError):
        svc.run_browse(_job(), sessions_dir=tmp_path, run=boom, session_id="s2")
    assert svc.active() is False                    # lock freed even on failure
