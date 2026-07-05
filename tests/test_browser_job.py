# -*- coding: utf-8 -*-
"""BrowserJob (playbook slot) + playbook persistence. $0, no browser, no network."""
from app.services.browser.job import BrowserJob
from app.services.browser import playbook as pb


def test_browserjob_roundtrip_preserves_all_fields():
    job = BrowserJob(name="watch-comp", urls=["https://ex.com"], task="find price",
                     mode="read", allowed_domains=["ex.com"], extract="price",
                     model="claude-sonnet-4-6", max_steps=10, max_wall_s=90,
                     schedule="30m")
    d = job.to_dict()
    back = BrowserJob.from_dict(d)
    assert back == job
    assert d["mode"] == "read" and d["schedule"] == "30m"


def test_browserjob_defaults_are_read_and_safe():
    job = BrowserJob(urls=["https://ex.com"], task="t")
    assert job.mode == "read"            # default is read-only, never act
    assert job.name is None and job.schedule is None
    assert job.allowed_domains == []     # explicit, not None


def test_from_dict_ignores_unknown_but_keeps_known():
    d = {"urls": ["https://ex.com"], "task": "t", "mode": "read", "future_field": 1}
    job = BrowserJob.from_dict(d)
    assert job.task == "t" and job.mode == "read"


def test_playbook_save_then_load_is_identity(tmp_path):
    job = BrowserJob(name="daily", urls=["https://ex.com"], task="t", schedule="1d")
    pb.save(job, base_dir=tmp_path)
    loaded = pb.load("daily", base_dir=tmp_path)
    assert loaded == job
    assert "daily" in pb.list_names(base_dir=tmp_path)
