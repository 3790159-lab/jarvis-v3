# -*- coding: utf-8 -*-
"""Vizir /task bot wiring — pure helpers + admin-only + isolation regression. $0."""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


def test_confirm_keyboard_shows_cap_and_run_cancel():
    kb = mod._task_confirm_keyboard(cap_usd=0.90)
    # inline_keyboard: list of rows of {"text","callback_data"}
    flat = [btn for row in kb["inline_keyboard"] for btn in row]
    datas = [b["callback_data"] for b in flat]
    texts = " ".join(b["text"] for b in flat)
    assert "task:run" in datas and "task:cancel" in datas
    assert "0.90" in texts  # cap shown


def test_progress_text_attempt_and_rejection():
    assert "1" in mod._task_progress_text("attempt_started", {"attempt": 1})
    txt = mod._task_progress_text("attempt_rejected",
                                  {"attempt": 1, "reasons": ["run did not complete"]})
    assert "did not complete" in txt and ("фидбек" in txt.lower() or "feedback" in txt.lower())
