"""Онбординг-дырка №2: /funnel_gate on|off командой, а не правкой yaml.

Единственный переключатель, ошибка в котором = Аня пишет личным контактам
владельца. Он обязан быть командой с подтверждением, а не молчаливой строкой
в файле, которую правят руками в чужом редакторе.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from chatter.config.yaml_edit import YamlEditError, set_funnel_gate
from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def _clients(tmp_path) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    return dst


def _runner(clients):
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return build_runner(
        client=c, clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _gate_of(path: Path) -> bool:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["telegram"].get("funnel_gate", False)


# --- чистый редактор yaml ---------------------------------------------------

def test_sets_gate_when_key_absent():
    src = "telegram:\n  allowlist: [1]\n"
    out = set_funnel_gate(src, True)
    assert yaml.safe_load(out)["telegram"]["funnel_gate"] is True


def test_flips_existing_key():
    src = "telegram:\n  allowlist: [1]\n  funnel_gate: false\n"
    assert yaml.safe_load(set_funnel_gate(src, True))["telegram"]["funnel_gate"] is True
    src2 = "telegram:\n  allowlist: [1]\n  funnel_gate: true\n"
    assert yaml.safe_load(set_funnel_gate(src2, False))["telegram"]["funnel_gate"] is False


def test_activates_commented_out_key():
    """В demo-настройках ключ лежит закомментированным — команда обязана его
    активировать, а не добавить второй."""
    src = "telegram:\n  allowlist: [1]\n  # funnel_gate: true   # ← подтверждение\n"
    out = set_funnel_gate(src, True)
    assert yaml.safe_load(out)["telegram"]["funnel_gate"] is True
    assert out.count("funnel_gate: true") == 1


def test_preserves_surrounding_comments():
    """settings.yaml — половина документации продукта. Правка не смеет её съесть."""
    src = ("# верхний комментарий\nmodel: x\ntelegram:\n"
           "  # почему allowlist такой\n  allowlist: [1]\n")
    out = set_funnel_gate(src, True)
    assert "# верхний комментарий" in out
    assert "# почему allowlist такой" in out


def test_rejects_missing_telegram_block():
    with pytest.raises(YamlEditError):
        set_funnel_gate("model: x\n", True)


def test_real_demo_settings_roundtrip(tmp_path):
    """Боевой файл со всеми комментариями обязан пережить правку и остаться
    загружаемым нашим же лоадером."""
    from chatter.config.loader import load_config
    clients = _clients(tmp_path)
    p = clients / "demo" / "settings.yaml"
    p.write_text(set_funnel_gate(p.read_text(encoding="utf-8"), True), encoding="utf-8")
    cfg = load_config(clients, "demo")
    assert cfg.settings.telegram.funnel_gate is True
    assert cfg.settings.telegram.allowlist          # соседние ключи целы
    assert "Арка 3C" in p.read_text(encoding="utf-8")   # комментарии целы


# --- команда пульта ---------------------------------------------------------

def test_status_without_arg(tmp_path):
    r = _runner(_clients(tmp_path))
    out = _run(r.handle_config_command("funnel_gate", "", language="ru"))
    assert "выкл" in out.lower()


def test_on_requires_confirmation(tmp_path):
    """Опасное направление не должно исполняться с первого раза."""
    clients = _clients(tmp_path)
    r = _runner(clients)
    out = _run(r.handle_config_command("funnel_gate", "on", language="ru"))
    assert r.funnel_gate is False                       # НЕ включилось
    assert _gate_of(clients / "demo" / "settings.yaml") is False
    assert "подтвер" in out.lower()                     # объяснили, как подтвердить


def test_on_confirmed_enables_and_persists(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    _run(r.handle_config_command("funnel_gate", "on", language="ru"))
    out = _run(r.handle_config_command("funnel_gate", "on confirm", language="ru"))
    assert r.funnel_gate is True                        # раннер подхватил
    assert _gate_of(clients / "demo" / "settings.yaml") is True   # переживёт рестарт
    assert "вкл" in out.lower()


def test_off_is_immediate_no_confirmation(tmp_path):
    """Безопасное направление подтверждения не требует: выключить надо быстро."""
    clients = _clients(tmp_path)
    r = _runner(clients)
    _run(r.handle_config_command("funnel_gate", "on confirm", language="ru"))
    assert r.funnel_gate is True
    out = _run(r.handle_config_command("funnel_gate", "off", language="ru"))
    assert r.funnel_gate is False
    assert _gate_of(clients / "demo" / "settings.yaml") is False
    assert "выкл" in out.lower()


def test_garbage_arg_is_rejected(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    out = _run(r.handle_config_command("funnel_gate", "включи давай", language="ru"))
    assert r.funnel_gate is False
    assert "on" in out.lower() and "off" in out.lower()   # подсказали синтаксис


def test_config_command_still_reports_gate(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    _run(r.handle_config_command("funnel_gate", "on confirm", language="ru"))
    out = _run(r.handle_config_command("config", "", language="ru"))
    assert "ВКЛ" in out


@pytest.mark.parametrize("lang", ["ru", "en", "uk"])
def test_all_languages_have_strings(tmp_path, lang):
    r = _runner(_clients(tmp_path))
    for arg in ("", "on", "on confirm", "off", "мусор"):
        out = _run(r.handle_config_command("funnel_gate", arg, language=lang))
        assert out and "{" not in out          # шаблон не остался неподставленным
