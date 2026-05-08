"""Phase F.3: /landing command for HTML landing pages."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_generate_landing_creates_html_file(tmp_path):
    from app.services.landing_generator import generate_landing
    path = generate_landing("restaurant booking", str(tmp_path))
    assert os.path.exists(path)
    assert path.endswith(".html")


def test_generated_html_has_structure(tmp_path):
    from app.services.landing_generator import generate_landing
    path = generate_landing("SaaS tool", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "<!DOCTYPE html>" in content
    assert "tailwindcss" in content
    assert "Features" in content
    assert "Pricing" in content


def test_landing_uses_topic_in_title(tmp_path):
    from app.services.landing_generator import generate_landing
    path = generate_landing("online coffee shop", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "coffee" in content.lower() or "online" in content.lower()


def test_landing_no_args_shows_usage():
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    original_send = mod.send
    try:
        mod.send = lambda cid, text, **kw: sent.append(text)
        mod.cmd_landing("c1", "")
    finally:
        mod.send = original_send
    assert sent
    assert "/landing" in sent[0] or "Использование" in sent[0]


def test_landing_registered_in_handle_command():
    import tools.jarvis_smart_telegram_control as mod
    import inspect
    src = inspect.getsource(mod.handle_command)
    assert '"/landing"' in src or "'/landing'" in src


def test_landing_pricing_plans_present(tmp_path):
    from app.services.landing_generator import generate_landing
    path = generate_landing("fitness app", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "Free" in content
    assert "Pro" in content
    assert "Business" in content
