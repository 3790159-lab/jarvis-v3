from __future__ import annotations
from pathlib import Path
import textwrap
import pytest
from chatter.config.loader import load_config, ConfigError

SETTINGS = """\
model: claude-haiku-4-5
language: ru
owner_id: "owner-1"
work_hours: {start: 9, end: 22}
timings:
  read_delay_min: 1.0
  read_delay_max: 5.0
  cps_min: 3.0
  cps_max: 6.0
  jitter_min: 0.8
  jitter_max: 1.4
  split_pause_min: 0.5
  split_pause_max: 2.0
  split_max_len: 160
  night_multiplier: 2.0
  debounce_window: 3.0
limits:
  max_tokens_per_dialog: 20000
  per_contact_hourly: 20
  daily_cap: 500
"""

def _make_client(root: Path, slug: str = "demo", settings: str = SETTINGS):
    d = root / slug
    d.mkdir(parents=True)
    (d / "persona.md").write_text("Меня зовут Аня.", encoding="utf-8")
    (d / "knowledge.md").write_text("Консультация 5000.", encoding="utf-8")
    (d / "playbook.md").write_text("Стадии воронки.", encoding="utf-8")
    (d / "settings.yaml").write_text(settings, encoding="utf-8")
    return d

def test_loads_valid_config(tmp_path):
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.slug == "demo"
    assert cfg.persona.startswith("Меня зовут")
    assert cfg.settings.model == "claude-haiku-4-5"
    assert cfg.settings.language == "ru"
    assert cfg.settings.timings.cps_max == 6.0
    assert cfg.settings.limits.daily_cap == 500
    assert cfg.settings.work_hours.start == 9

def test_missing_file_fails_at_load(tmp_path):
    d = _make_client(tmp_path)
    (d / "knowledge.md").unlink()
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "knowledge.md" in str(e.value)

def test_empty_persona_fails(tmp_path):
    d = _make_client(tmp_path)
    (d / "persona.md").write_text("   ", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "persona.md" in str(e.value)

def test_bad_language_fails(tmp_path):
    _make_client(tmp_path, settings=SETTINGS.replace("language: ru", "language: fr"))
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "language" in str(e.value)

def test_missing_settings_key_fails(tmp_path):
    broken = "\n".join(l for l in SETTINGS.splitlines() if "daily_cap" not in l)
    _make_client(tmp_path, settings=broken)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "daily_cap" in str(e.value)
