from __future__ import annotations
from pathlib import Path
import textwrap
import pytest
from chatter.config.loader import DEFAULT_LIMITS, load_config, ConfigError

SETTINGS = """\
model: claude-haiku-4-5
language: ru
owner_id: "owner-1"
persona_name: "Аня"
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
  debounce_max: 15.0
limits:
  max_reply_tokens: 20000
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

def test_missing_optional_key_now_uses_default(tmp_path):
    """Онбординг-дырка №1 сменила контракт: раньше пропуск daily_cap ронял
    клиента (12 обязательных полей в timings + 3 в limits — забыл одно, не
    стартуешь). Теперь у таких полей есть боевые дефолты."""
    relaxed = "\n".join(l for l in SETTINGS.splitlines() if "daily_cap" not in l)
    _make_client(tmp_path, settings=relaxed)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.limits.daily_cap == DEFAULT_LIMITS.daily_cap

def test_missing_identity_key_still_fails(tmp_path):
    """А вот идентичность персоны дефолту не подлежит — без неё конфиг
    бессмыслен, и молчать об этом нельзя."""
    broken = "\n".join(l for l in SETTINGS.splitlines() if "persona_name" not in l)
    _make_client(tmp_path, settings=broken)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "persona_name" in str(e.value)

def test_persona_name_equals_owner_fails(tmp_path):
    colliding = SETTINGS.replace('owner_id: "owner-1"', 'owner_id: "Аня"')
    _make_client(tmp_path, settings=colliding)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    msg = str(e.value)
    assert "persona_name" in msg and "owner_id" in msg

def test_telegram_block_absent_is_none(tmp_path):
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.telegram is None

def test_telegram_block_parses_allowlist(tmp_path):
    settings = SETTINGS + "telegram:\n  allowlist: [237616472, 42]\n"
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.telegram is not None
    assert cfg.settings.telegram.allowlist == (237616472, 42)

def test_telegram_funnel_gate_and_denylist_default_off(tmp_path):
    # Арка 3C: без явной настройки переворот гейта ВЫКЛЮЧЕН (безопасный дефолт).
    settings = SETTINGS + "telegram:\n  allowlist: [237616472]\n"
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.telegram.funnel_gate is False
    assert cfg.settings.telegram.denylist == ()


def test_telegram_funnel_gate_and_denylist_parse(tmp_path):
    settings = SETTINGS + (
        "telegram:\n"
        "  allowlist: [237616472]\n"
        "  denylist: [666, 777]\n"
        "  funnel_gate: true\n"
    )
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.telegram.funnel_gate is True
    assert cfg.settings.telegram.denylist == (666, 777)


def test_telegram_block_missing_allowlist_key_fails(tmp_path):
    settings = SETTINGS + "telegram: {}\n"
    _make_client(tmp_path, settings=settings)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "allowlist" in str(e.value)

def test_control_block_is_optional_and_has_defaults(tmp_path):
    # Клиент без блока control обязан работать: дефолты живут в коде.
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.control.auto_resume_hours == 6.0
    assert cfg.settings.control.takeover_grace_seconds == 2.0
    assert cfg.settings.control.status_window_hours == 24

def test_control_block_overrides_defaults(tmp_path):
    settings = SETTINGS + (
        "control:\n"
        "  auto_resume_hours: 2\n"
        "  takeover_grace_seconds: 0.5\n"
        "  status_window_hours: 48\n"
    )
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.control.auto_resume_hours == 2.0
    assert cfg.settings.control.takeover_grace_seconds == 0.5
    assert cfg.settings.control.status_window_hours == 48

def test_control_block_non_mapping_fails(tmp_path):
    settings = SETTINGS + "control: \"nope\"\n"
    _make_client(tmp_path, settings=settings)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "control" in str(e.value)


def test_control_bot_fields_default_off(tmp_path):
    # Арка 3B: без блока control контрол-бот выключен, эскалация на дефолтах.
    # Токен НЕ настроен -> Saved Messages остаётся фоллбеком (инвариант арки).
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    c = cfg.settings.control
    assert c.control_bot_token_env is None
    assert c.owner_chat_id is None
    assert c.pairing_code is None
    assert c.classifier_enabled is True
    assert c.classifier_error_threshold == 5
    assert c.snooze_seconds == 3600.0


def test_pairing_code_with_invalid_chars_fails(tmp_path):
    # deep link t.me/<bot>?start=<код> ограничивает payload [A-Za-z0-9_-], ≤64.
    settings = SETTINGS + "control:\n  pairing_code: \"bad code!\"\n"
    _make_client(tmp_path, settings=settings)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "pairing_code" in str(e.value)


def test_control_bot_fields_parse(tmp_path):
    settings = SETTINGS + (
        "control:\n"
        "  control_bot_token_env: CHATTER_CONTROL_BOT_TOKEN\n"
        "  owner_chat_id: 237616472\n"
        "  pairing_code: pair-abc-123\n"
        "  classifier_enabled: false\n"
        "  classifier_error_threshold: 3\n"
        "  snooze_seconds: 1800\n"
    )
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    c = cfg.settings.control
    # settings.yaml хранит ИМЯ переменной окружения, не значение токена.
    assert c.control_bot_token_env == "CHATTER_CONTROL_BOT_TOKEN"
    assert c.owner_chat_id == 237616472
    assert c.pairing_code == "pair-abc-123"
    assert c.classifier_enabled is False
    assert c.classifier_error_threshold == 3
    assert c.snooze_seconds == 1800.0
