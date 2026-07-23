"""Контракт JSON-плана: его читает PowerShell-супервизор, поэтому форма
фиксируется тестом, а не договорённостью."""
from __future__ import annotations

import json

from chatter.registry_cli import build_plan, session_available

REG = (
    "clients:\n"
    "  volska:\n"
    "    enabled: true\n"
    "    personas: [volska]\n"
    "    session: .secrets/demo.session\n"
    "    db: .secrets/demo.db\n"
    "  demo:\n"
    "    enabled: false\n"
    "    personas: [demo, demo2]\n"
    "    session: .secrets/demo.session\n"
    "    db: .secrets/demo.db\n"
)


def _plan(text=REG, *, session_available=lambda p: True, client_dir_exists=lambda s: True):
    return build_plan(text, root=r"C:\jarvis", session_available=session_available,
                      client_dir_exists=client_dir_exists)


# ── доступность сессии: .enc ИЛИ plaintext (контракт build_session) ─────────
#
# Ловушка, найденная фактом при первом живом прогоне CLI: после cutover P1/P2
# plaintext .session на диске НЕТ ВОВСЕ — в .secrets лежит только
# demo.session.enc. Наивное «файл по пути должен существовать» пометило бы
# ЖИВУЮ volska как invalid, и супервизор отказался бы её поднимать. То есть
# валидация, написанная ради защиты прода, стала бы способом его уронить.

def test_session_available_accepts_encrypted_only(tmp_path):
    """Сегодняшняя прод-реальность: только .enc."""
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "demo.session.enc").write_bytes(b"x")
    assert session_available(".secrets/demo.session", root=str(tmp_path)) is True


def test_session_available_accepts_legacy_plaintext(tmp_path):
    """До cutover и на откате сессия лежит plaintext — build_session её примет
    и мигрирует, значит клиент запускаем."""
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "demo.session").write_bytes(b"x")
    assert session_available(".secrets/demo.session", root=str(tmp_path)) is True


def test_session_available_false_when_neither_exists(tmp_path):
    assert session_available(".secrets/ghost.session", root=str(tmp_path)) is False


def test_enc_derivation_is_not_duplicated():
    """Правило '.enc' берём каноническое (secret_loader), а не повторяем
    строкой — иначе оно разъедется с build_session при первом изменении."""
    from chatter.security.secret_loader import derive_session_enc_path
    assert derive_session_enc_path(".secrets/demo.session") == ".secrets/demo.session.enc"


def test_plan_lists_every_client_including_disabled():
    """Супервизору нужен ПОЛНЫЙ список: выключенных надо не только не
    запускать, но и остановить, если они ещё живы."""
    plan = _plan()
    assert [c["slug"] for c in plan["clients"]] == ["volska", "demo"]


def test_runnable_client_carries_launch_arguments():
    (volska,) = [c for c in _plan()["clients"] if c["slug"] == "volska"]
    assert volska["desired"] == "enabled"
    assert volska["runnable"] is True
    assert volska["error"] is None
    assert volska["personas"] == ["volska"]
    assert volska["session"] == ".secrets/demo.session"
    assert volska["db"] == ".secrets/demo.db"


def test_disabled_client_is_not_runnable_and_has_no_error():
    (demo,) = [c for c in _plan()["clients"] if c["slug"] == "demo"]
    assert demo["desired"] == "disabled"
    assert demo["runnable"] is False
    assert demo["error"] is None


def test_conflict_surfaces_as_error_on_both():
    text = REG.replace("  demo:\n    enabled: false", "  demo:\n    enabled: true")
    plan = _plan(text)
    errs = {c["slug"]: c["error"] for c in plan["clients"]}
    assert errs["volska"] and "demo" in errs["volska"]
    assert errs["demo"] and "volska" in errs["demo"]
    assert all(c["runnable"] is False for c in plan["clients"])


def test_plan_is_json_serialisable():
    """PowerShell читает это через ConvertFrom-Json — никаких кортежей."""
    json.dumps(_plan())


def test_broken_registry_reports_fatal_not_crash():
    """Сломанный реестр не должен ронять гардиан: он обязан узнать причину и
    сказать её владельцу (DEV-18)."""
    plan = _plan("nope: 1\n")
    assert plan["clients"] == []
    assert "clients" in plan["fatal"]
