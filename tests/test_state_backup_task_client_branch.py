# -*- coding: utf-8 -*-
"""Обвязка клиентского набора в ЕЖЕДНЕВНОЙ задаче (`scripts/state_backup.py`).

Сама `run_client_backup` покрыта в `test_client_backup_upload.py`. Здесь — шов
между ней и задачей, который до 22.08 не пинил НИКТО: приёмка показала
поведение замером, а замер живёт один вечер, сторож — всегда.

Что обязана различать задача (DEV-46 §3, докстринг `scripts/state_backup.py`):

* **ключа нет — состояние НАСТРОЙКИ, а не авария.** Задача не падает, `rc` не
  портит, но и не молчит: отказ называется ОТДЕЛЬНОЙ строкой сводки. Ронять
  задачу каждую ночь, пока владелец не завёл пару, — значит приучить не
  смотреть на её алерты;
* **ключ есть, а заливка упала — ошибка.** И в сводку, и в `rc`.

Склейка этих двух исходов в один и есть дефект, ради которого файл написан:
«не отправлено» и «упало» лечатся по-разному, а `rc` — то, что видит
планировщик.

Наружу тесты не ходят: `run_backup`, ротация и телеграм подменены; настоящей
остаётся ровно ветка клиента.
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


@pytest.fixture()
def task(monkeypatch):
    """Задача с подменённым окружением: наружу не ходит, ветка клиента живая."""
    mod = _load_module()
    from app.services import state_backup as sb

    state = sb.BackupResult(date="2026-08-22", uploaded=["users.json"],
                            verified=["users.json"], total_bytes=100)
    monkeypatch.setattr(sb, "run_backup", lambda root: state)
    monkeypatch.setattr(sb, "rotate_all_backups",
                        lambda: {"backups/state": [], "backups/client": []})
    sent: list[str] = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: sent.append(text) or True)
    return mod, sb, sent


def _client_ok(sb):
    return sb.BackupResult(date="2026-08-22", uploaded=["a1b2/db"],
                           verified=["a1b2/db"], total_bytes=2048)


# ── задача вообще ЗОВЁТ клиентский набор ────────────────────────────────────


def test_the_daily_task_calls_the_client_backup_at_all(task, monkeypatch):
    """Молчаливое «не позвали» неотличимо от «нечего слать» — кроме как здесь.

    Клиентский набор не попадает ни в одну строку сводки, когда его не
    позвали: сводка про `state` выглядит ровно как обычно.
    """
    mod, sb, _sent = task
    calls = []
    monkeypatch.setattr(sb, "run_client_backup",
                        lambda root, **kw: calls.append(root) or _client_ok(sb))

    mod.main([])

    assert len(calls) == 1, "ежедневная задача не позвала run_client_backup"
    root = Path(calls[0])
    assert (root / "scripts" / "state_backup.py").exists(), (
        "клиентский набор позван не от корня дерева: %r" % (calls[0],))


# ── ключа нет: состояние настройки ──────────────────────────────────────────


def test_a_missing_key_keeps_rc_zero(task, monkeypatch):
    mod, sb, _sent = task

    def _refuse(root, **kw):
        raise sb.ClientBackupRefused("нет JARVIS_BACKUP_PUBLIC_KEY: не задана")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)

    assert mod.main([]) == 0, (
        "отказ по отсутствию ключа испортил rc — планировщик покажет аварию "
        "там, где владелец ещё просто не завёл пару")


def test_a_missing_key_is_named_on_its_own_line(task, monkeypatch):
    """Не молчать — вторая половина того же правила. Причина едет целиком."""
    mod, sb, sent = task

    def _refuse(root, **kw):
        raise sb.ClientBackupRefused("нет JARVIS_BACKUP_PUBLIC_KEY: не задана")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)
    mod.main([])

    assert sent, "сводка не отправлена вовсе"
    lines = [ln for ln in sent[0].splitlines() if "клиентский набор НЕ отправлен" in ln]
    assert len(lines) == 1, "отказ не назван отдельной строкой: %r" % (sent[0],)
    assert "нет JARVIS_BACKUP_PUBLIC_KEY" in lines[0], (
        "строка не несёт ПРИЧИНУ отказа: %r" % (lines[0],))


def test_a_refusal_is_not_reported_as_a_crash(task, monkeypatch):
    """«Не отправлено» и «упало» лечатся по-разному и обязаны читаться по-разному."""
    mod, sb, sent = task

    def _refuse(root, **kw):
        raise sb.ClientBackupRefused("нет JARVIS_BACKUP_PUBLIC_KEY: не задана")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)
    mod.main([])

    assert "УПАЛ" not in sent[0], "отказ прочитан как падение: %r" % (sent[0],)


def test_a_refusal_does_not_swallow_the_rotation_line(task, monkeypatch):
    """Отказ клиента не отменяет ротацию и не глушит её строку."""
    mod, sb, sent = task
    monkeypatch.setattr(sb, "rotate_all_backups", lambda: {
        "backups/state": ["backups/state/2026-08-01/users.json"],
        "backups/client": [],
    })

    def _refuse(root, **kw):
        raise sb.ClientBackupRefused("нет ключа")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)
    mod.main([])

    assert "Ротация" in sent[0], "строка ротации пропала за отказом клиента"


# ── ключ есть, а заливка упала: это авария ──────────────────────────────────


def test_a_failure_with_the_key_present_spoils_rc(task, monkeypatch):
    mod, sb, _sent = task

    def _boom(root, **kw):
        raise RuntimeError("R2 unreachable")

    monkeypatch.setattr(sb, "run_client_backup", _boom)

    assert mod.main([]) == 1, (
        "падение клиентской заливки не испортило rc — ночная авария осталась "
        "бы зелёной в планировщике")


def test_a_failure_with_the_key_present_is_named_in_the_summary(task, monkeypatch):
    mod, sb, sent = task

    def _boom(root, **kw):
        raise RuntimeError("R2 unreachable")

    monkeypatch.setattr(sb, "run_client_backup", _boom)
    mod.main([])

    assert "R2 unreachable" in sent[0], "падение не названо в сводке: %r" % (sent[0],)


def test_partial_client_failure_spoils_rc_too(task, monkeypatch):
    """`failed` внутри результата — тоже ошибка, а не «почти успех»."""
    mod, sb, _sent = task
    partial = sb.BackupResult(
        date="2026-08-22", uploaded=["a1b2/db"],
        failed=[{"rel_path": "a1b2/requisites", "error": "boom"}])
    monkeypatch.setattr(sb, "run_client_backup", lambda root, **kw: partial)

    assert mod.main([]) == 1


# ── успех называется своим именем ───────────────────────────────────────────


def test_a_successful_client_set_is_named_and_keeps_rc_zero(task, monkeypatch):
    mod, sb, sent = task
    monkeypatch.setattr(sb, "run_client_backup", lambda root, **kw: _client_ok(sb))

    rc = mod.main([])

    assert rc == 0
    assert "Клиентский набор за 2026-08-22" in sent[0], (
        "успешный клиентский набор не назван в сводке: %r" % (sent[0],))


# ── два исхода не склеиваются ───────────────────────────────────────────────


def test_a_client_refusal_does_not_hide_a_state_failure(task, monkeypatch):
    """Красное набора `state` остаётся красным, чем бы ни кончился клиентский."""
    mod, sb, _sent = task
    broken = sb.BackupResult(date="2026-08-22",
                             failed=[{"rel_path": "users.json", "error": "boom"}])
    monkeypatch.setattr(sb, "run_backup", lambda root: broken)

    def _refuse(root, **kw):
        raise sb.ClientBackupRefused("нет ключа")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)

    assert mod.main([]) == 1
