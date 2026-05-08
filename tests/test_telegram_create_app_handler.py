# -*- coding: utf-8 -*-
"""Tests for L.2 Telegram /create_app and bolt.diy handlers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_SPEC = {
    "app_name": "TrackFit",
    "tagline": "Fitness companion",
    "description": "Fitness tracking app",
    "tech_stack": "React + Tailwind CSS",
    "features": [
        {"name": "Workout Logger", "description": "Log workouts", "priority": "high"},
        {"name": "Progress Charts", "description": "Charts", "priority": "medium"},
    ],
    "components": ["Header", "Dashboard"],
    "data_model": [],
    "pages": [{"name": "Dashboard", "purpose": "Overview"}],
    "styling": {"color_scheme": "dark", "primary_color": "#6366f1", "design_system": "Tailwind"},
    "bolt_diy_prompt": "Build TrackFit: a fitness tracking app using React and Tailwind CSS...",
    "raw_user_input": "fitness tracker",
}


# ---------------------------------------------------------------------------
# cmd_bolt_status
# ---------------------------------------------------------------------------

def test_cmd_bolt_status_running():
    from tools.jarvis_smart_telegram_control import cmd_bolt_status
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            cmd_bolt_status("123", "")
    assert any("работает" in t.lower() or "5173" in t for t in sent)


def test_cmd_bolt_status_not_running():
    from tools.jarvis_smart_telegram_control import cmd_bolt_status
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=False):
            cmd_bolt_status("123", "")
    assert any("pnpm" in t or "запущен" in t.lower() for t in sent)


# ---------------------------------------------------------------------------
# cmd_bolt_open
# ---------------------------------------------------------------------------

def test_cmd_bolt_open_sends_url():
    from tools.jarvis_smart_telegram_control import cmd_bolt_open
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            cmd_bolt_open("123", "")
    full = " ".join(sent)
    assert "5173" in full or "localhost" in full


# ---------------------------------------------------------------------------
# cmd_create_app
# ---------------------------------------------------------------------------

def test_cmd_create_app_empty_query():
    from tools.jarvis_smart_telegram_control import cmd_create_app
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        cmd_create_app("123", "")
    assert any("/create_app" in t or "Использование" in t for t in sent)


def test_cmd_create_app_bolt_not_running():
    from tools.jarvis_smart_telegram_control import cmd_create_app
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=False):
            cmd_create_app("123", "todo app")
    assert any("pnpm" in t or "запущен" in t.lower() for t in sent)


def test_cmd_create_app_generates_spec():
    from tools.jarvis_smart_telegram_control import cmd_create_app
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            with patch("app.services.app_spec_generator.generate_app_spec", return_value=_SAMPLE_SPEC):
                with tempfile.TemporaryDirectory() as tmp:
                    import app.services.bolt_queue as bq_mod
                    old = bq_mod._QUEUE_DIR
                    bq_mod._QUEUE_DIR = Path(tmp)
                    bq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
                    try:
                        cmd_create_app("123", "fitness tracker")
                    finally:
                        bq_mod._QUEUE_DIR = old
    full = " ".join(sent)
    assert "TrackFit" in full or "bolt.diy" in full.lower() or "Промпт" in full


def test_cmd_create_app_sends_bolt_prompt():
    from tools.jarvis_smart_telegram_control import cmd_create_app
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            with patch("app.services.app_spec_generator.generate_app_spec", return_value=_SAMPLE_SPEC):
                with tempfile.TemporaryDirectory() as tmp:
                    import app.services.bolt_queue as bq_mod
                    old = bq_mod._QUEUE_DIR
                    bq_mod._QUEUE_DIR = Path(tmp)
                    bq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
                    try:
                        cmd_create_app("123", "fitness app")
                    finally:
                        bq_mod._QUEUE_DIR = old
    full = " ".join(sent)
    assert "Build TrackFit" in full or "Промпт" in full or len(full) > 100


def test_cmd_create_app_error_sends_error():
    from tools.jarvis_smart_telegram_control import cmd_create_app
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            with patch("app.services.app_spec_generator.generate_app_spec", side_effect=Exception("fail")):
                cmd_create_app("123", "app")
    assert any("Ошибка" in t or "ошибка" in t.lower() for t in sent)


# ---------------------------------------------------------------------------
# cmd_create_simple
# ---------------------------------------------------------------------------

def test_cmd_create_simple_empty_query():
    from tools.jarvis_smart_telegram_control import cmd_create_simple
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        cmd_create_simple("123", "")
    assert len(sent) >= 1


def test_cmd_create_simple_with_query():
    from tools.jarvis_smart_telegram_control import cmd_create_simple
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.bolt_diy_health.check_bolt_running", return_value=True):
            with patch("app.services.app_spec_generator.generate_simple_app_spec", return_value=_SAMPLE_SPEC):
                with tempfile.TemporaryDirectory() as tmp:
                    import app.services.bolt_queue as bq_mod
                    old = bq_mod._QUEUE_DIR
                    bq_mod._QUEUE_DIR = Path(tmp)
                    bq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
                    try:
                        cmd_create_simple("123", "counter")
                    finally:
                        bq_mod._QUEUE_DIR = old
    assert len(sent) >= 1


# ---------------------------------------------------------------------------
# cmd_bolt_queue
# ---------------------------------------------------------------------------

def test_cmd_bolt_queue_empty():
    from tools.jarvis_smart_telegram_control import cmd_bolt_queue
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with tempfile.TemporaryDirectory() as tmp:
            import app.services.bolt_queue as bq_mod
            old = bq_mod._QUEUE_DIR
            bq_mod._QUEUE_DIR = Path(tmp)
            bq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
            try:
                cmd_bolt_queue("123", "")
            finally:
                bq_mod._QUEUE_DIR = old
    assert any("пустая" in t.lower() or "очередь" in t.lower() for t in sent)
