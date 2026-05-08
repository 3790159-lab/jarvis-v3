# -*- coding: utf-8 -*-
"""Tests for L.4 Telegram landing brief handlers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_CONTENT = {
    "hero_headline": "Amazing Service",
    "hero_subheadline": "We do great things.",
    "cta_text": "Get Started",
    "features": [{"title": "F1", "description": "D1", "icon_name": "star"}],
    "testimonials": [{"quote": "Great!", "name": "Client", "role": "Customer"}],
    "faq": [{"question": "Q1?", "answer": "A1."}],
    "about_text": "About us.",
    "footer_links": ["Contact"],
}


# ---------------------------------------------------------------------------
# cmd_landing_brief
# ---------------------------------------------------------------------------

def test_cmd_landing_brief_sends_first_question():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with tempfile.TemporaryDirectory() as tmp:
            import app.services.landing_brief_session as mod
            old = mod._BRIEFS_DIR
            mod._BRIEFS_DIR = Path(tmp)
            try:
                cmd_landing_brief("123", "")
            finally:
                mod._BRIEFS_DIR = old
    full = " ".join(sent)
    assert "1/8" in full or "Шаг 1" in full


def test_cmd_landing_brief_cancels_existing():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief
    from app.services.landing_brief_session import LandingBriefSession
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            # Create first session
            sess1 = LandingBriefSession("123", "123")
            sess1_id = sess1.session_id
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                cmd_landing_brief("123", "")
            # Old session should be cancelled
            old_sess = LandingBriefSession("123", "123", session_id=sess1_id)
            assert old_sess.status == "cancelled"
        finally:
            mod._BRIEFS_DIR = old


# ---------------------------------------------------------------------------
# cmd_landing_brief_answer
# ---------------------------------------------------------------------------

def test_landing_brief_answer_returns_false_no_session():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief_answer
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            result = cmd_landing_brief_answer("999", "answer")
        finally:
            mod._BRIEFS_DIR = old
    assert result is False


def test_landing_brief_answer_returns_true_when_session_active():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief_answer
    from app.services.landing_brief_session import LandingBriefSession
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            LandingBriefSession("test_uid", "test_uid")
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                result = cmd_landing_brief_answer("test_uid", "Cafe Lapin")
        finally:
            mod._BRIEFS_DIR = old
    assert result is True


def test_landing_brief_answer_sends_next_question():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief_answer
    from app.services.landing_brief_session import LandingBriefSession
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            LandingBriefSession("test2", "test2")
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                cmd_landing_brief_answer("test2", "Cafe Lapin")
        finally:
            mod._BRIEFS_DIR = old
    assert any("2/8" in t for t in sent)


def test_landing_brief_all_8_answers_generates_landing():
    from tools.jarvis_smart_telegram_control import cmd_landing_brief_answer
    from app.services.landing_brief_session import LandingBriefSession
    sent = []
    answers = ["Cafe Lapin", "Families", "French food", "Q, A, V",
               "Book now", "warm", "luxury", "email@test.com"]

    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as lbs_mod
        import app.services.landing_generator_v2 as lgv2
        old_briefs = lbs_mod._BRIEFS_DIR
        old_landings = lgv2._LANDINGS_DIR
        lbs_mod._BRIEFS_DIR = Path(tmp)
        lgv2._LANDINGS_DIR = Path(tmp) / "landings"
        try:
            LandingBriefSession("u_complete", "u_complete")
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                with patch("app.services.landing_content_generator.generate_landing_content", return_value=_SAMPLE_CONTENT):
                    for answer in answers:
                        cmd_landing_brief_answer("u_complete", answer)
        finally:
            lbs_mod._BRIEFS_DIR = old_briefs
            lgv2._LANDINGS_DIR = old_landings

    full = " ".join(sent)
    assert "готов" in full.lower() or "landing" in full.lower() or "Лендинг" in full


# ---------------------------------------------------------------------------
# cmd_cancel
# ---------------------------------------------------------------------------

def test_cmd_cancel_with_session():
    from tools.jarvis_smart_telegram_control import cmd_cancel
    from app.services.landing_brief_session import LandingBriefSession
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            LandingBriefSession("cancel_test", "cancel_test")
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                cmd_cancel("cancel_test", "")
        finally:
            mod._BRIEFS_DIR = old
    assert any("отмен" in t.lower() for t in sent)


def test_cmd_cancel_no_session():
    from tools.jarvis_smart_telegram_control import cmd_cancel
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
                cmd_cancel("no_session_user", "")
        finally:
            mod._BRIEFS_DIR = old
    assert any("Нет" in t or "нет" in t.lower() or "активн" in t.lower() for t in sent)


def test_cmd_landing_demo_sends_examples():
    from tools.jarvis_smart_telegram_control import cmd_landing_demo
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        cmd_landing_demo("123", "")
    assert any("Кафе" in t or "Cafe" in t or "FitSpace" in t or "landing_brief" in t for t in sent)
