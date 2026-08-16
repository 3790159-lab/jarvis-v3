# -*- coding: utf-8 -*-
"""Перешифровать `.env` -> `.env.enc` после правки секретов, и уметь СКАЗАТЬ,
что они разошлись.

Почему это отдельный шаг, а не деталь `add_secret.ps1`:
`scripts/add_secret.ps1` пишет в PLAINTEXT `.env` (значение идёт через
маскированный промпт и не попадает в argv/историю), а `bootstrap_env` при
наличии `.env.enc` читает ТОЛЬКО `.enc` — тихих plaintext-путей нет по
P1P2 §2.1. Между этими двумя фактами дыра: ключ записан, но до процесса не
доезжает, и НИЧТО об этом не сообщает.

Дыра стоила трёх ручных повторов и одного близкого промаха: `.env` был
свежий, `.env.enc` четырёхдневный, а `chatter.security.recovery export` берёт
`.enc` — бандл секретов чуть не уехал со СТАРЫМ env. Поэтому у скрипта две
функции, и `enc_status` важнее самой перешифровки: она отвечает на вопрос
«можно ли сейчас снимать бандл».

Логика живёт здесь (Python, под pytest), а `.ps1` рядом — тонкая обёртка.
Тот же принцип, что у гардиана: решение принимает тестируемая сторона.

⚠️ Сверка идёт РАСШИФРОВАННЫМ содержимым. Побайтовое сравнение `.enc`
невозможно в принципе: DPAPI недетерминирован, две шифровки одних и тех же
байт дают разные блобы (пин — `test_verification_cannot_be_byte_comparison_of_enc`).

Значения секретов не печатаются и в argv не попадают: отчёт содержит только
имена ключей и их количество.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatter.security.crypto import decrypt_from_file, encrypt_to_file  # noqa: E402


class ReencryptError(Exception):
    """Отказ, который обязан быть громким. Молчаливый провал здесь означает
    секрет, не доехавший до процесса, — и ничего в логах (DEV-18)."""


def _paths(root) -> tuple[Path, Path]:
    root = Path(root)
    return root / ".env", root / ".env.enc"


def key_names(data: bytes) -> list[str]:
    """Имена ключей из plaintext-`.env`. ТОЛЬКО имена: отчёт со значением
    секрета — это тот же plaintext-путь, только через stdout."""
    out = []
    for line in data.decode("utf-8-sig", "replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        out.append(stripped.split("=", 1)[0].strip())
    return sorted(set(out))


def enc_status(root, *, entropy_path=None) -> str:
    """'no_env' | 'no_enc' | 'unreadable' | 'in_sync' | 'stale'.

    Решение по СОДЕРЖИМОМУ, а не по mtime. mtime двигают копирование, git,
    антивирус и синхронизация, ничего не меняя внутри; а `.env`,
    отредактированный «в те же байты», не должен считаться рассинхроном.
    Здесь дешевле расшифровать и сравнить, чем держать эвристику, которая
    врёт в обе стороны."""
    plain, enc = _paths(root)
    if not plain.exists():
        return "no_env"
    if not enc.exists():
        return "no_enc"
    try:
        current = decrypt_from_file(enc, entropy_path=entropy_path)
    except Exception:
        # Причина не важна: нет entropy, tamper, чужая машина — во всех
        # случаях вердикт один и он НЕ зелёный.
        return "unreadable"
    return "in_sync" if current == plain.read_bytes() else "stale"


def reencrypt(root, *, entropy_path=None) -> list[str]:
    """`.env` -> `.env.enc` с бэкапом и верификацией. Возвращает отчёт
    (только метаданные). Любой сбой — исключение, не False."""
    plain, enc = _paths(root)
    report: list[str] = []

    if not plain.exists():
        raise ReencryptError(f"нет {plain} — нечего шифровать")
    data = plain.read_bytes()

    backup: Path | None = None
    if enc.exists():
        # СНАЧАЛА убеждаемся, что прежний .enc читается. Нерасшифровываемый
        # .enc — авария, а не повод его перезаписать: перезапись уничтожит
        # единственную улику того, что пошло не так (потерянная entropy,
        # tamper, копия с другой машины).
        try:
            old = decrypt_from_file(enc, entropy_path=entropy_path)
        except Exception as exc:
            raise ReencryptError(
                f"прежний {enc.name} НЕ расшифровывается ({type(exc).__name__}): "
                "останавливаюсь до перезаписи, разберись с entropy/tamper"
            ) from exc
        backup = enc.with_name(f".env.enc.pre-regen-{int(time.time())}.bak")
        backup.write_bytes(enc.read_bytes())
        report.append(f"бэкап прежнего .enc: {backup.name} ({len(old)} байт)")

    encrypt_to_file(enc, data, entropy_path=entropy_path)

    if decrypt_from_file(enc, entropy_path=entropy_path) != data:
        if backup is not None:
            enc.write_bytes(backup.read_bytes())
            report.append("верификация не сошлась — прежний .enc ВОССТАНОВЛЕН")
        raise ReencryptError(
            "верификация не сошлась: .enc не расшифровывается в исходник. "
            "Использовать его НЕЛЬЗЯ." + ("" if backup else " Бэкапа не было."))

    names = key_names(data)
    report.append(f"{enc.name} перешифрован и верифицирован, {len(data)} байт")
    report.append(f"ключей в .env: {len(names)}")
    report.append("ключи: " + ", ".join(names))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".", help="корень репо (дефолт: cwd)")
    p.add_argument("--check", action="store_true",
                   help="только доложить статус, ничего не писать")
    args = p.parse_args(argv)

    status = enc_status(args.root)
    print(f"[reencrypt] статус до: {status}")

    if args.check:
        # 'stale' — рабочее состояние, но НЕ то, из которого снимают бандл.
        return 0 if status == "in_sync" else 1

    if status == "in_sync":
        print("[reencrypt] .env.enc уже совпадает с .env — ничего не делаю")
        return 0
    try:
        for line in reencrypt(args.root):
            print(f"[reencrypt] {line}")
    except ReencryptError as exc:
        print(f"[reencrypt] ОТКАЗ: {exc}", file=sys.stderr)
        return 1
    print("[reencrypt] OK. Секреты доедут до процесса при следующем старте.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
