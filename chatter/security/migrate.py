"""Одноразовый мигратор секретов P1/P2 (слой процесса, задача 3).

`python -m chatter.security.migrate --root C:\\jarvis` = setup-шаги cutover
§6 п.4-6 одним инструментом, каждый шаг идемпотентен:

1. entropy.bin (32Б os.urandom; существующий НИКОГДА не перезаписывается);
2. ACL SYSTEM+Administrators, наследование срезано: .secrets\\, entropy,
   все созданные .enc;
3. .env → .env.enc (верификация: расшифровка байт-в-байт);
4. .secrets/<slug>.session (SQLite) → StringSession → <slug>.session.enc
   (верификация: обратная расшифровка в ту же строку).

Plaintext НЕ удаляется — бэкап отката до cutover п.9, шред руками после
верификации (§6). User-scope блобов не существует (cutover не было) —
миграция всегда plaintext → machine-scope.

Отчёт не содержит значений секретов — только пути и факты.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

from chatter.security.crypto import (
    CryptoError, decrypt_from_file, generate_entropy,
    restrict_to_system_admins,
)
from chatter.security.secret_loader import (
    SecretLoaderError, load_string_session, migrate_plaintext_file,
    save_string_session,
)


class MigrationError(RuntimeError):
    """Мигратор упал — явно, с указанием шага (DEV-18)."""


def _sqlite_session_to_string(path: str) -> str:
    """Legacy SQLite-сессия → StringSession-строка (офлайн, без сети).
    Дубль трёх строк из telethon_run — импорт оттуда тащит весь раннер."""
    from telethon.sessions import SQLiteSession, StringSession
    return StringSession.save(SQLiteSession(path))


def run_migration(
    root: str | Path,
    *,
    slug: str = "demo",
    acl: Callable[[Path], None] = restrict_to_system_admins,
    session_to_string: Callable[[str], str] | None = None,
    environ=os.environ,
) -> list[str]:
    """Все шаги миграции. Возвращает отчёт (строки без значений секретов)."""
    root = Path(root)
    secrets_dir = root / ".secrets"
    report: list[str] = []

    # 1. entropy: env-override уважается (тот же резолв, что в crypto)
    env_override = environ.get("JARVIS_ENTROPY_FILE")
    entropy = Path(env_override) if env_override else secrets_dir / "entropy.bin"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        created = generate_entropy(entropy)
    except CryptoError as exc:
        raise MigrationError(f"шаг entropy: {exc}") from exc
    report.append(f"entropy {entropy}: "
                  + ("создан (32Б)" if created else "уже есть, не трогаю"))

    # 2. ACL на каталог и entropy сразу — до появления .enc-материала
    acl_targets = [secrets_dir, entropy]

    # 3. .env → .env.enc
    env_plain, env_enc = root / ".env", root / ".env.enc"
    if env_plain.exists() or env_enc.exists():
        try:
            migrated = migrate_plaintext_file(env_plain, env_enc,
                                              entropy_path=entropy)
        except (SecretLoaderError, CryptoError) as exc:
            raise MigrationError(f"шаг .env: {exc}") from exc
        if migrated:
            # верификация: расшифровка байт-в-байт (cutover §6 п.5)
            if decrypt_from_file(env_enc, entropy_path=entropy) != \
                    env_plain.read_bytes():
                raise MigrationError(
                    f"шаг .env: верификация не сошлась — {env_enc} не "
                    "расшифровывается в исходник, .enc НЕ использовать")
        report.append(f"{env_enc}: "
                      + ("создан, верифицирован" if migrated
                         else "уже есть, не трогаю"))
        acl_targets.append(env_enc)
    else:
        report.append(f"{env_plain}: нет — пропуск (skip)")

    # 4. сессия slug'а → .session.enc
    sess_plain = secrets_dir / f"{slug}.session"
    sess_enc = secrets_dir / f"{slug}.session.enc"
    if sess_enc.exists():
        report.append(f"{sess_enc}: уже есть, не трогаю")
        acl_targets.append(sess_enc)
    elif sess_plain.exists():
        string = (session_to_string or _sqlite_session_to_string)(
            str(sess_plain))
        if not string:
            raise MigrationError(
                f"шаг сессии: {sess_plain} без auth_key — это не "
                "залогиненная сессия, мигрировать нечего")
        save_string_session(sess_enc, string, entropy_path=entropy)
        # верификация: обратная расшифровка в ту же строку (cutover §6 п.5)
        if load_string_session(sess_enc, entropy_path=entropy) != string:
            raise MigrationError(
                f"шаг сессии: верификация не сошлась — {sess_enc} не "
                "расшифровывается в исходную строку, .enc НЕ использовать")
        report.append(f"{sess_enc}: создан, верифицирован "
                      "(plaintext оставлен бэкапом)")
        acl_targets.append(sess_enc)
    else:
        report.append(f"{sess_plain}: нет — пропуск (skip)")

    # 5. ACL последним, когда весь материал на месте
    for target in acl_targets:
        try:
            acl(target)
        except CryptoError as exc:
            raise MigrationError(f"шаг ACL {target}: {exc}") from exc
    report.append("ACL SYSTEM+Administrators (наследование срезано): "
                  + ", ".join(str(t) for t in acl_targets))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".", help="корень репо (дефолт: cwd)")
    p.add_argument("--slug", default="demo", help="slug клиента (дефолт demo)")
    args = p.parse_args(argv)
    try:
        report = run_migration(args.root, slug=args.slug)
    except MigrationError as e:
        print(f"[migrate] FAIL: {e}", file=sys.stderr)
        return 1
    for line in report:
        print(f"[migrate] {line}")
    print("[migrate] OK. Plaintext НЕ удалён (бэкап отката) — шред только "
          "руками после верификации, cutover §6 п.9.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
