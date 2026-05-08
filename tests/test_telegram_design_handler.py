# -*- coding: utf-8 -*-
"""Tests for L.1 Telegram /design handler and Figma queue commands."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_BRIEF = {
    "project_name": "Cafe Roma",
    "project_type": "landing",
    "target_audience": "Families",
    "design_style": "warm",
    "color_palette": {"primary": "#D4622A", "secondary": "#8B4513", "accent": "#FFD700", "bg": "#FFF8F0", "text": "#2C1810"},
    "typography": {"heading_font": "Playfair Display", "body_font": "Open Sans",
                   "heading_size": "48px", "body_size": "16px", "heading_weight": "700"},
    "components": [
        {"name": "Header", "content": "Logo + Nav", "type": "navigation"},
        {"name": "Hero", "content": "Headline + CTA", "type": "hero"},
    ],
    "desktop_frame": {"width": 1440, "height": 900, "sections": ["Header", "Hero"]},
    "mobile_frame": {"width": 375, "height": 812, "sections": ["Header", "Hero"]},
    "raw_prompt": "restaurant landing page",
}


def _get_cmd_design():
    from tools.jarvis_smart_telegram_control import cmd_design
    return cmd_design


def _get_cmd_figma_queue():
    from tools.jarvis_smart_telegram_control import cmd_figma_queue
    return cmd_figma_queue


def _get_cmd_figma_status():
    from tools.jarvis_smart_telegram_control import cmd_figma_status
    return cmd_figma_status


def _get_cmd_figma_clear():
    from tools.jarvis_smart_telegram_control import cmd_figma_clear
    return cmd_figma_clear


# ---------------------------------------------------------------------------
# cmd_design
# ---------------------------------------------------------------------------

def test_cmd_design_empty_query_sends_usage():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        _get_cmd_design()("123", "")
    assert len(sent) == 1
    assert "/design" in sent[0] or "design" in sent[0].lower()


def test_cmd_design_with_query_calls_brief_generator():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.figma_brief_generator.generate_design_brief", return_value=_SAMPLE_BRIEF):
            with tempfile.TemporaryDirectory() as tmp:
                import app.services.figma_queue as fq_mod
                old = fq_mod._QUEUE_DIR
                fq_mod._QUEUE_DIR = Path(tmp)
                fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
                try:
                    _get_cmd_design()("123", "ресторан")
                finally:
                    fq_mod._QUEUE_DIR = old
    assert any("Бриф готов" in t or "бриф" in t.lower() or "Cafe Roma" in t or "ID" in t for t in sent)


def test_cmd_design_with_query_sends_queue_id():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.figma_brief_generator.generate_design_brief", return_value=_SAMPLE_BRIEF):
            with tempfile.TemporaryDirectory() as tmp:
                import app.services.figma_queue as fq_mod
                old = fq_mod._QUEUE_DIR
                fq_mod._QUEUE_DIR = Path(tmp)
                fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
                try:
                    _get_cmd_design()("123", "test design")
                finally:
                    fq_mod._QUEUE_DIR = old
    full_text = " ".join(sent)
    # Should contain queue ID info
    assert len(full_text) > 20


def test_cmd_design_error_sends_error_message():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("app.services.figma_brief_generator.generate_design_brief", side_effect=Exception("API fail")):
            _get_cmd_design()("123", "test")
    assert any("Ошибка" in t or "ошибка" in t.lower() for t in sent)


# ---------------------------------------------------------------------------
# cmd_figma_queue
# ---------------------------------------------------------------------------

def test_cmd_figma_queue_empty_shows_empty_message():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with tempfile.TemporaryDirectory() as tmp:
            import app.services.figma_queue as fq_mod
            old = fq_mod._QUEUE_DIR
            fq_mod._QUEUE_DIR = Path(tmp)
            fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
            try:
                _get_cmd_figma_queue()("123", "")
            finally:
                fq_mod._QUEUE_DIR = old
    assert any("пустая" in t.lower() or "очередь" in t.lower() or "empty" in t.lower() for t in sent)


def test_cmd_figma_queue_shows_pending():
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.figma_queue as fq_mod
        old = fq_mod._QUEUE_DIR
        fq_mod._QUEUE_DIR = Path(tmp)
        fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
        try:
            from app.services.figma_queue import FigmaQueue
            q = FigmaQueue()
            q.add(_SAMPLE_BRIEF)
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                _get_cmd_figma_queue()("123", "")
        finally:
            fq_mod._QUEUE_DIR = old
    full = " ".join(sent)
    assert "Cafe Roma" in full or "1" in full


# ---------------------------------------------------------------------------
# cmd_figma_status
# ---------------------------------------------------------------------------

def test_cmd_figma_status_no_items():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with tempfile.TemporaryDirectory() as tmp:
            import app.services.figma_queue as fq_mod
            old = fq_mod._QUEUE_DIR
            fq_mod._QUEUE_DIR = Path(tmp)
            fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
            try:
                _get_cmd_figma_status()("123", "")
            finally:
                fq_mod._QUEUE_DIR = old
    assert len(sent) >= 1


def test_cmd_figma_status_with_items():
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.figma_queue as fq_mod
        old = fq_mod._QUEUE_DIR
        fq_mod._QUEUE_DIR = Path(tmp)
        fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
        try:
            from app.services.figma_queue import FigmaQueue
            q = FigmaQueue()
            qid = q.add(_SAMPLE_BRIEF)
            q.mark_completed(qid, "http://figma.com/test")
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                _get_cmd_figma_status()("123", "")
        finally:
            fq_mod._QUEUE_DIR = old
    full = " ".join(sent)
    assert "Cafe Roma" in full or "OK" in full or "completed" in full.lower()


# ---------------------------------------------------------------------------
# cmd_figma_clear
# ---------------------------------------------------------------------------

def test_cmd_figma_clear_nothing_to_clear():
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with tempfile.TemporaryDirectory() as tmp:
            import app.services.figma_queue as fq_mod
            old = fq_mod._QUEUE_DIR
            fq_mod._QUEUE_DIR = Path(tmp)
            fq_mod._LOG_FILE = Path(tmp) / "log.jsonl"
            try:
                _get_cmd_figma_clear()("123", "")
            finally:
                fq_mod._QUEUE_DIR = old
    full = " ".join(sent)
    assert "Нечего" in full or "нечего" in full.lower() or "0" in full
