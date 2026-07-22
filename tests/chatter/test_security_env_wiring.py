"""P1/P2 рев. 2, слой процесса (§2.1): точка входа грузит .env.enc В ПАМЯТЬ.

`bootstrap_env` — единая стартовая точка процессов Jarvis (O1: общий, не
chatter-only): .enc → environ; legacy plaintext → одноразовая авто-миграция
(как build_session для сессий, спека §4.5); plaintext-фолбэк — ТОЛЬКО по
явному opt-in `JARVIS_ALLOW_PLAINTEXT_ENV=1` и с громким TG-алертом.
Тихих plaintext-путей не остаётся: и ANTHROPIC-ключ, и .env-фолбэк
load_api_credentials после успешного .enc — закрыты (нет молчаливого
чтения plaintext, когда рядом лежит бэкап до шреда).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chatter.security.crypto import decrypt_from_file, encrypt_to_file
from chatter.security.secret_loader import (
    SecretLoaderError, bootstrap_env,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


def _no_alert(url, payload):  # алерт в этих тестах не ожидается
    raise AssertionError("alert must not fire on this path")


# --- .enc есть → секреты в память, plaintext не читается --------------------

def test_bootstrap_loads_enc_into_environ(tmp_path):
    env_file = tmp_path / ".env"
    encrypt_to_file(str(env_file) + ".enc", b"API_KEY=enc-value\n")
    environ: dict[str, str] = {}
    loaded = bootstrap_env(env_file, environ=environ,
                           alert_transport=_no_alert)
    assert environ["API_KEY"] == "enc-value"
    assert loaded == {"API_KEY": "enc-value"}


def test_bootstrap_prefers_enc_over_plaintext_backup(tmp_path):
    """Plaintext-бэкап (до шреда, cutover п.9) лежит рядом — НЕ читается."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=stale-plain\n", encoding="utf-8")
    encrypt_to_file(str(env_file) + ".enc", b"API_KEY=enc-value\n")
    environ: dict[str, str] = {}
    bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ["API_KEY"] == "enc-value"


def test_bootstrap_enc_without_entropy_is_explicit_error(tmp_path, monkeypatch):
    """Есть .enc, нет entropy → явная ошибка старта с ремедиацией,
    не тихий фолбэк на plaintext (спека §4.6)."""
    env_file = tmp_path / ".env"
    encrypt_to_file(str(env_file) + ".enc", b"API_KEY=x\n")
    env_file.write_text("API_KEY=plain\n", encoding="utf-8")  # соблазн рядом
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(tmp_path / "missing.bin"))
    environ: dict[str, str] = {}
    with pytest.raises(SecretLoaderError, match="entropy"):
        bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ == {}  # plaintext НЕ подхвачен


# --- legacy plaintext, .enc нет → одноразовая авто-миграция (§4.5) ----------

