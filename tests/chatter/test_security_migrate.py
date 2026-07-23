"""P1/P2, задача 3 слоя процесса: одноразовый мигратор секретов.

`python -m chatter.security.migrate` = setup-шаги cutover §6 п.4-6 одним
инструментом: entropy (32Б, без перезаписи) → ACL SYSTEM+Admins → .env →
.env.enc → сессия → .session.enc, с верификацией обратной расшифровки.
Plaintext НИКОГДА не удаляется (бэкап отката до cutover п.9, шред руками).
Повторный запуск = no-op (ничего не перешифровывает, entropy не трогает).

User-scope блобов на диске нет (cutover не было) — миграция всегда
plaintext → machine-scope, отдельного user→machine пути не существует.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chatter.security.crypto import decrypt_from_file
from chatter.security.migrate import MigrationError, run_migration
from chatter.security.secret_loader import load_string_session

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


def _fake_converter(path: str) -> str:
    return "string-session-from-" + Path(path).name


def _setup_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".secrets").mkdir(parents=True)
    (root / ".env").write_text("API_KEY=v\nTELEGRAM_API_ID=1\n",
                               encoding="utf-8")
    (root / ".secrets" / "demo.session").write_bytes(b"sqlite-bytes")
    return root


def test_migration_full_flow(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _setup_root(tmp_path)
    acl_calls: list[str] = []

    report = run_migration(root, slug="demo",
                           acl=lambda p: acl_calls.append(str(Path(p))),
                           session_to_string=_fake_converter)

    entropy = root / ".secrets" / "entropy.bin"
    assert entropy.exists() and entropy.stat().st_size == 32
    # .env → .env.enc, расшифровка байт-в-байт, plaintext остался
    env_enc = root / ".env.enc"
    assert decrypt_from_file(env_enc, entropy_path=entropy) == \
        (root / ".env").read_bytes()
    assert (root / ".env").exists()
    # сессия → .enc, round-trip в ту же строку, plaintext остался
    sess_enc = root / ".secrets" / "demo.session.enc"
    assert load_string_session(sess_enc, entropy_path=entropy) == \
        "string-session-from-demo.session"
    assert (root / ".secrets" / "demo.session").exists()
    # ACL: каталог секретов, entropy, оба .enc
    for target in (root / ".secrets", entropy, env_enc, sess_enc):
        assert str(target) in acl_calls, f"нет ACL на {target}"
    # отчёт человекочитаемый и без значений секретов
    joined = "\n".join(report)
    assert "entropy" in joined and ".env.enc" in joined
    assert "v" != joined  # никаких значений секретов в отчёте
    assert "API_KEY=v" not in joined


def test_migration_second_run_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _setup_root(tmp_path)
    run_migration(root, slug="demo", acl=lambda p: None,
                  session_to_string=_fake_converter)
    entropy_before = (root / ".secrets" / "entropy.bin").read_bytes()
    env_enc_before = (root / ".env.enc").read_bytes()
    sess_enc_before = (root / ".secrets" / "demo.session.enc").read_bytes()

    def exploding_converter(path):
        raise AssertionError("повторная миграция сессии не должна запускаться")

    run_migration(root, slug="demo", acl=lambda p: None,
                  session_to_string=exploding_converter)

    assert (root / ".secrets" / "entropy.bin").read_bytes() == entropy_before
    assert (root / ".env.enc").read_bytes() == env_enc_before
    assert (root / ".secrets" / "demo.session.enc").read_bytes() == \
        sess_enc_before


def test_migration_nothing_to_migrate_still_sets_up_entropy_and_acl(
        tmp_path, monkeypatch):
    """Чистая машина без .env и сессии: entropy+ACL ставятся (setup), а
    отсутствие материалов — факт отчёта, не ошибка."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = tmp_path / "repo"
    (root / ".secrets").mkdir(parents=True)
    acl_calls: list[str] = []
    report = run_migration(root, slug="demo",
                           acl=lambda p: acl_calls.append(str(Path(p))),
                           session_to_string=_fake_converter)
    assert (root / ".secrets" / "entropy.bin").exists()
    assert str(root / ".secrets") in acl_calls
    assert not (root / ".env.enc").exists()
    joined = "\n".join(report).lower()
    assert "нет" in joined or "skip" in joined


def test_migration_empty_session_string_is_error(tmp_path, monkeypatch):
    """SQLite без auth_key → пустая строка → явная ошибка, пустой .enc
    не создаётся (DEV-18)."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _setup_root(tmp_path)
    with pytest.raises(MigrationError, match="auth"):
        run_migration(root, slug="demo", acl=lambda p: None,
                      session_to_string=lambda p: "")
    assert not (root / ".secrets" / "demo.session.enc").exists()


def test_migration_respects_entropy_env_override(tmp_path, monkeypatch):
    """JARVIS_ENTROPY_FILE задан → entropy живёт там (прецедент crypto),
    а не в root/.secrets."""
    override = tmp_path / "elsewhere" / "entropy.bin"
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(override))
    root = _setup_root(tmp_path)
    run_migration(root, slug="demo", acl=lambda p: None,
                  session_to_string=_fake_converter)
    assert override.exists()
    assert not (root / ".secrets" / "entropy.bin").exists()
    assert decrypt_from_file(root / ".env.enc", entropy_path=override) == \
        (root / ".env").read_bytes()
