"""P1/P2 §12: процедура восстановления — 🔴 блокер приёмки.

DPAPI привязан к учётке+машине → смерть диска/профиля без экспорта = потеря
всех сессий. Экспорт НАМЕРЕННО не через DPAPI (циркулярность): пароль
владельца → scrypt → AES-GCM, формат с версией. Хранение вне машины.

Живой слой (§12 п.1/п.3): collect_secrets (сбор материала с root в
machine-независимом виде), restore_secrets (чистый root → machine-scope
.enc локальным DPAPI, plaintext на диск не ложится), CLI export/restore
(пароль только через getpass, не argv), приёмка «чистый профиль →
восстановили → раннер поднялся» (bootstrap_env + сессия читаются).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chatter.security.recovery import (
    BUNDLE_MAGIC,
    RecoveryError,
    collect_secrets,
    export_bundle,
    export_bundle_to_file,
    import_bundle,
    import_bundle_from_file,
    main as recovery_main,
    restore_secrets,
)

SECRETS = {
    "volska.session": b"1ApWapzMBu4-fake-string-session",
    ".env": "API_KEY=s3cret\nUNICODE=значение\n".encode("utf-8"),
}


def test_round_trip():
    blob = export_bundle(SECRETS, "owner-password")
    assert import_bundle(blob, "owner-password") == SECRETS


def test_bundle_is_ciphertext():
    """Смысл §12: бандл хранится вне машины (телефон/облако) — plaintext
    в нём недопустим даже фрагментом."""
    blob = export_bundle(SECRETS, "owner-password")
    assert b"1ApWapzMBu4" not in blob
    assert b"s3cret" not in blob
    assert "volska".encode() not in blob  # имена файлов тоже секрет не выдают


def test_wrong_password_is_explicit_error():
    blob = export_bundle(SECRETS, "owner-password")
    with pytest.raises(RecoveryError, match="пароль|повреж"):
        import_bundle(blob, "wrong-password")


def test_tampered_bundle_raises():
    blob = bytearray(export_bundle(SECRETS, "pw"))
    blob[-1] ^= 0xFF
    with pytest.raises(RecoveryError):
        import_bundle(bytes(blob), "pw")


def test_not_a_bundle_raises_clear_error():
    with pytest.raises(RecoveryError, match="магическ|формат"):
        import_bundle(b"random junk that is not a bundle", "pw")


def test_unknown_version_raises_with_version():
    """Формат с версией: бандл из будущей версии → явное «нужна новая
    версия инструмента», не InvalidTag-мусор."""
    blob = bytearray(export_bundle(SECRETS, "pw"))
    blob[len(BUNDLE_MAGIC)] = 99  # байт версии сразу после магии
    with pytest.raises(RecoveryError, match="верси|99"):
        import_bundle(bytes(blob), "pw")


def test_two_exports_differ():
    """Соль/nonce свежие на каждый экспорт — одинаковый вход не даёт
    одинаковый шифртекст (иначе утечка через сравнение бэкапов)."""
    assert export_bundle(SECRETS, "pw") != export_bundle(SECRETS, "pw")


def test_empty_password_rejected():
    with pytest.raises(RecoveryError, match="парол"):
        export_bundle(SECRETS, "")


def test_file_round_trip(tmp_path):
    p = tmp_path / "jarvis_secrets_backup.jrvbak"
    export_bundle_to_file(p, SECRETS, "owner-password")
    assert import_bundle_from_file(p, "owner-password") == SECRETS


def test_import_missing_file_raises_with_path(tmp_path):
    with pytest.raises(RecoveryError, match="nope.jrvbak"):
        import_bundle_from_file(tmp_path / "nope.jrvbak", "pw")


# ---------------------------------------------------------------------------
# Живой слой §12: сбор с root / restore на чистый root / CLI / приёмка.
# DPAPI есть только на Windows — как в test_security_migrate.
# ---------------------------------------------------------------------------

win_only = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


def _fake_converter(path: str) -> str:
    return "string-session-from-" + Path(path).name


def _plaintext_root(tmp_path: Path, name: str = "repo") -> Path:
    """Root ДО cutover: plaintext .env + legacy SQLite-сессия."""
    root = tmp_path / name
    (root / ".secrets").mkdir(parents=True)
    (root / ".env").write_text("API_KEY=s3cret\nTELEGRAM_API_ID=1\n",
                               encoding="utf-8")
    (root / ".secrets" / "demo.session").write_bytes(b"sqlite-bytes")
    return root


@win_only
def test_collect_from_plaintext_root(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _plaintext_root(tmp_path)
    secrets = collect_secrets(root, slugs=["demo"],
                              session_to_string=_fake_converter)
    assert secrets[".env"] == (root / ".env").read_bytes()
    assert secrets["demo.session"] == \
        b"string-session-from-demo.session"
    assert "entropy.bin" not in secrets  # entropy ещё нет — нечего включать


@win_only
def test_collect_prefers_enc_and_includes_entropy(tmp_path, monkeypatch):
    """Post-cutover root: правда живёт в .enc (plaintext мог устареть или
    быть шреднут), entropy.bin включается в экспорт (спека §6 п.4)."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.crypto import encrypt_to_file, generate_entropy
    from chatter.security.secret_loader import save_string_session
    root = _plaintext_root(tmp_path)
    entropy = root / ".secrets" / "entropy.bin"
    generate_entropy(entropy)
    encrypt_to_file(root / ".env.enc", b"API_KEY=fresh\n",
                    entropy_path=entropy)
    save_string_session(root / ".secrets" / "demo.session.enc",
                        "fresh-string-session", entropy_path=entropy)
    (root / ".env").write_text("API_KEY=stale\n", encoding="utf-8")

    secrets = collect_secrets(root, slugs=["demo"],
                              session_to_string=_fake_converter)

    assert secrets[".env"] == b"API_KEY=fresh\n"
    assert secrets["demo.session"] == b"fresh-string-session"
    assert secrets["entropy.bin"] == entropy.read_bytes()


