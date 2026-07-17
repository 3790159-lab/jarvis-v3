"""Config-команды пульта: /config /reload /knowledge /rollback через раннер."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def _clients(tmp_path) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "demo" / "settings.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == "control:"), len(lines))
    p.write_text("\n".join(lines[:idx]) + "\n", encoding="utf-8")
    return dst


def _runner(clients):
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return build_runner(
        client=c, clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_config_shows_human_summary(tmp_path):
    r = _runner(_clients(tmp_path))
    out = _run(r.handle_config_command("config", "", language="ru"))
    assert "Аня" in out                 # имя персоны
    assert "ru" in out or "рус" in out.lower()  # язык
    assert "haiku" in out.lower()        # модель
    # что-то про базу знаний (кол-во)
    assert any(w in out.lower() for w in ("знани", "позиц", "раздел"))


def test_reload_good_reports_success(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    kb = clients / "demo" / "knowledge.md"
    kb.write_text(kb.read_text(encoding="utf-8").replace("5000", "7777"), encoding="utf-8")
    out = _run(r.handle_config_command("reload", "", language="ru"))
    assert "✅" in out or "обнов" in out.lower()
    assert "7777" in r.personas["demo"].cfg.knowledge


def test_reload_broken_keeps_old_and_reports_reason(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    old = r.personas["demo"].cfg.knowledge
    (clients / "demo" / "settings.yaml").write_text("model: [broken", encoding="utf-8")
    out = _run(r.handle_config_command("reload", "", language="ru"))
    assert "settings.yaml" in out                 # причина названа
    assert r.personas["demo"].cfg.knowledge == old  # старый жив


def test_knowledge_no_arg_shows_current(tmp_path):
    r = _runner(_clients(tmp_path))
    out = _run(r.handle_config_command("knowledge", "", language="ru"))
    assert "5000" in out                 # текущая база


def test_knowledge_with_text_replaces_and_reloads(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    out = _run(r.handle_config_command(
        "knowledge", "Консультация теперь 9999 руб.", language="ru"))
    assert "✅" in out or "обнов" in out.lower()
    # записано на диск И перечитано в память
    assert "9999" in (clients / "demo" / "knowledge.md").read_text(encoding="utf-8")
    assert "9999" in r.personas["demo"].cfg.knowledge


def test_rollback_restores_previous_knowledge(tmp_path):
    clients = _clients(tmp_path)
    r = _runner(clients)
    _run(r.handle_config_command("knowledge", "новая база 9999", language="ru"))
    assert "9999" in r.personas["demo"].cfg.knowledge
    out = _run(r.handle_config_command("rollback", "", language="ru"))
    assert "9999" not in r.personas["demo"].cfg.knowledge   # откатились
    assert "5000" in r.personas["demo"].cfg.knowledge       # к прежней
    assert "↩" in out or "откат" in out.lower() or "верн" in out.lower()
