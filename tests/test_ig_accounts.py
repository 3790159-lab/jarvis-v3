# -*- coding: utf-8 -*-
"""Мульти-аккаунт IG credential store (``app.services.ig_accounts``).

Изолировано conftest'ным ``_isolate_ig_accounts_file`` (env ``IG_ACCOUNTS_FILE``
-> tmp_path) — ни один тест не трогает прод ``state/ig_accounts.json``. $0,
чистая логика (файл + env), ноль сети.
"""
from __future__ import annotations

import json

from app.services import ig_accounts as iga


# ---- resolve_credentials: bc-fallback / json lookup / fail-closed ---------


def test_resolve_credentials_no_json_no_env_returns_empty(monkeypatch):
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IG_USER_ID", raising=False)
    token, uid = iga.resolve_credentials()
    assert (token, uid) == ("", "")


def test_resolve_credentials_no_json_falls_back_to_legacy_env(monkeypatch, tmp_path):
    # Point at a file that doesn't exist and give it NO token to migrate,
    # so the json stays absent and the legacy env fallback path is exercised.
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "nope" / "ig_accounts.json"))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("IG_USER_ID", "17841400000000001")
    token, uid = iga.resolve_credentials()
    assert token == ""
    assert uid == "17841400000000001"


def test_resolve_credentials_two_accounts_returns_right_token(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    f.write_text(json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
        "vera_ai_ua": {"account_key": "vera_ai_ua", "ig_user_id": "222",
                       "username": "vera.ai.ua", "access_token": "TOK_VERA",
                       "token_refreshed_at": 2000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))

    assert iga.resolve_credentials("jtest_lab_") == ("TOK_JTEST", "111")
    assert iga.resolve_credentials("vera_ai_ua") == ("TOK_VERA", "222")


def test_resolve_credentials_default_key_from_env(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    f.write_text(json.dumps({"accounts": {
        "vera_ai_ua": {"account_key": "vera_ai_ua", "ig_user_id": "222",
                       "username": "vera.ai.ua", "access_token": "TOK_VERA",
                       "token_refreshed_at": 2000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.setenv("IG_DEFAULT_ACCOUNT", "vera_ai_ua")

    assert iga.resolve_credentials(None) == ("TOK_VERA", "222")


def test_resolve_credentials_default_key_falls_back_to_jtest_lab(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    f.write_text(json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.delenv("IG_DEFAULT_ACCOUNT", raising=False)

    assert iga.resolve_credentials(None) == ("TOK_JTEST", "111")


def test_resolve_credentials_unknown_key_raises_fail_closed(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    f.write_text(json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))

    try:
        iga.resolve_credentials("does_not_exist")
        assert False, "should raise"
    except iga.IGAccountError as exc:
        assert "does_not_exist" in str(exc)
        assert "jtest_lab_" in str(exc)


# ---- auto-migration on first touch -----------------------------------------


def test_migration_creates_json_from_legacy_env(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.setenv("IG_ACCESS_TOKEN", "LEGACY_TOKEN")
    monkeypatch.setenv("IG_USER_ID", "17841400000000009")

    assert not f.exists()
    token, uid = iga.resolve_credentials()

    assert (token, uid) == ("LEGACY_TOKEN", "17841400000000009")
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["accounts"]["jtest_lab_"]["access_token"] == "LEGACY_TOKEN"
    assert data["accounts"]["jtest_lab_"]["ig_user_id"] == "17841400000000009"


def test_migration_is_idempotent_second_call_reads_json_not_env(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.setenv("IG_ACCESS_TOKEN", "LEGACY_TOKEN")
    monkeypatch.setenv("IG_USER_ID", "111")

    iga.resolve_credentials()                     # migrates
    monkeypatch.setenv("IG_ACCESS_TOKEN", "CHANGED_ENV_TOKEN_IGNORED")
    token, uid = iga.resolve_credentials()         # must read json now, not env

    assert token == "LEGACY_TOKEN"


def test_migration_skipped_when_no_legacy_token(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)

    iga.resolve_credentials()
    assert not f.exists()


# ---- save_account / get_account / list_accounts ----------------------------


def test_save_and_get_account_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    iga.save_account("vera_ai_ua", ig_user_id="222", username="vera.ai.ua",
                     access_token="TOK", token_refreshed_at=123.0)
    acct = iga.get_account("vera_ai_ua")
    assert acct == {
        "account_key": "vera_ai_ua", "ig_user_id": "222", "username": "vera.ai.ua",
        "access_token": "TOK", "token_refreshed_at": 123.0,
    }


def test_save_account_does_not_clobber_other_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    iga.save_account("jtest_lab_", access_token="A")
    iga.save_account("vera_ai_ua", access_token="B")
    accounts = iga.list_accounts()
    assert set(accounts) == {"jtest_lab_", "vera_ai_ua"}


def test_list_accounts_missing_file_is_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "nope.json"))
    assert iga.list_accounts() == {}


def test_get_account_unknown_key_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "nope.json"))
    assert iga.get_account("whatever") is None


def test_load_corrupt_json_is_treated_as_empty(tmp_path, monkeypatch):
    f = tmp_path / "ig_accounts.json"
    f.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    assert iga.list_accounts() == {}


# ---- default_account_key ----------------------------------------------------


def test_default_account_key_uses_env(monkeypatch):
    monkeypatch.setenv("IG_DEFAULT_ACCOUNT", "vera_ai_ua")
    assert iga.default_account_key() == "vera_ai_ua"


def test_default_account_key_falls_back_to_jtest_lab(monkeypatch):
    monkeypatch.delenv("IG_DEFAULT_ACCOUNT", raising=False)
    assert iga.default_account_key() == "jtest_lab_"


# ---- parse_account_arg -------------------------------------------------------


def test_parse_account_arg_extracts_prefix():
    assert iga.parse_account_arg("@vera_ai_ua тема поста") == ("vera_ai_ua", "тема поста")


def test_parse_account_arg_no_prefix_returns_none_and_full_query():
    assert iga.parse_account_arg("тема поста") == (None, "тема поста")


def test_parse_account_arg_empty_query():
    assert iga.parse_account_arg("") == (None, "")
    assert iga.parse_account_arg(None) == (None, "")


def test_parse_account_arg_prefix_only_no_rest():
    assert iga.parse_account_arg("@jtest_lab_") == ("jtest_lab_", "")
