# -*- coding: utf-8 -*-
"""Тумблер `/payments on confirm` и валидатор конфига на СТАРТЕ (§5.1).

Тумблер ОДИН, и он команда, а не правка файла: значение в yaml — факт, а не
цель, потому что гардиан деплоит из рабочего дерева и включённая в файле фича
поднялась бы на ребуте без команды владельца (та же причина, что у
`funnel_gate`).

Направление несимметрично намеренно: включение требует `confirm` (бот начинает
называть суммы и слать реквизиты), выключение исполняется сразу — аварию чинят
быстро, а не через второй экран.

Главное здесь — последний тест: тумблер защищён ТЕМ ЖЕ валидатором, что и старт.
Включить фичу на конфиге, которым нечего ответить, нельзя ни правкой файла, ни
командой: перезагрузка падает, файл откатывается, владелец видит причину.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from chatter.config.loader import ConfigError, load_config
from chatter.config.yaml_edit import YamlEditError, set_payments_enabled
from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"

PAYMENTS_BLOCK = """
payments:
  enabled: false
  channels:
    - id: iban_main
      kind: bank_transfer
      mode: manual
      currency: USD
      requisites_template: iban_main
      display: "Банківський переказ"
"""
REQUISITES = "templates:\n  iban_main:\n    body: |\n      IBAN UA00 0000 0000\n"


def _clients(tmp_path, *, payments=True, requisites=True) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    if payments:
        p = dst / "demo" / "settings.yaml"
        p.write_text(p.read_text(encoding="utf-8") + PAYMENTS_BLOCK, encoding="utf-8")
    if requisites:
        (dst / "demo" / "requisites.yaml").write_text(REQUISITES, encoding="utf-8")
    return dst


def _runner(clients):
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return build_runner(
        client=c, clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _enabled_of(path: Path) -> bool:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["payments"].get("enabled", False)


# ── чистый редактор yaml ───────────────────────────────────────────────────

def test_sets_enabled_when_key_absent_in_the_block():
    out = set_payments_enabled("payments:\n  channels: []\n", True)
    assert yaml.safe_load(out)["payments"]["enabled"] is True


def test_rewrites_live_key_in_place_keeping_the_comment():
    src = "payments:\n  enabled: false   # ← включает команда пульта\n"
    out = set_payments_enabled(src, True)
    assert "enabled: true" in out
    assert "← включает команда пульта" in out, "комментарий объясняет ЗАЧЕМ флаг — он не мусор"
    assert out.count("enabled:") == 1, "второй ключ рядом с первым — тот, который читает loader, неизвестен"


def test_rewrites_commented_out_key_in_place():
    src = "payments:\n  # enabled: true\n  channels: []\n"
    out = set_payments_enabled(src, True)
    assert yaml.safe_load(out)["payments"]["enabled"] is True
    assert out.count("enabled:") == 1


def test_live_key_wins_over_the_commented_sample():
    src = "payments:\n  # enabled: true   # образец\n  enabled: false\n"
    out = set_payments_enabled(src, True)
    assert yaml.safe_load(out)["payments"]["enabled"] is True


def test_off_writes_false():
    src = "payments:\n  enabled: true\n"
    assert yaml.safe_load(set_payments_enabled(src, False))["payments"]["enabled"] is False


def test_missing_payments_block_is_an_error_not_a_silent_creation():
    """Тумблер без каналов бессмыслен: включить было бы нечего. Молча
    сочинять блок платежей мы не станем — это конфиг про деньги."""
    with pytest.raises(YamlEditError, match="payments"):
        set_payments_enabled("model: x\n", True)


def test_editor_does_not_touch_other_keys():
    src = "telegram:\n  funnel_gate: false\npayments:\n  enabled: false\n"
    out = set_payments_enabled(src, True)
    assert "funnel_gate: false" in out


# ── валидатор на СТАРТЕ (через load_config) ────────────────────────────────

def test_loader_exposes_payments_section(tmp_path):
    cfg = load_config(_clients(tmp_path), "demo")
    assert cfg.settings.payments.enabled is False
    assert [c.id for c in cfg.settings.payments.channels] == ["iban_main"]
    assert cfg.settings.payments.requisites.templates["iban_main"].startswith("IBAN")


def test_client_without_payments_block_still_loads(tmp_path):
    cfg = load_config(_clients(tmp_path, payments=False, requisites=False), "demo")
    assert cfg.settings.payments.enabled is False


def test_enabled_without_requisites_is_a_start_error(tmp_path):
    """Приёмка §8.5 п.6. Ошибка СТАРТА, а не предупреждение: «включено, но
    сказать нечего» — ровно тот сломанный дефолт, который назвал владелец."""
    clients = _clients(tmp_path, requisites=False)
    p = clients / "demo" / "settings.yaml"
    p.write_text(set_payments_enabled(p.read_text(encoding="utf-8"), True), encoding="utf-8")
    with pytest.raises(ConfigError, match="iban_main"):
        load_config(clients, "demo")


def test_broken_requisites_yaml_is_loud(tmp_path):
    clients = _clients(tmp_path)
    (clients / "demo" / "requisites.yaml").write_text("templates: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="requisites.yaml"):
        load_config(clients, "demo")


def test_unknown_key_in_payments_block_is_a_start_error(tmp_path):
    clients = _clients(tmp_path)
    p = clients / "demo" / "settings.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "  due_hourz: 48\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="due_hourz"):
        load_config(clients, "demo")


# ── команда пульта ─────────────────────────────────────────────────────────

def test_status_without_argument_does_not_change_the_file(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    out = _run(r.handle_config_command("payments", "", language="ru"))
    assert out.strip()
    assert _enabled_of(clients / "demo" / "settings.yaml") is False


def test_on_without_confirm_only_warns(tmp_path):
    """Включение = бот начинает называть суммы и слать реквизиты живым лидам.
    Один тап с телефона на это права не даёт."""
    clients = _clients(tmp_path)
    r = _runner(clients)
    out = _run(r.handle_config_command("payments", "on", language="ru"))
    assert "confirm" in out
    assert _enabled_of(clients / "demo" / "settings.yaml") is False


def test_on_confirm_turns_it_on(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    _run(r.handle_config_command("payments", "on confirm", language="ru"))
    assert _enabled_of(clients / "demo" / "settings.yaml") is True
    assert r.personas["demo"].cfg.settings.payments.enabled is True, \
        "файл переписан, а живой конфиг раннера остался старым — тумблер не доехал"


def test_off_needs_no_confirm(tmp_path):
    """Безопасное направление исполняется сразу: аварию чинят быстро."""
    clients = _clients(tmp_path)
    p = clients / "demo" / "settings.yaml"
    p.write_text(set_payments_enabled(p.read_text(encoding="utf-8"), True), encoding="utf-8")
    r = _runner(clients)
    _run(r.handle_config_command("payments", "off", language="ru"))
    assert _enabled_of(p) is False


def test_unknown_argument_shows_usage(tmp_path):
    r = _runner(_clients(tmp_path))
    out = _run(r.handle_config_command("payments", "включи", language="ru"))
    assert "/payments" in out


def test_toggle_is_guarded_by_the_same_validator_as_start(tmp_path):
    """Включить фичу, которой нечего сказать, нельзя и командой: перезагрузка
    падает на валидаторе, файл откатывается к заведомо рабочему, владелец видит
    причину. Иначе тумблер был бы дырой в обход старта."""
    clients = _clients(tmp_path, requisites=False)
    p = clients / "demo" / "settings.yaml"
    before = p.read_text(encoding="utf-8")
    r = _runner(clients)
    out = _run(r.handle_config_command("payments", "on confirm", language="ru"))
    assert p.read_text(encoding="utf-8") == before, "файл остался включённым при упавшей валидации"
    assert _enabled_of(p) is False
    assert "iban_main" in out or "реквизит" in out.casefold()
