from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def test_demo_config_loads():
    cfg = load_config(CLIENTS, "demo")
    assert cfg.settings.language == "ru"
    # sonnet-5, а НЕ haiku: стабильный префикс классификатора demo — 3021 токен
    # при пороге включения кэша у haiku 4096. Ниже порога кэш выключается молча,
    # и «дешёвая» модель выходит дороже. Сторож §2.2 (prefix_budget) откажется
    # поднимать demo на haiku. Перевернуть эту строку обратно можно только
    # вместе с наращиванием плейбука demo выше 4096.
    assert cfg.settings.model == "claude-sonnet-5"
    assert "консультац" in cfg.knowledge.lower()
    assert "фотосесс" in cfg.knowledge.lower()
    assert cfg.settings.owner_id
    assert cfg.settings.timings.debounce_max > 0
    assert cfg.settings.timings.debounce_window > 0