@win_only
def test_collect_empty_root_is_explicit_error(tmp_path, monkeypatch):
    """Экспорт «ничего» — это потерянный бэкап, не тихий успех (DEV-18)."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(RecoveryError, match="нечего|нет"):
        collect_secrets(root, slugs=["demo"],
                        session_to_string=_fake_converter)


@win_only
def test_restore_on_clean_root(tmp_path, monkeypatch):
    """Чистый root: entropy из бандла, .env.enc/.session.enc создаются
    локальным machine-scope DPAPI с round-trip-верификацией, plaintext
    на диск НЕ ложится, ACL на весь материал, отчёт без значений."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.crypto import decrypt_from_file
    from chatter.security.secret_loader import load_string_session
    bundle = {
        ".env": b"API_KEY=s3cret\n",
        "demo.session": b"restored-string-session",
        "entropy.bin": b"e" * 32,
    }
    root = tmp_path / "clean"
    acl_calls: list[str] = []

    report = restore_secrets(bundle, root, slugs=["demo"],
                             acl=lambda p: acl_calls.append(str(Path(p))))

    entropy = root / ".secrets" / "entropy.bin"
    assert entropy.read_bytes() == b"e" * 32
    assert decrypt_from_file(root / ".env.enc", entropy_path=entropy) == \
        b"API_KEY=s3cret\n"
    assert load_string_session(root / ".secrets" / "demo.session.enc",
                               entropy_path=entropy) == \
        "restored-string-session"
    assert not (root / ".env").exists()  # plaintext на диск не ложится
    assert not (root / ".secrets" / "demo.session").exists()
    for target in (root / ".secrets", entropy, root / ".env.enc",
                   root / ".secrets" / "demo.session.enc"):
        assert str(target) in acl_calls, f"нет ACL на {target}"
    joined = "\n".join(report)
    assert "s3cret" not in joined and "restored-string-session" not in joined


