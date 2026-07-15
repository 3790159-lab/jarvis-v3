# -*- coding: utf-8 -*-
"""``/backup_now``/``/backup_status`` bot wiring (DEV-16). $0, mocks only, no
real R2/network — mirrors the pattern in tests/test_garbage_cleanup_wiring.py.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_backup_now_is_admin_only_not_friend():
    assert "/backup_now" not in mod.FRIEND_ALLOWED_COMMANDS


def test_backup_status_is_admin_only_not_friend():
    assert "/backup_status" not in mod.FRIEND_ALLOWED_COMMANDS


def test_backup_now_is_not_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/backup_now") is False


def test_backup_status_is_not_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/backup_status") is False


# ── _backup_now_dispatch ──────────────────────────────────────────────────


def test_backup_now_dispatch_runs_backup_and_rotation_and_sends_summary(monkeypatch):
    from app.services import state_backup as sb

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"],
                              manifest_key="backups/state/2026-07-15/manifest.json",
                              total_bytes=42)
    calls = {"root": None, "rotated": False}

    def _fake_run_backup(root):
        calls["root"] = root
        return result

    def _fake_rotate():
        calls["rotated"] = True
        return ["backups/state/2026-06-01/users.json"]

    monkeypatch.setattr(sb, "run_backup", _fake_run_backup)
    monkeypatch.setattr(sb, "rotate_old_backups", _fake_rotate)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._backup_now_dispatch(ADMIN)

    assert calls["root"] is not None
    assert calls["rotated"] is True
    assert len(sent) == 1
    assert "2026-07-15" in sent[0]
    assert "Ротация" in sent[0]


def test_backup_now_dispatch_run_backup_failure_is_honest_not_crash(monkeypatch):
    from app.services import state_backup as sb

    def _boom(root):
        raise RuntimeError("R2 config missing")

    monkeypatch.setattr(sb, "run_backup", _boom)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._backup_now_dispatch(ADMIN)

    assert len(sent) == 1
    assert "R2 config missing" in sent[0]
    assert "🚫" in sent[0]


def test_backup_now_dispatch_rotation_failure_is_non_fatal(monkeypatch):
    from app.services import state_backup as sb

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"])
    monkeypatch.setattr(sb, "run_backup", lambda root: result)

    def _boom_rotate():
        raise RuntimeError("list_objects failed")

    monkeypatch.setattr(sb, "rotate_old_backups", _boom_rotate)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._backup_now_dispatch(ADMIN)

    assert len(sent) == 1
    assert "2026-07-15" in sent[0]        # backup summary still sent


# ── _backup_status_dispatch ───────────────────────────────────────────────


def test_backup_status_dispatch_sends_formatted_status(monkeypatch):
    from app.services import state_backup as sb
    monkeypatch.setattr(sb, "list_backup_dates",
                         lambda: ["2026-07-01", "2026-07-14", "2026-07-15"])
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._backup_status_dispatch(ADMIN)

    assert len(sent) == 1
    assert "2026-07-15" in sent[0]
    assert "2026-07-01" in sent[0]


def test_backup_status_dispatch_failure_is_honest_not_crash(monkeypatch):
    from app.services import state_backup as sb

    def _boom():
        raise RuntimeError("R2 unreachable")

    monkeypatch.setattr(sb, "list_backup_dates", _boom)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._backup_status_dispatch(ADMIN)

    assert len(sent) == 1
    assert "R2 unreachable" in sent[0]
    assert "🚫" in sent[0]


# ── handle_command routing ────────────────────────────────────────────────


def test_backup_now_command_dispatches_via_handle_command(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_backup_now_dispatch", lambda cid: calls.append(cid))
    mod.handle_command(ADMIN, "/backup_now", "", {})
    assert calls == [ADMIN]


def test_backup_status_command_dispatches_via_handle_command(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_backup_status_dispatch", lambda cid: calls.append(cid))
    mod.handle_command(ADMIN, "/backup_status", "", {})
    assert calls == [ADMIN]
