from __future__ import annotations
from pathlib import Path

import pytest

from chatter.telethon_login import (
    CredentialsError, load_api_credentials, make_client, resolve_session_path,
)


# ---------------------------------------------------------------------------
# load_api_credentials -- env wins over .env, .env is a fallback, missing is
# a clear error. Pure function: no real filesystem I/O beyond a tmp_path
# fixture, no network, no interactive Telethon anywhere near this test.
# ---------------------------------------------------------------------------
def test_env_wins_over_dotenv_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_API_ID=999999\nTELEGRAM_API_HASH=fromfile\n", encoding="utf-8")
    env = {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "fromenv"}

    api_id, api_hash = load_api_credentials(env, env_file)

    assert (api_id, api_hash) == ("12345", "fromenv")


def test_dotenv_fallback_used_when_env_is_empty(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nTELEGRAM_API_ID=\"12345\"\nTELEGRAM_API_HASH='fromfile'\r\n",
        encoding="utf-8",
    )

    api_id, api_hash = load_api_credentials({}, env_file)

    assert (api_id, api_hash) == ("12345", "fromfile")


def test_env_partial_falls_back_to_dotenv_for_the_missing_key(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_API_ID=11111\nTELEGRAM_API_HASH=hashfromfile\n", encoding="utf-8")
    env = {"TELEGRAM_API_HASH": "hashfromenv"}  # only HASH set in the real env

    api_id, api_hash = load_api_credentials(env, env_file)

    assert (api_id, api_hash) == ("11111", "hashfromenv")


def test_missing_everywhere_raises_clear_error(tmp_path: Path):
    env_file = tmp_path / "does-not-exist.env"

    with pytest.raises(CredentialsError) as exc_info:
        load_api_credentials({}, env_file)

    msg = str(exc_info.value)
    assert "TELEGRAM_API_ID" in msg
    assert "TELEGRAM_API_HASH" in msg


def test_missing_dotenv_file_is_not_itself_an_error_if_env_has_everything(tmp_path: Path):
    env_file = tmp_path / "does-not-exist.env"
    env = {"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}

    api_id, api_hash = load_api_credentials(env, env_file)

    assert (api_id, api_hash) == ("1", "h")


# ---------------------------------------------------------------------------
# resolve_session_path
# ---------------------------------------------------------------------------
def test_resolve_session_path_env_override():
    assert resolve_session_path({"TELETHON_SESSION": "custom.session"}) == "custom.session"


def test_resolve_session_path_default_matches_telethon_run_default():
    # Must match chatter/telethon_run.py's own default so a login done with
    # defaults and a run done with defaults talk to the same session file.
    from chatter.telethon_run import main as _unused  # noqa: F401  (sanity: module importable)
    assert resolve_session_path({}) == str(Path(".secrets") / "chatter_telethon.session")


# ---------------------------------------------------------------------------
# make_client -- constructed with the right args. NO real TelegramClient.
# ---------------------------------------------------------------------------
def test_make_client_constructs_with_session_path_int_api_id_and_hash():
    calls = []

    class FakeClient:
        def __init__(self, session_path, api_id, api_hash):
            calls.append((session_path, api_id, api_hash))

    result = make_client(".secrets/chatter_telethon.session", "12345", "deadbeef", client_cls=FakeClient)

    assert calls == [(".secrets/chatter_telethon.session", 12345, "deadbeef")]
    assert isinstance(result, FakeClient)