@win_only
def test_restore_refuses_non_clean_root(tmp_path, monkeypatch):
    """Restore поверх живых секретов = случайное затирание прода — явный
    отказ, а не перезапись."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.crypto import generate_entropy
    bundle = {".env": b"API_KEY=x\n"}
    root = tmp_path / "occupied"
    (root / ".secrets").mkdir(parents=True)
    generate_entropy(root / ".secrets" / "entropy.bin")
    with pytest.raises(RecoveryError, match="чист|существ|занят"):
        restore_secrets(bundle, root, slugs=["demo"], acl=lambda p: None)


@win_only
def test_restore_without_entropy_in_bundle_generates_fresh(
        tmp_path, monkeypatch):
    """Старый бандл без entropy: на новой машине machine-ключ всё равно
    другой — свежие 32Б, восстановление не блокируется."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.crypto import decrypt_from_file
    bundle = {".env": b"API_KEY=x\n"}
    root = tmp_path / "clean"
    restore_secrets(bundle, root, slugs=["demo"], acl=lambda p: None)
    entropy = root / ".secrets" / "entropy.bin"
    assert entropy.exists() and entropy.stat().st_size == 32
    assert decrypt_from_file(root / ".env.enc", entropy_path=entropy) == \
        b"API_KEY=x\n"


@win_only
def test_acceptance_clean_profile_runner_rises(tmp_path, monkeypatch):
    """§12 п.3 приёмка: старый root → export → ЧИСТЫЙ root → restore →
    «раннер поднялся» = bootstrap_env кладёт секреты в environ и
    StringSession читается (тот же путь, которым telethon_run стартует)."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.secret_loader import (
        bootstrap_env, load_string_session,
    )
    old = _plaintext_root(tmp_path, "old-machine")
    bundle_file = tmp_path / "backup.jrvbak"
    export_bundle_to_file(
        bundle_file,
        collect_secrets(old, slugs=["demo"], session_to_string=_fake_converter),
        "owner-password")

    clean = tmp_path / "clean-profile"
    restore_secrets(import_bundle_from_file(bundle_file, "owner-password"),
                    clean, slugs=["demo"], acl=lambda p: None)

    entropy = clean / ".secrets" / "entropy.bin"
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(entropy))
    environ: dict[str, str] = {"JARVIS_ENTROPY_FILE": str(entropy)}
    values = bootstrap_env(clean / ".env", environ=environ)
    assert environ["API_KEY"] == "s3cret"
    assert values["TELEGRAM_API_ID"] == "1"
    assert load_string_session(clean / ".secrets" / "demo.session.enc",
                               entropy_path=entropy) == \
        "string-session-from-demo.session"


@win_only
def test_cli_export_password_prompted_twice_and_mismatch_fails(
        tmp_path, capsys, monkeypatch):
    """Пароль ТОЛЬКО через getpass (argv светится в process list/истории);
    export спрашивает дважды, расхождение = явный отказ без файла."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _plaintext_root(tmp_path)
    out = tmp_path / "b.jrvbak"
    answers = iter(["pw-one", "pw-two"])
    rc = recovery_main(
        ["export", "--root", str(root), "--slug", "demo",
         "--out", str(out)],
        ask_password=lambda prompt: next(answers),
        session_to_string=_fake_converter)
    assert rc == 1
    assert not out.exists()
    assert "совпад" in capsys.readouterr().err


