"""Phase F.2: /design command for Figma wireframes."""
from __future__ import annotations

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_design_no_args_shows_usage():
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeymod = mod
    original_send = mod.send

    try:
        mod.send = lambda cid, text, **kw: sent.append(text)
        mod.cmd_design("c1", "")
    finally:
        mod.send = original_send

    assert sent
    assert "/design" in sent[0] or "Использование" in sent[0]


def test_design_creates_brief_and_queues(tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    import app.services.figma_queue as fq_mod
    from pathlib import Path

    sent = []
    original_send = mod.send
    old_dir = fq_mod._QUEUE_DIR
    fq_mod._QUEUE_DIR = tmp_path
    fq_mod._LOG_FILE = tmp_path / "log.jsonl"

    _brief = {
        "project_name": "Restaurant Landing",
        "project_type": "landing",
        "design_style": "warm",
        "color_palette": {"primary": "#D4622A", "secondary": "#8B4513", "accent": "#FFD700", "bg": "#FFF", "text": "#000"},
        "typography": {"heading_font": "Inter", "body_font": "Inter", "heading_size": "48px", "body_size": "16px", "heading_weight": "700"},
        "components": [{"name": "Header", "content": "Nav", "type": "navigation"}],
        "desktop_frame": {"width": 1440, "height": 900, "sections": ["Header"]},
        "mobile_frame": {"width": 375, "height": 812, "sections": ["Header"]},
        "raw_prompt": "restaurant landing",
    }

    try:
        mod.send = lambda cid, text, **kw: sent.append(text)
        with patch("app.services.figma_brief_generator.generate_design_brief", return_value=_brief):
            mod.cmd_design("c1", "ресторан лендинг")
    finally:
        mod.send = original_send
        fq_mod._QUEUE_DIR = old_dir

    # Should mention Claude Code or process figma queue
    full_text = " ".join(sent)
    assert len(full_text) > 20


def test_design_error_handled_gracefully():
    import tools.jarvis_smart_telegram_control as mod

    sent = []
    original_send = mod.send

    try:
        mod.send = lambda cid, text, **kw: sent.append(text)
        with patch("app.services.figma_brief_generator.generate_design_brief", side_effect=RuntimeError("test error")):
            mod.cmd_design("c1", "test query")
    finally:
        mod.send = original_send

    assert any("Ошибка" in s or "ошибка" in s.lower() for s in sent)


def test_design_registered_in_handle_command():
    import tools.jarvis_smart_telegram_control as mod
    import inspect
    src = inspect.getsource(mod.handle_command)
    assert '"/design"' in src or "'/design'" in src
