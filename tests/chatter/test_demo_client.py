from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def test_demo_config_loads():
    cfg = load_config(CLIENTS, "demo")
    assert cfg.settings.language == "ru"
    assert cfg.settings.model == "claude-haiku-4-5"
    assert "консультац" in cfg.knowledge.lower()
    assert "фотосесс" in cfg.knowledge.lower()
    assert cfg.settings.owner_id
    assert cfg.settings.timings.debounce_max > 0
    assert cfg.settings.timings.debounce_window > 0
