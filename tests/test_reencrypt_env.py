# -*- coding: utf-8 -*-
"""Перешифровка .env -> .env.enc: сторожа ОТ КОНТРАКТА.

Зачем скрипт вообще существует: `scripts/add_secret.ps1` пишет в PLAINTEXT
`.env`, а `bootstrap_env` при наличии `.env.enc` читает ТОЛЬКО `.enc` (тихих
plaintext-путей нет, P1P2 §2.1). Между двумя этими фактами — дыра, в которую
уже трижды падали руками, и один раз бандл секретов чуть не уехал со СТАРЫМ
env: `.env` был свежий, `.env.enc` четырёхдневный, а экспорт берёт `.enc`.

Поэтому проверяемых свойств два, и второе важнее первого:
  1. перешифровка честная (сверка РАСШИФРОВАННЫМ, не байтами);
  2. рассинхрон `.env` / `.env.enc` виден ДО того, как кто-то снимет бандл.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI — Windows-only")

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "reencrypt_env", REPO_ROOT / "scripts" / "reencrypt_env.py")
re_env = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(re_env)

sys.path.insert(0, str(REPO_ROOT))
from chatter.security.crypto import (  # noqa: E402
    decrypt_from_file, encrypt_to_file, generate_entropy)


@pytest.fixture
def env(tmp_path):
    """Изолированный корень со СВОЕЙ entropy: настоящую .secrets/entropy.bin
    тест не трогает даже на чтение."""
    ent = tmp_path / "entropy.bin"
    generate_entropy(ent)
    (tmp_path / ".env").write_bytes(b"A=1\nB=2\n")
    return tmp_path, ent


# ── 1. честность перешифровки ─────────────────────────────────────────────

def test_reencrypt_produces_enc_that_decrypts_back_to_env(env):
    root, ent = env
    re_env.reencrypt(root, entropy_path=ent)
    assert decrypt_from_file(root / ".env.enc", entropy_path=ent) == \
        (root / ".env").read_bytes()


def test_verification_cannot_be_byte_comparison_of_enc(env):
    """Пин на ПРИЧИНУ, а не на поведение: DPAPI недетерминирован, две
    шифровки одних байт дают РАЗНЫЕ блобы. Сверка байтами .enc была бы
    вечным ложным красным — грабля уже стоила разбора."""
    root, ent = env
    data = b"SAME=bytes\n"
    a, b = root / "a.enc", root / "b.enc"
    encrypt_to_file(a, data, entropy_path=ent)
    encrypt_to_file(b, data, entropy_path=ent)
    assert a.read_bytes() != b.read_bytes(), "DPAPI вдруг стал детерминирован"
    assert decrypt_from_file(a, entropy_path=ent) == \
        decrypt_from_file(b, entropy_path=ent)


def test_existing_enc_is_backed_up_with_its_OLD_content(env):
    root, ent = env
    encrypt_to_file(root / ".env.enc", b"OLD=1\n", entropy_path=ent)
    re_env.reencrypt(root, entropy_path=ent)
    baks = list(root.glob(".env.enc.pre-regen-*.bak"))
    assert len(baks) == 1, f"ожидался ровно один бэкап, найдено: {baks}"
    assert decrypt_from_file(baks[0], entropy_path=ent) == b"OLD=1\n"


def test_unreadable_existing_enc_stops_before_overwrite(env):
    """Нерасшифровываемый .enc — это АВАРИЯ, а не повод перезаписать.
    Перезаписав, мы уничтожим единственную улику того, что пошло не так."""
    root, ent = env
    (root / ".env.enc").write_bytes(b"not a dpapi blob at all")
    before = (root / ".env.enc").read_bytes()
    with pytest.raises(re_env.ReencryptError):
        re_env.reencrypt(root, entropy_path=ent)
    assert (root / ".env.enc").read_bytes() == before, ".enc перезаписан после отказа"


def test_missing_plaintext_env_is_a_loud_refusal(env):
    root, ent = env
    (root / ".env").unlink()
    with pytest.raises(re_env.ReencryptError):
        re_env.reencrypt(root, entropy_path=ent)
    assert not (root / ".env.enc").exists()


def test_report_never_contains_secret_values(env):
    root, ent = env
    (root / ".env").write_bytes(b"TOKEN=super-secret-value-12345\n")
    report = re_env.reencrypt(root, entropy_path=ent)
    blob = "\n".join(report)
    assert "super-secret-value-12345" not in blob
    assert "TOKEN" in blob, "имя ключа называть НАДО — иначе отчёт бесполезен"


# ── 2. рассинхрон виден ДО бандла (ради чего скрипт и заведён) ────────────

def test_in_sync_after_reencrypt(env):
    root, ent = env
    re_env.reencrypt(root, entropy_path=ent)
    assert re_env.enc_status(root, entropy_path=ent) == "in_sync"


def test_env_edited_after_enc_is_STALE(env):
    """Тот самый случай: add_secret.ps1 дописал ключ в .env, .enc остался
    прежним, и экспорт бандла взял бы СТАРЫЙ env."""
    root, ent = env
    re_env.reencrypt(root, entropy_path=ent)
    (root / ".env").write_bytes(b"A=1\nB=2\nNEW=3\n")
    assert re_env.enc_status(root, entropy_path=ent) == "stale"


def test_stale_is_decided_by_CONTENT_not_by_mtime(env):
    """mtime врёт: копирование, git, антивирус и синхронизация двигают его,
    не меняя содержимого. Свежий mtime при тех же байтах — это in_sync.

    ⚠️ Расхождение времени задаётся ЯВНО через os.utime. Прежняя редакция
    просто перезаписывала файл теми же байтами и полагалась на то, что
    отметки разойдутся сами, — они укладывались в один тик, mtime-реализация
    проходила тест, и сторож был ПУСТОЙ. Поймано мутационной проверкой."""
    import os
    root, ent = env
    re_env.reencrypt(root, entropy_path=ent)
    p, enc = root / ".env", root / ".env.enc"
    same_bytes = p.read_bytes()
    newer = enc.stat().st_mtime + 3600
    os.utime(p, (newer, newer))            # ЗАВЕДОМО новее, байты те же
    assert p.stat().st_mtime > enc.stat().st_mtime, "расхождение не создалось"
    assert p.read_bytes() == same_bytes
    assert re_env.enc_status(root, entropy_path=ent) == "in_sync"


def test_missing_enc_is_its_own_status_not_stale(env):
    root, ent = env
    assert re_env.enc_status(root, entropy_path=ent) == "no_enc"


def test_unreadable_enc_status_is_loud(env):
    root, ent = env
    (root / ".env.enc").write_bytes(b"garbage")
    assert re_env.enc_status(root, entropy_path=ent) == "unreadable"


# ── 3. entropy берётся от --root, а не от текущего каталога ───────────────

def test_entropy_is_resolved_against_root_not_cwd(tmp_path, monkeypatch):
    """РЕГРЕССИЯ первого живого запуска обёртки.

    crypto.DEFAULT_ENTROPY_PATH — путь ОТНОСИТЕЛЬНО cwd ('.secrets/entropy.bin').
    Для раннера это верно (гардиан пускает с -WorkingDirectory C:\\jarvis), для
    утилиты, запускаемой откуда угодно, — нет: entropy не находится, и
    ИСПРАВНЫЙ .env.enc докладывается как 'unreadable', то есть здоровье
    читается как авария. Обёртка так и сделала на первом же прогоне."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    (tmp_path / ".secrets").mkdir()
    generate_entropy(tmp_path / ".secrets" / "entropy.bin")
    (tmp_path / ".env").write_bytes(b"A=1\n")

    monkeypatch.chdir(tmp_path)
    re_env.main(["--root", str(tmp_path)])          # создаёт .enc
    monkeypatch.chdir(tmp_path.parent)              # cwd УЕХАЛ

    assert re_env.main(["--root", str(tmp_path), "--check"]) == 0, (
        "из чужого каталога статус исправного .env.enc должен остаться in_sync")


def test_explicit_entropy_env_wins_over_root(tmp_path, monkeypatch):
    """Гардиан и тесты пинят entropy явно — их выбор сильнее вывода из root."""
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(tmp_path / "custom.bin"))
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "entropy.bin").write_bytes(b"x" * 32)
    assert re_env.default_entropy_for(tmp_path) == tmp_path / "custom.bin"


def test_no_entropy_under_root_defers_to_crypto(tmp_path, monkeypatch):
    """Нет своего мнения — не выдумываем путь, пусть решает crypto."""
    monkeypatch.delenv("JARVIS_ENTROPY_FILE", raising=False)
    assert re_env.default_entropy_for(tmp_path) is None
