# -*- coding: utf-8 -*-
"""Tests for the standalone daily state backup job (scripts/state_backup.py).

Mirrors tests/test_morning_digest_script.py.

🔴 УТВЕРЖДЕНИЕ «Every network/R2 seam is mocked» ЗДЕСЬ СТОЯЛО И БЫЛО НЕВЕРНЫМ.
Замер 04.09.2026: `main()` зовёт `sb.run_client_backup(_ROOT)`, которого ни
один тест не подменял, и каждый полный прогон суиты делал НАСТОЯЩУЮ заливку в
боевой бакет — перезаписывая `manifest.json` описанием из одной записи.
Заглушка живёт теперь в автоиспользуемой фикстуре ниже; утверждение
переписано так, чтобы оно называло МЕХАНИЗМ, а не обещание.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "state_backup.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("state_backup_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["state_backup_script"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _no_live_client_backup(monkeypatch):
    """Клиентская заливка НЕ должна исполняться из этих тестов.

    Замер 04.09.2026: `main()` зовёт `sb.run_client_backup(_ROOT)`, и ни один
    тест этого файла его не подменял — а `.env` в worktree ссылка на живой,
    значит ключи R2 боевые. Каждый полный прогон суиты заливал один объект и
    ПЕРЕЗАПИСЫВАЛ боевой `manifest.json` описанием из одной записи, оставляя
    клиентские базы неописанными.

    Тот же класс ошибки, что уже описан ниже про `rotate_old_backups`, только
    другой шов. Теперь его держит ещё и застава в `run_client_backup`
    (`LiveClientBackupBlocked`), но полагаться на неё здесь нельзя: тесты
    обязаны быть герметичными сами по себе, а застава — последний рубеж.

    Отказ по `ClientBackupRefused` выбран намеренно: это штатное «клиентского
    набора здесь нет», rc задачи оно не портит и сводку не ломает.
    """
    from app.services import state_backup as sb

    def _refuse(*_a, **_kw):
        raise sb.ClientBackupRefused(
            "тестовое окружение: клиентский набор не трогаем")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)


# ── send_telegram: shared test-isolation guard ───────────────────────────────


def test_send_telegram_blocked_under_test_isolation_no_network(monkeypatch, caplog):
    mod = _load_module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)

    def boom(*a, **k):
        raise AssertionError("network send attempted under test isolation")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with caplog.at_level("INFO"):
        ok = mod.send_telegram("backup text")

    assert ok is False
    assert any("suppressed under test isolation" in r.getMessage() for r in caplog.records)


def test_send_telegram_sends_when_isolation_opted_out(monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["url"] = req.full_url
        return _FakeResponse()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    ok = mod.send_telegram("backup text")

    assert ok is True
    assert "faketoken123" in captured["url"]


# ── main(): orchestration, everything mocked ─────────────────────────────────


def test_main_success_sends_summary_and_returns_zero(monkeypatch):
    mod = _load_module()
    from app.services import state_backup as sb

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"],
                              manifest_key="backups/state/2026-07-15/manifest.json",
                              total_bytes=100)
    monkeypatch.setattr(sb, "run_backup", lambda root: result)
    monkeypatch.setattr(sb, "rotate_all_backups", lambda: {
        "backups/state": ["backups/state/2026-06-01/users.json"],
        "backups/client": [],
    })

    captured = {}

    def fake_send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", fake_send)

    rc = mod.main([])

    assert rc == 0
    assert "2026-07-15" in captured["text"]
    assert "Ротация" in captured["text"]


def test_main_run_backup_raises_alerts_and_returns_one(monkeypatch):
    mod = _load_module()
    from app.services import state_backup as sb

    def _boom(root):
        raise RuntimeError("R2 unreachable")

    monkeypatch.setattr(sb, "run_backup", _boom)

    captured = {}

    def fake_send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", fake_send)

    rc = mod.main([])

    assert rc == 1
    assert "R2 unreachable" in captured["text"]


def test_main_partial_failure_returns_one_but_still_notifies(monkeypatch):
    mod = _load_module()
    from app.services import state_backup as sb

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"],
                              failed=[{"rel_path": "cost_tracking.json", "error": "boom"}])
    monkeypatch.setattr(sb, "run_backup", lambda root: result)
    monkeypatch.setattr(sb, "rotate_all_backups",
                         lambda: {"backups/state": [], "backups/client": []})

    captured = {}
    monkeypatch.setattr(mod, "send_telegram", lambda text: captured.setdefault("text", text) or True)

    rc = mod.main([])

    assert rc == 1
    assert "cost_tracking.json" in captured["text"]


def test_main_rotation_failure_is_non_fatal(monkeypatch):
    mod = _load_module()
    from app.services import state_backup as sb

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"])
    monkeypatch.setattr(sb, "run_backup", lambda root: result)

    def _boom_rotate():
        raise RuntimeError("list_objects failed")

    monkeypatch.setattr(sb, "rotate_all_backups", _boom_rotate)

    captured = {}
    monkeypatch.setattr(mod, "send_telegram", lambda text: captured.setdefault("text", text) or True)

    rc = mod.main([])

    assert rc == 0  # backup itself succeeded; rotation failure doesn't fail the run
    assert "2026-07-15" in captured["text"]
