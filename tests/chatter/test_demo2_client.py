from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def test_demo2_config_loads():
    cfg = load_config(CLIENTS, "demo2")
    assert cfg.settings.language == "en"
    assert cfg.settings.persona_name == "Dmitry"
    assert cfg.settings.owner_id == "Alex"
    assert cfg.settings.persona_name != cfg.settings.owner_id
    assert "automation" in cfg.knowledge.lower()
    assert "$" in cfg.knowledge
    assert cfg.settings.timings.debounce_max > 0
    assert cfg.settings.timings.debounce_window > 0