def test_bootstrap_automigrates_plaintext_to_enc(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=migrate-me\n", encoding="utf-8")
    environ: dict[str, str] = {}
    bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ["API_KEY"] == "migrate-me"
    # .enc создан, расшифровывается в исходник; plaintext остался бэкапом
    enc = Path(str(env_file) + ".enc")
    assert decrypt_from_file(enc) == env_file.read_bytes()  # байт-в-байт
    assert env_file.exists()


def test_bootstrap_second_run_uses_enc_not_remigration(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=v1\n", encoding="utf-8")
    bootstrap_env(env_file, environ={}, alert_transport=_no_alert)
    # оператор правит plaintext ПОСЛЕ миграции — истина теперь .enc,
    # расхождение не подхватывается молча
    env_file.write_text("API_KEY=v2-divergent\n", encoding="utf-8")
    environ: dict[str, str] = {}
    bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ["API_KEY"] == "v1"


def test_bootstrap_automigrate_without_entropy_is_explicit_error(tmp_path,
                                                                 monkeypatch):
    """Мигрировать без entropy нельзя → явная ошибка с подсказкой про
    генерацию (миграционный шаг cutover §6 п.4), НЕ тихий plaintext."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=plain\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(tmp_path / "missing.bin"))
    environ: dict[str, str] = {}
    with pytest.raises(SecretLoaderError, match="entropy"):
        bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ == {}


# --- plaintext-фолбэк: только явный opt-in + громкий алерт (задача 5) -------

def test_bootstrap_optin_uses_plaintext_and_alerts(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=plain\nTELEGRAM_BOT_TOKEN=tok\n",
                        encoding="utf-8")
    sent: list[tuple[str, bytes]] = []
    environ = {"JARVIS_ALLOW_PLAINTEXT_ENV": "1"}
    bootstrap_env(env_file, environ=environ,
                  alert_transport=lambda url, p: sent.append((url, p)))
    assert environ["API_KEY"] == "plain"
    assert len(sent) == 1                       # каждый старт кричит
    # opt-in = осознанный plaintext-режим: .enc сам не создаётся
    assert not Path(str(env_file) + ".enc").exists()


def test_bootstrap_optin_still_prefers_enc(tmp_path):
    """Opt-in выставлен, но .enc уже есть → работаем с .enc, без алерта."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=plain\n", encoding="utf-8")
    encrypt_to_file(str(env_file) + ".enc", b"API_KEY=enc\n")
    environ = {"JARVIS_ALLOW_PLAINTEXT_ENV": "1"}
    bootstrap_env(env_file, environ=environ, alert_transport=_no_alert)
    assert environ["API_KEY"] == "enc"


# --- файлов нет вовсе → no-op, работа от реального environ ------------------

def test_bootstrap_no_files_is_noop(tmp_path):
    """Нет ни .enc, ни .env — не ошибка: ключи могут жить в реальном
    environ (прецедент load_api_credentials: missing .env = нет фолбэка)."""
    environ = {"API_KEY": "from-real-env"}
    loaded = bootstrap_env(tmp_path / ".env", environ=environ,
                           alert_transport=_no_alert)
    assert loaded == {}
    assert environ == {"API_KEY": "from-real-env"}


# --- wiring в entrypoints: раннер/логин стартуют через bootstrap_env --------

def test_load_api_credentials_none_skips_file_fallback():
    """После успешного bootstrap из .enc файловый .env-фолбэк ОТКЛЮЧАЕТСЯ
    (env_file_path=None): неполный .enc при лежащем рядом plaintext-бэкапе
    даёт явный CredentialsError, а не тихое чтение plaintext."""
    from chatter.telethon_login import CredentialsError, load_api_credentials
    env = {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "abc"}
    assert load_api_credentials(env, None) == ("123", "abc")
    with pytest.raises(CredentialsError, match="TELEGRAM_API_HASH"):
        load_api_credentials({"TELEGRAM_API_ID": "123"}, None)


def test_resolve_control_token_no_silent_plaintext_read(tmp_path, monkeypatch):
    """Токен контрол-бота — только из environ (заполнен bootstrap_env в
    main); тихое чтение plaintext .env в рантайме убрано."""
    import chatter.telethon_run as tr
    env_file = tmp_path / ".env"
    env_file.write_text("CTRL_TOKEN=from-plain-file\n", encoding="utf-8")
    monkeypatch.setattr(tr, "DEFAULT_ENV_FILE", env_file)
    monkeypatch.delenv("CTRL_TOKEN", raising=False)
    assert tr._resolve_control_token("CTRL_TOKEN") is None
    monkeypatch.setenv("CTRL_TOKEN", "from-environ")
    assert tr._resolve_control_token("CTRL_TOKEN") == "from-environ"


def test_telethon_run_main_fails_loudly_on_broken_secret_layer(
        tmp_path, monkeypatch, capsys):
    """Раннер: .env.enc есть, entropy нет → ЯВНАЯ ошибка старта про
    секрет-слой (не «missing TELEGRAM_API_ID», не тихий plaintext)."""
    import chatter.telethon_run as tr
    env_file = tmp_path / ".env"
    encrypt_to_file(str(env_file) + ".enc", b"TELEGRAM_API_ID=1\n")
    env_file.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\n",
                        encoding="utf-8")  # соблазн тихого plaintext рядом
    monkeypatch.setattr(tr, "DEFAULT_ENV_FILE", env_file)
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(tmp_path / "missing.bin"))
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    assert tr.main([]) == 1
    err = capsys.readouterr().err
    assert "entropy" in err


def test_telethon_login_main_fails_loudly_on_broken_secret_layer(
        tmp_path, monkeypatch, capsys):
    """Логин — та же стартовая точка: битый секрет-слой = явный отказ."""
    import chatter.telethon_login as tl
    env_file = tmp_path / ".env"
    encrypt_to_file(str(env_file) + ".enc", b"TELEGRAM_API_ID=1\n")
    monkeypatch.setattr(tl, "DEFAULT_ENV_FILE", env_file)
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(tmp_path / "missing.bin"))
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    assert tl.main([]) == 1
    err = capsys.readouterr().err
    assert "entropy" in err
