"""Онбординг-дырка №1: клиент создаётся из шаблона, а не 4 файлами руками.

Плюс: пропуск необязательного поля не роняет конфиг (было 12 обязательных
полей в timings — забыл одно, клиент не стартует), НО опечатка в имени поля
обязана быть громкой, иначе тихо поедут тайминги.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chatter.config.loader import ConfigError, load_config
from chatter.create_client import create_client, render_client


# --- дефолты вместо 12 обязательных полей -----------------------------------

def _write(tmp_path: Path, settings: str) -> Path:
    d = tmp_path / "clients" / "acme"
    d.mkdir(parents=True)
    (d / "persona.md").write_text("Меня зовут Аня, мне 26.", encoding="utf-8")
    (d / "knowledge.md").write_text("## Услуги\n- Что-то\n", encoding="utf-8")
    (d / "playbook.md").write_text("## Цель\n- Что-то\n", encoding="utf-8")
    (d / "settings.yaml").write_text(settings, encoding="utf-8")
    return tmp_path / "clients"


MINIMAL = """\
model: claude-haiku-4-5
language: ru
owner_id: "Дмитрий"
persona_name: "Аня"
"""


def test_minimal_settings_loads(tmp_path):
    """Четырёх строк должно хватать на старт — остальное дефолты."""
    cfg = load_config(_write(tmp_path, MINIMAL), "acme")
    assert cfg.settings.persona_name == "Аня"
    assert cfg.settings.timings.cps_min > 0        # дефолт подставлен
    assert cfg.settings.limits.daily_cap > 0
    assert cfg.settings.work_hours.end > cfg.settings.work_hours.start


def test_partial_timings_fills_rest(tmp_path):
    cfg = load_config(_write(tmp_path, MINIMAL + "timings:\n  cps_min: 9.0\n"), "acme")
    assert cfg.settings.timings.cps_min == 9.0     # своё значение уважили
    assert cfg.settings.timings.cps_max > 0        # остальное — дефолт


def test_partial_limits_fills_rest(tmp_path):
    cfg = load_config(_write(tmp_path, MINIMAL + "limits:\n  daily_cap: 42\n"), "acme")
    assert cfg.settings.limits.daily_cap == 42
    assert cfg.settings.limits.per_contact_hourly > 0


def test_typo_in_timings_is_loud(tmp_path):
    """Пропуск поля — ок, ОПЕЧАТКА — нет. Иначе тайминги тихо поедут и никто
    не поймёт, почему Аня печатает не так (DEV-18: не глотать молча)."""
    with pytest.raises(ConfigError) as e:
        load_config(_write(tmp_path, MINIMAL + "timings:\n  cps_mn: 9.0\n"), "acme")
    assert "cps_mn" in str(e.value)


def test_typo_in_limits_is_loud(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(_write(tmp_path, MINIMAL + "limits:\n  daily_capp: 5\n"), "acme")
    assert "daily_capp" in str(e.value)


def test_still_requires_the_four_identity_keys(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "model: x\nlanguage: ru\nowner_id: Д\n"), "acme")


# --- генератор клиента ------------------------------------------------------

def test_render_produces_all_four_files():
    files = render_client(slug="acme", persona_name="Аня", owner_id="Дмитрий")
    assert set(files) == {"persona.md", "knowledge.md", "playbook.md", "settings.yaml"}
    assert all(v.strip() for v in files.values())


def test_generated_persona_starts_with_prose_not_heading():
    """Связь с дыркой №0: шаблон обязан задавать ПРАВИЛЬНУЮ форму первой
    строки, иначе генератор сам воспроизводил бы починенный баг."""
    from chatter.run import _persona_first_line
    files = render_client(slug="acme", persona_name="Аня", owner_id="Дмитрий")
    first = files["persona.md"].splitlines()[0]
    assert not first.lstrip().startswith(("#", "-", "*", ">"))
    assert "Аня" in _persona_first_line(files["persona.md"])


def test_created_client_loads_with_our_own_loader(tmp_path):
    """Главный приёмочный тест: то, что сгенерировали, обязано грузиться."""
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий",
                  language="ru", currency="грн")
    cfg = load_config(clients, "acme")
    assert cfg.settings.persona_name == "Аня"
    assert cfg.settings.owner_id == "Дмитрий"
    assert cfg.settings.currency == "грн"
    assert cfg.settings.telegram.funnel_gate is False     # выключен по умолчанию


def test_created_client_has_pairing_code(tmp_path):
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий")
    cfg = load_config(clients, "acme")
    assert cfg.settings.control.pairing_code                # готов к /start


def test_refuses_to_clobber_existing_client(tmp_path):
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий")
    (clients / "acme" / "knowledge.md").write_text("боевая база", encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_client(clients, slug="acme", persona_name="Другая", owner_id="Дмитрий")
    assert (clients / "acme" / "knowledge.md").read_text(encoding="utf-8") == "боевая база"


def test_rejects_persona_equal_to_owner(tmp_path):
    """Лоадер это запрещает — генератор не должен создавать заведомо битое."""
    with pytest.raises(ValueError):
        create_client(tmp_path / "clients", slug="acme",
                      persona_name="Аня", owner_id="Аня")


@pytest.mark.parametrize("bad", ["", "../evil", "acme/x", "A B"])
def test_rejects_bad_slug(tmp_path, bad):
    with pytest.raises(ValueError):
        create_client(tmp_path / "clients", slug=bad,
                      persona_name="Аня", owner_id="Дмитрий")


def test_settings_yaml_keeps_explanatory_comments(tmp_path):
    """Сгенерированный settings.yaml — это ещё и инструкция клиенту."""
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий")
    text = (clients / "acme" / "settings.yaml").read_text(encoding="utf-8")
    assert "#" in text
    assert yaml.safe_load(text)                    # и при этом валидный YAML


def test_allowlist_can_be_set_at_creation(tmp_path):
    """Иначе первый же шаг после генерации — снова ручная правка файла."""
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий",
                  allowlist=[237616472])
    cfg = load_config(clients, "acme")
    assert cfg.settings.telegram.allowlist == (237616472,)


def test_allowlist_defaults_to_empty(tmp_path):
    clients = tmp_path / "clients"
    create_client(clients, slug="acme", persona_name="Аня", owner_id="Дмитрий")
    assert load_config(clients, "acme").settings.telegram.allowlist == ()
