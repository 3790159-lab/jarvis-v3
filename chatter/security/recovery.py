"""Экспорт/импорт бэкапа секретов под паролем владельца (P1/P2 §12).

🔴 Блокер приёмки P1/P2: DPAPI привязан к учётке+машине, поэтому смерть
диска/профиля без внешнего бэкапа = потеря всех сессий клиентов. Экспорт
НАМЕРЕННО не через DPAPI (циркулярность — бэкап должен открываться на
ЧУЖОЙ машине): пароль владельца → scrypt → AES-256-GCM.

Формат (версионирован):
    MAGIC(7) | version(1) | salt(16) | nonce(12) | AESGCM(payload)
payload = JSON {имя: base64(bytes)}; magic+version идут в AAD — подмена
заголовка рвёт тег. Правило процесса: экспорт обязателен после подключения
КАЖДОГО нового клиента, хранение вне машины (ONBOARDING_MANUAL).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chatter.security.crypto import (
    ENTROPY_SIZE, CryptoError, decrypt_from_file, encrypt_to_file,
    generate_entropy, restrict_to_system_admins,
)
from chatter.security.secret_loader import (
    SecretLoaderError, load_string_session, save_string_session,
)

BUNDLE_MAGIC = b"JRVBAK\x00"
BUNDLE_VERSION = 1

_SALT_LEN = 16
_NONCE_LEN = 12
# Параметры scrypt: интерактивный профиль (~100мс), защита от офлайн-брута
# бэкапа, утёкшего с телефона/облака владельца.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1


class RecoveryError(Exception):
    """Экспорт/импорт бэкапа не удался. Явно, не тихо (DEV-18)."""


def _derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise RecoveryError("пустой пароль недопустим для бэкапа секретов")
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024,
        dklen=32)


def _header(version: int) -> bytes:
    return BUNDLE_MAGIC + bytes([version])


def export_bundle(secrets: dict[str, bytes], password: str) -> bytes:
    payload = json.dumps(
        {name: base64.b64encode(data).decode("ascii")
         for name, data in secrets.items()},
        ensure_ascii=False).encode("utf-8")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    header = _header(BUNDLE_VERSION)
    ct = AESGCM(_derive_key(password, salt)).encrypt(nonce, payload, header)
    return header + salt + nonce + ct


def import_bundle(blob: bytes, password: str) -> dict[str, bytes]:
    if not blob.startswith(BUNDLE_MAGIC):
        raise RecoveryError(
            "нет магического префикса JRVBAK — это не бэкап секретов "
            "(битый файл? чужой формат?)")
    version_off = len(BUNDLE_MAGIC)
    version = blob[version_off]
    if version != BUNDLE_VERSION:
        raise RecoveryError(
            f"бэкап версии {version}, поддерживается {BUNDLE_VERSION} — "
            "нужна соответствующая версия инструмента восстановления")
    body = blob[version_off + 1:]
    if len(body) < _SALT_LEN + _NONCE_LEN + 16:  # 16 = GCM-тег
        raise RecoveryError("бэкап обрезан — файл повреждён")
    salt = body[:_SALT_LEN]
    nonce = body[_SALT_LEN:_SALT_LEN + _NONCE_LEN]
    ct = body[_SALT_LEN + _NONCE_LEN:]
    try:
        payload = AESGCM(_derive_key(password, salt)).decrypt(
            nonce, ct, _header(version))
    except InvalidTag as exc:
        raise RecoveryError(
            "расшифровка бэкапа не удалась: неверный пароль или файл "
            "повреждён") from exc
    parsed = json.loads(payload.decode("utf-8"))
    return {name: base64.b64decode(b64) for name, b64 in parsed.items()}


def export_bundle_to_file(path: str | Path, secrets: dict[str, bytes],
                          password: str) -> None:
    Path(path).write_bytes(export_bundle(secrets, password))


def import_bundle_from_file(path: str | Path,
                            password: str) -> dict[str, bytes]:
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise RecoveryError(f"не удалось прочитать бэкап {p}: {exc}") from exc
    return import_bundle(blob, password)


# ---------------------------------------------------------------------------
# Живой слой §12: сбор материала с root / restore на чистый root / CLI.
# ---------------------------------------------------------------------------

# Ключи в бандле — machine-независимый plaintext-вид материала:
#   ".env"           — байты env-файла;
#   "<slug>.session" — StringSession-строка (utf-8);
#   "entropy.bin"    — optionalEntropy DPAPI (спека §6 п.4: в экспорт).
_ENTROPY_KEY = "entropy.bin"
_ENV_KEY = ".env"


def _resolve_entropy(root: Path, environ: Mapping[str, str]) -> Path:
    """Тот же резолв, что migrate: env-override или root/.secrets."""
    override = environ.get("JARVIS_ENTROPY_FILE")
    return Path(override) if override else root / ".secrets" / "entropy.bin"


def _sqlite_session_to_string(path: str) -> str:
    """Legacy SQLite-сессия → StringSession (офлайн; тот же дубль, что в
    migrate — импорт telethon_run тащит весь раннер)."""
    from telethon.sessions import SQLiteSession, StringSession
    return StringSession.save(SQLiteSession(path))


def collect_secrets(
    root: str | Path,
    *,
    slug: str = "demo",
    session_to_string: Callable[[str], str] | None = None,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, bytes]:
    """Собирает секреты root'а для экспорта. Правда живёт в `.enc`
    (post-cutover plaintext мог устареть/быть шреднут) — plaintext лишь
    фолбэк до cutover. Пустой сбор = потерянный бэкап, явная ошибка."""
    root = Path(root)
    entropy = _resolve_entropy(root, environ)
    secrets: dict[str, bytes] = {}

    env_enc, env_plain = root / ".env.enc", root / ".env"
    try:
        if env_enc.exists():
            secrets[_ENV_KEY] = decrypt_from_file(env_enc,
                                                  entropy_path=entropy)
        elif env_plain.exists():
            secrets[_ENV_KEY] = env_plain.read_bytes()

        sess_enc = root / ".secrets" / f"{slug}.session.enc"
        sess_plain = root / ".secrets" / f"{slug}.session"
        if sess_enc.exists():
            string = load_string_session(sess_enc, entropy_path=entropy)
        elif sess_plain.exists():
            string = (session_to_string or _sqlite_session_to_string)(
                str(sess_plain))
            if not string:
                raise RecoveryError(
                    f"{sess_plain} без auth_key — это не залогиненная "
                    "сессия, в бэкап не годится")
        else:
            string = None
        if string is not None:
            secrets[f"{slug}.session"] = string.encode("utf-8")
    except (CryptoError, SecretLoaderError) as exc:
        raise RecoveryError(f"сбор секретов {root}: {exc}") from exc

    if entropy.exists():
        secrets[_ENTROPY_KEY] = entropy.read_bytes()

    if _ENV_KEY not in secrets and f"{slug}.session" not in secrets:
        raise RecoveryError(
            f"в {root} нечего экспортировать (нет ни .env/.env.enc, ни "
            f".secrets/{slug}.session[.enc]) — пустой бэкап это потерянный "
            "бэкап")
    return secrets


def restore_secrets(
    bundle: Mapping[str, bytes],
    root: str | Path,
    *,
    slug: str = "demo",
    acl: Callable[[Path], None] = restrict_to_system_admins,
    environ: Mapping[str, str] = os.environ,
) -> list[str]:
    """Бандл → ЧИСТЫЙ root: entropy из бандла (иначе свежая), machine-scope
    `.enc` локальным DPAPI с round-trip-верификацией. Plaintext-секреты на
    диск не ложатся. Поверх живых секретов — явный отказ, не перезапись.
    Возвращает отчёт (строки без значений секретов)."""
    root = Path(root)
    secrets_dir = root / ".secrets"
    entropy = _resolve_entropy(root, environ)
    env_enc = root / ".env.enc"
    sess_enc = secrets_dir / f"{slug}.session.enc"
    report: list[str] = []

    occupied = [p for p in (entropy, env_enc, root / ".env", sess_enc,
                            secrets_dir / f"{slug}.session") if p.exists()]
    if occupied:
        raise RecoveryError(
            "root не чист — уже существуют: "
            + ", ".join(str(p) for p in occupied)
            + ". Restore не перезаписывает живые секреты (защита прода); "
            "восстанавливать только на чистый профиль/каталог")

    secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw_entropy = bundle.get(_ENTROPY_KEY)
        if raw_entropy is not None:
            if len(raw_entropy) != ENTROPY_SIZE:
                raise RecoveryError(
                    f"entropy в бандле {len(raw_entropy)}Б вместо "
                    f"{ENTROPY_SIZE}Б — бандл повреждён")
            entropy.parent.mkdir(parents=True, exist_ok=True)
            entropy.write_bytes(raw_entropy)
            report.append(f"entropy {entropy}: восстановлена из бандла")
        else:
            generate_entropy(entropy)
            report.append(f"entropy {entropy}: в бандле нет — сгенерирована "
                          "свежая (machine-ключ новой машины всё равно свой)")

        acl_targets = [secrets_dir, entropy]

        if _ENV_KEY in bundle:
            encrypt_to_file(env_enc, bundle[_ENV_KEY], entropy_path=entropy)
            if decrypt_from_file(env_enc, entropy_path=entropy) != \
                    bundle[_ENV_KEY]:
                raise RecoveryError(
                    f"{env_enc}: верификация не сошлась — .enc не "
                    "расшифровывается в материал бандла, НЕ использовать")
            report.append(f"{env_enc}: создан, верифицирован "
                          "(plaintext на диск не писался)")
            acl_targets.append(env_enc)
        else:
            report.append(".env: в бандле нет — пропуск")

        sess_key = f"{slug}.session"
        if sess_key in bundle:
            string = bundle[sess_key].decode("utf-8")
            save_string_session(sess_enc, string, entropy_path=entropy)
            if load_string_session(sess_enc, entropy_path=entropy) != string:
                raise RecoveryError(
                    f"{sess_enc}: верификация не сошлась — .enc не "
                    "расшифровывается в исходную строку, НЕ использовать")
            report.append(f"{sess_enc}: создан, верифицирован")
            acl_targets.append(sess_enc)
        else:
            report.append(f"{sess_key}: в бандле нет — пропуск")

        for target in acl_targets:
            acl(target)
        report.append("ACL SYSTEM+Administrators: "
                      + ", ".join(str(t) for t in acl_targets))
    except (CryptoError, SecretLoaderError) as exc:
        raise RecoveryError(f"restore в {root}: {exc}") from exc
    return report


def main(
    argv: list[str] | None = None,
    *,
    ask_password: Callable[[str], str] = getpass.getpass,
    session_to_string: Callable[[str], str] | None = None,
    acl: Callable[[Path], None] = restrict_to_system_admins,
) -> int:
    """CLI §12. Пароль ТОЛЬКО через getpass-промпт: argv светится в
    process list и истории шелла, а бэкап-пароль — корень всей схемы."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="root → зашифрованный бандл")
    exp.add_argument("--root", default=".", help="корень репо (дефолт: cwd)")
    exp.add_argument("--slug", default="demo", help="slug клиента")
    exp.add_argument("--out", required=True, help="файл бандла (.jrvbak)")
    res = sub.add_parser("restore", help="бандл → ЧИСТЫЙ root")
    res.add_argument("--root", required=True, help="чистый корень")
    res.add_argument("--slug", default="demo", help="slug клиента")
    res.add_argument("--bundle", required=True, help="файл бандла (.jrvbak)")
    args = p.parse_args(argv)

    try:
        if args.cmd == "export":
            pw = ask_password("Пароль бэкапа (владельца): ")
            if ask_password("Повторите пароль: ") != pw:
                print("[recovery] FAIL: пароли не совпадают", file=sys.stderr)
                return 1
            secrets = collect_secrets(args.root, slug=args.slug,
                                      session_to_string=session_to_string)
            export_bundle_to_file(args.out, secrets, pw)
            print(f"[recovery] экспортировано {len(secrets)} элемент(ов) "
                  f"из {args.root} -> {args.out}")
            print("[recovery] OK. Бандл хранить ВНЕ машины (рядом с "
                  "recovery-ключами BitLocker) — P1P2_SPEC §12.")
        else:
            pw = ask_password("Пароль бэкапа (владельца): ")
            bundle = import_bundle_from_file(args.bundle, pw)
            report = restore_secrets(bundle, args.root, slug=args.slug,
                                     acl=acl)
            for line in report:
                print(f"[recovery] {line}")
            print("[recovery] OK. Дальше: старт раннера штатным путём "
                  "(bootstrap_env прочитает .enc).")
    except (RecoveryError, CryptoError) as e:
        print(f"[recovery] FAIL: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