@win_only
def test_cli_export_then_restore_round_trip(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _plaintext_root(tmp_path)
    out = tmp_path / "b.jrvbak"
    rc = recovery_main(
        ["export", "--root", str(root), "--slug", "demo",
         "--out", str(out)],
        ask_password=lambda prompt: "owner-password",
        session_to_string=_fake_converter)
    assert rc == 0 and out.exists()

    clean = tmp_path / "clean"
    rc = recovery_main(
        ["restore", "--root", str(clean), "--slug", "demo",
         "--bundle", str(out)],
        ask_password=lambda prompt: "owner-password",
        acl=lambda p: None)
    assert rc == 0
    assert (clean / ".env.enc").exists()
    assert (clean / ".secrets" / "demo.session.enc").exists()
    captured = capsys.readouterr()
    assert "s3cret" not in captured.out  # отчёт без значений секретов
    assert "owner-password" not in captured.out


@win_only
def test_cli_restore_wrong_password_fails_clean(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _plaintext_root(tmp_path)
    out = tmp_path / "b.jrvbak"
    recovery_main(
        ["export", "--root", str(root), "--slug", "demo",
         "--out", str(out)],
        ask_password=lambda prompt: "owner-password",
        session_to_string=_fake_converter)
    clean = tmp_path / "clean"
    rc = recovery_main(
        ["restore", "--root", str(clean), "--slug", "demo",
         "--bundle", str(out)],
        ask_password=lambda prompt: "wrong",
        acl=lambda p: None)
    assert rc == 1
    assert not (clean / ".env.enc").exists()
    assert "парол" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Несколько живых сессий в одном root (тестовый лид Э1 + сессия клиента).
# Односессионный экспорт молча терял вторую сессию, а обход «два бандла»
# не восстанавливался: второй restore упирался в защиту чистого root.
# ---------------------------------------------------------------------------


def _two_session_root(tmp_path: Path, name: str = "repo") -> Path:
    """Post-cutover root с ДВУМЯ сессиями: клиентская + тестовый лид."""
    from chatter.security.crypto import encrypt_to_file, generate_entropy
    from chatter.security.secret_loader import save_string_session
    root = tmp_path / name
    (root / ".secrets").mkdir(parents=True)
    entropy = root / ".secrets" / "entropy.bin"
    generate_entropy(entropy)
    encrypt_to_file(root / ".env.enc", b"TELEGRAM_API_ID=1\n",
                    entropy_path=entropy)
    save_string_session(root / ".secrets" / "demo.session.enc",
                        "string-demo", entropy_path=entropy)
    save_string_session(root / ".secrets" / "drill_lead.session.enc",
                        "string-lead", entropy_path=entropy)
    return root


@win_only
def test_collect_discovers_every_session_in_secrets(tmp_path, monkeypatch):
    """Без явного --slug собираются ВСЕ сессии root'а. Молчаливая потеря
    второй сессии = потерянный бэкап, который выглядит здоровым."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _two_session_root(tmp_path)

    secrets = collect_secrets(root)

    assert secrets["demo.session"] == b"string-demo"
    assert secrets["drill_lead.session"] == b"string-lead"
    assert secrets["entropy.bin"] == \
        (root / ".secrets" / "entropy.bin").read_bytes()


@win_only
def test_collect_explicit_slugs_limit_selection(tmp_path, monkeypatch):
    """Явный слаг остаётся точным управлением: берём только его."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _two_session_root(tmp_path)

    secrets = collect_secrets(root, slugs=["drill_lead"])

    assert secrets["drill_lead.session"] == b"string-lead"
    assert "demo.session" not in secrets


@win_only
def test_collect_discovers_plaintext_session_too(tmp_path, monkeypatch):
    """До cutover сессия лежит plaintext — авто-обнаружение обязано видеть
    и её, иначе бэкап тихо пропустит незашифрованного клиента."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _plaintext_root(tmp_path)

    secrets = collect_secrets(root, session_to_string=_fake_converter)

    assert secrets["demo.session"] == b"string-session-from-demo.session"


@win_only
def test_restore_writes_every_session_from_bundle(tmp_path, monkeypatch):
    """Бандл с двумя сессиями раскатывается ЗА ОДИН restore — обход «два
    бандла подряд» невозможен: второй упирается в защиту чистого root."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.secret_loader import load_string_session
    bundle = {
        ".env": b"TELEGRAM_API_ID=1\n",
        "demo.session": b"string-demo",
        "drill_lead.session": b"string-lead",
        "entropy.bin": b"e" * 32,
    }
    root = tmp_path / "clean"

    restore_secrets(bundle, root, acl=lambda p: None)

    entropy = root / ".secrets" / "entropy.bin"
    assert load_string_session(root / ".secrets" / "demo.session.enc",
                               entropy_path=entropy) == "string-demo"
    assert load_string_session(root / ".secrets" / "drill_lead.session.enc",
                               entropy_path=entropy) == "string-lead"
    assert not (root / ".secrets" / "drill_lead.session").exists()


@win_only
def test_restore_clean_check_covers_every_bundle_slug(tmp_path, monkeypatch):
    """Клин-чек обязан смотреть на слаги БАНДЛА, а не на один дефолтный:
    иначе restore тихо затирает живую сессию, которой нет среди дефолтных."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.crypto import generate_entropy
    from chatter.security.secret_loader import save_string_session
    root = tmp_path / "occupied"
    (root / ".secrets").mkdir(parents=True)
    entropy = root / ".secrets" / "entropy.bin"
    generate_entropy(entropy)
    live = root / ".secrets" / "drill_lead.session.enc"
    save_string_session(live, "ЖИВАЯ-сессия-лида", entropy_path=entropy)
    # Единственный занятый путь — САМА сессия лида: иначе страж сработал бы
    # на entropy/.env и тест зеленел бы, не проверяя ничего про слаги.
    entropy.unlink()
    before = live.read_bytes()
    bundle = {"drill_lead.session": b"string-from-bundle"}

    with pytest.raises(RecoveryError, match="чист|существ|занят"):
        restore_secrets(bundle, root, acl=lambda p: None)

    assert live.read_bytes() == before, "живая сессия затёрта restore'ом"


@win_only
def test_cli_export_without_slug_takes_all_sessions(tmp_path, monkeypatch):
    """Дефолт CLI = все сессии: забыть флаг нельзя так, чтобы это молча
    стоило сессии."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _two_session_root(tmp_path)
    out = tmp_path / "b.jrvbak"

    rc = recovery_main(["export", "--root", str(root), "--out", str(out)],
                       ask_password=lambda prompt: "owner-password")

    assert rc == 0
    bundle = import_bundle_from_file(out, "owner-password")
    assert bundle["demo.session"] == b"string-demo"
    assert bundle["drill_lead.session"] == b"string-lead"


@win_only
def test_cli_export_slug_flag_repeats(tmp_path, monkeypatch):
    """--slug повторяемый: явный выбор подмножества остаётся возможным."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    root = _two_session_root(tmp_path)
    out = tmp_path / "b.jrvbak"

    rc = recovery_main(
        ["export", "--root", str(root), "--slug", "drill_lead",
         "--out", str(out)],
        ask_password=lambda prompt: "owner-password")

    assert rc == 0
    bundle = import_bundle_from_file(out, "owner-password")
    assert "demo.session" not in bundle


@win_only
def test_old_single_session_bundle_still_restores(tmp_path, monkeypatch):
    """Совместимость: бандлы, снятые до этой правки, восстанавливаются как
    раньше — формат и версия не менялись, ключи в нём уже именные."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    from chatter.security.secret_loader import load_string_session
    old_bundle = {
        ".env": b"API_KEY=s3cret\n",
        "demo.session": b"restored-string-session",
        "entropy.bin": b"e" * 32,
    }
    root = tmp_path / "clean"

    restore_secrets(old_bundle, root, acl=lambda p: None)

    entropy = root / ".secrets" / "entropy.bin"
    assert load_string_session(root / ".secrets" / "demo.session.enc",
                               entropy_path=entropy) == \
        "restored-string-session"
