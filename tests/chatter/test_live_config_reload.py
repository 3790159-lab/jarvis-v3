"""Phase A: атомарный fail-safe reload конфига без рестарта раннера."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC_CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def _clients(tmp_path) -> Path:
    """Копия demo/demo2 БЕЗ control-блока (герметично, без контрол-бота)."""
    dst = tmp_path / "clients"
    shutil.copytree(SRC_CLIENTS, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "demo" / "settings.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == "control:"), len(lines))
    p.write_text("\n".join(lines[:idx]) + "\n", encoding="utf-8")
    return dst


def _client():
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return c


def _runner(clients):
    return build_runner(
        client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def test_reload_picks_up_changed_knowledge_without_restart():
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        runner = _runner(clients)
        assert "5000" in runner.personas["demo"].cfg.knowledge   # старая цена
        # клиент меняет цену
        kb = clients / "demo" / "knowledge.md"
        kb.write_text(kb.read_text(encoding="utf-8").replace("5000", "7777"), encoding="utf-8")

        ok, err = runner.reload_configs()

        assert ok is True and err is None
        assert "7777" in runner.personas["demo"].cfg.knowledge   # новая цена ЖИВЬЁМ
        assert "5000" not in runner.personas["demo"].cfg.knowledge
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_reload_preserves_store_state():
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        store = Store(":memory:")
        runner = build_runner(
            client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
            store=store, loop=asyncio.new_event_loop(), llm_mode="fake")
        store.get_or_create_contact("42:demo")
        store.mute("42:demo", source="human_takeover", now=1.0)   # живое состояние

        runner.reload_configs()

        # тот же Store, пауза цела (не потеряли живое состояние при reload)
        assert runner.primary_store() is store
        assert store.get_or_create_contact("42:demo")["paused"] == 1
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_broken_config_keeps_old_and_reports_file_and_reason():
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        runner = _runner(clients)
        old_knowledge = runner.personas["demo"].cfg.knowledge
        # клиент ломает settings.yaml (невалидный YAML)
        s = clients / "demo" / "settings.yaml"
        s.write_text("model: [unclosed\n  bracket: nope", encoding="utf-8")

        ok, err = runner.reload_configs()

        assert ok is False
        assert err and "settings.yaml" in err          # какой файл
        # Аня НЕ замолчала: старый конфиг жив
        assert runner.personas["demo"].cfg.knowledge == old_knowledge
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_reload_updates_gate_fields():
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        runner = _runner(clients)
        assert runner.funnel_gate is False
        s = clients / "demo" / "settings.yaml"
        txt = s.read_text(encoding="utf-8").replace(
            "  allowlist: [237616472]",
            "  allowlist: [237616472]\n  denylist: [666]\n  funnel_gate: true")
        s.write_text(txt, encoding="utf-8")

        ok, err = runner.reload_configs()

        assert ok is True
        assert runner.funnel_gate is True
        assert 666 in runner.denylist
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_startup_recovers_from_last_known_good_when_config_broken():
    # Снятие мины crash-loop: битый конфиг на старте → грузимся с последней
    # рабочей версии + помечаем recovery, а НЕ падаем в петлю гардиана.
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        _runner(clients)                       # первый билд → базовый снимок хорошего
        (clients / "demo" / "settings.yaml").write_text("model: [broken yaml", encoding="utf-8")
        r2 = build_runner(                     # НЕ должен упасть
            client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
            store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")
        assert r2._startup_recovery is not None          # помечено, что восстановились
        assert "5000" in r2.personas["demo"].cfg.knowledge  # рабочий конфиг загружен
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_startup_hard_fails_only_when_no_snapshot_exists():
    # Единственный случай hard-fail: первый запуск, конфиг битый, снимков нет.
    async def scenario(tmp_path):
        clients = _clients(tmp_path)
        (clients / "demo" / "settings.yaml").write_text("model: [broken", encoding="utf-8")
        from chatter.config.loader import ConfigError
        raised = False
        try:
            build_runner(
                client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
                store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")
        except ConfigError as e:
            raised = True
            assert "last-known-good" in str(e) or "snapshot" in str(e)
        assert raised
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))


def test_auto_reload_on_mtime_change():
    # §5: правка файла руками (mtime меняется) → перечитывание без команды.
    async def scenario(tmp_path):
        import os
        clients = _clients(tmp_path)
        runner = _runner(clients)
        assert runner.maybe_reload_on_change() is False   # ничего не менялось
        kb = clients / "demo" / "knowledge.md"
        kb.write_text(kb.read_text(encoding="utf-8").replace("5000", "4242"), encoding="utf-8")
        os.utime(kb, (10**10, 10**10))                    # заведомо новее (bump mtime)
        assert runner.maybe_reload_on_change() is True    # заметил и перечитал
        assert "4242" in runner.personas["demo"].cfg.knowledge
        assert runner.maybe_reload_on_change() is False   # повторно уже не перечитывает
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(scenario(Path(d)))
