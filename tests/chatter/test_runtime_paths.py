"""Онбординг-дырка №4: session/db выводятся из slug клиента.

Общий дефолт (.secrets/chatter_telethon.{session,db}) означал, что ВТОРОЙ
клиент, запущенный без флагов, молча садится на сессию и БД первого. Это
взрывается не на первом клиенте, а ровно в момент масштабирования.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.telethon_run import (
    LEGACY_DB_NAME,
    LEGACY_SESSION_NAME,
    derive_db_path,
    derive_session_path,
    migrate_legacy_runtime_files,
    resolve_runtime_paths,
)


def test_paths_derived_from_slug():
    assert Path(derive_session_path("acme")).name == "acme.session"
    assert Path(derive_db_path("acme")).name == "acme.db"


def test_two_clients_never_share_files():
    """Гвоздь дырки №4: без единого флага два клиента обязаны разъехаться
    по РАЗНЫМ файлам — и по сессии, и по БД."""
    a_session, a_db = resolve_runtime_paths(primary_slug="acme", env={})
    b_session, b_db = resolve_runtime_paths(primary_slug="beta", env={})
    assert a_session != b_session
    assert a_db != b_db
    # и ни один из них не садится на общий legacy-дефолт
    for p in (a_session, b_session, a_db, b_db):
        assert LEGACY_SESSION_NAME not in p and LEGACY_DB_NAME not in p


def test_explicit_flags_still_win():
    s, d = resolve_runtime_paths(
        primary_slug="acme", session_arg="/tmp/x.session", db_arg="/tmp/x.db", env={})
    assert s == "/tmp/x.session"
    assert d == "/tmp/x.db"


def test_env_override_still_wins_for_session():
    s, _ = resolve_runtime_paths(
        primary_slug="acme", env={"TELETHON_SESSION": "/tmp/env.session"})
    assert s == "/tmp/env.session"


def test_env_override_for_db():
    _, d = resolve_runtime_paths(primary_slug="acme", env={"CHATTER_DB": "/tmp/env.db"})
    assert d == "/tmp/env.db"


# --- миграция боевого клиента (демо-деплой уже живёт на общем дефолте) -----

def test_legacy_files_are_migrated_once(tmp_path):
    """Прод крутится на .secrets/chatter_telethon.session. Смена дефолта без
    переноса = разлогин живой Ани. Переносим один раз, автоматически."""
    legacy_s = tmp_path / LEGACY_SESSION_NAME
    legacy_d = tmp_path / LEGACY_DB_NAME
    legacy_s.write_text("session", encoding="utf-8")
    legacy_d.write_text("db", encoding="utf-8")
    new_s, new_d = tmp_path / "demo.session", tmp_path / "demo.db"

    moved = migrate_legacy_runtime_files(
        session_path=str(new_s), db_path=str(new_d), secrets_dir=tmp_path)

    assert new_s.read_text(encoding="utf-8") == "session"
    assert new_d.read_text(encoding="utf-8") == "db"
    assert not legacy_s.exists() and not legacy_d.exists()
    assert len(moved) == 2


def test_migration_never_clobbers_existing_target(tmp_path):
    """Если у клиента уже есть своя сессия — legacy НЕ должен её затереть."""
    legacy_s = tmp_path / LEGACY_SESSION_NAME
    legacy_s.write_text("legacy", encoding="utf-8")
    new_s = tmp_path / "demo.session"
    new_s.write_text("mine", encoding="utf-8")

    migrate_legacy_runtime_files(
        session_path=str(new_s), db_path=str(tmp_path / "demo.db"), secrets_dir=tmp_path)

    assert new_s.read_text(encoding="utf-8") == "mine"
    assert legacy_s.exists()          # legacy оставлен нетронутым, не удалён


def test_migration_is_noop_without_legacy(tmp_path):
    moved = migrate_legacy_runtime_files(
        session_path=str(tmp_path / "acme.session"),
        db_path=str(tmp_path / "acme.db"), secrets_dir=tmp_path)
    assert moved == []


def test_migration_skipped_for_non_legacy_target(tmp_path):
    """Второй клиент (acme) НЕ должен подхватить legacy-сессию первого:
    миграция допустима только в целевой путь ПЕРВИЧНОГО legacy-деплоя."""
    legacy_s = tmp_path / LEGACY_SESSION_NAME
    legacy_s.write_text("demo-session", encoding="utf-8")
    acme_s = tmp_path / "acme.session"

    migrate_legacy_runtime_files(
        session_path=str(acme_s), db_path=str(tmp_path / "acme.db"),
        secrets_dir=tmp_path, legacy_owner_slug="demo")

    assert not acme_s.exists()        # чужую сессию не забрал
    assert legacy_s.exists()


@pytest.mark.parametrize("slug", ["demo", "acme", "клиент-1"])
def test_derived_paths_live_under_secrets(slug):
    assert Path(derive_session_path(slug)).parent.name == ".secrets"
    assert Path(derive_db_path(slug)).parent.name == ".secrets"
