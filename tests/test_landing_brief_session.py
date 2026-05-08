# -*- coding: utf-8 -*-
"""Tests for landing_brief_session.py (Phase L.4)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_session(tmp_dir: str, user_id: str = "u1", chat_id: str = "c1"):
    import app.services.landing_brief_session as mod
    old = mod._BRIEFS_DIR
    mod._BRIEFS_DIR = Path(tmp_dir)
    from app.services.landing_brief_session import LandingBriefSession
    sess = LandingBriefSession(user_id, chat_id)
    return sess, mod, old


def test_session_created_with_active_status():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            assert sess.is_active()
            assert not sess.is_complete()
        finally:
            mod._BRIEFS_DIR = old


def test_session_has_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            assert len(sess.session_id) >= 15
        finally:
            mod._BRIEFS_DIR = old


def test_session_first_question():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            q = sess.get_current_question()
            assert "1/8" in q or "Шаг 1" in q
        finally:
            mod._BRIEFS_DIR = old


def test_session_advance_moves_to_next_step():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            completed, next_q = sess.advance("Cafe Lapin")
            assert not completed
            assert "2/8" in next_q
        finally:
            mod._BRIEFS_DIR = old


def test_session_advance_stores_answer():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            sess.advance("Cafe Lapin")
            assert sess.data["business_name"] == "Cafe Lapin"
        finally:
            mod._BRIEFS_DIR = old


def test_session_8_steps_to_complete():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            answers = ["Cafe Lapin", "Families 25-45", "French cuisine",
                       "Quality, Atmosphere, Value", "Book a table",
                       "warm", "luxury", "info@cafe.com"]
            for i, answer in enumerate(answers[:-1]):
                completed, next_q = sess.advance(answer)
                assert not completed, f"Should not complete at step {i+1}"
            completed, _ = sess.advance(answers[-1])
            assert completed
        finally:
            mod._BRIEFS_DIR = old


def test_session_completed_has_all_data():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            for ans in ["Name", "Audience", "Product", "Adv1, Adv2, Adv3",
                        "Buy now", "warm", "luxury", "email@test.com"]:
                sess.advance(ans)
            data = sess.get_brief_data()
            assert "business_name" in data
            assert "target_audience" in data
            assert "style" in data
        finally:
            mod._BRIEFS_DIR = old


def test_session_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            sess.cancel()
            assert not sess.is_active()
            assert sess.status == "cancelled"
        finally:
            mod._BRIEFS_DIR = old


def test_session_cancel_stops_advance():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            sess.cancel()
            completed, _ = sess.advance("some answer")
            assert completed  # cancelled = no more questions
        finally:
            mod._BRIEFS_DIR = old


def test_session_persisted_to_file():
    with tempfile.TemporaryDirectory() as tmp:
        sess, mod, old = _make_session(tmp)
        try:
            sess.advance("Cafe Lapin")
            # Session file should exist
            user_dir = Path(tmp) / "u1"
            files = list(user_dir.glob("*.json"))
            assert len(files) >= 1
        finally:
            mod._BRIEFS_DIR = old


def test_find_active_returns_session():
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            from app.services.landing_brief_session import LandingBriefSession
            sess = LandingBriefSession("u2", "c2")
            found = LandingBriefSession.find_active("u2")
            assert found is not None
            assert found.session_id == sess.session_id
        finally:
            mod._BRIEFS_DIR = old


def test_find_active_returns_none_after_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.landing_brief_session as mod
        old = mod._BRIEFS_DIR
        mod._BRIEFS_DIR = Path(tmp)
        try:
            from app.services.landing_brief_session import LandingBriefSession
            sess = LandingBriefSession("u3", "c3")
            sess.cancel()
            found = LandingBriefSession.find_active("u3")
            assert found is None
        finally:
            mod._BRIEFS_DIR = old


def test_session_step_count():
    from app.services.landing_brief_session import STEPS
    assert len(STEPS) == 8


def test_session_step_keys_unique():
    from app.services.landing_brief_session import STEPS
    keys = [s[0] for s in STEPS]
    assert len(keys) == len(set(keys))
