"""JSON-план реестра для PowerShell-супервизора.

    python -m chatter.registry_cli
    python -m chatter.registry_cli --registry <путь> --root <корень>

Тонкий слой: вся логика в chatter.core.client_registry, здесь только файловая
система и сериализация. Гардиан читает вывод через ConvertFrom-Json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from chatter.core.client_registry import RegistryError, parse_registry, validate
from chatter.security.secret_loader import derive_session_enc_path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "chatter" / "clients" / "registry.yaml"


def session_available(session: str, *, root: str) -> bool:
    """Сможет ли build_session открыть эту сессию.

    Зеркалит контракт telethon_run.build_session: сначала `.enc`, иначе legacy
    plaintext. Наивная проверка «файл по пути существует» здесь НЕВЕРНА —
    после cutover P1/P2 plaintext `.session` на диске нет вовсе (в .secrets
    лежит только demo.session.enc), и живая volska была бы помечена invalid,
    а супервизор отказался бы её поднимать.

    Правило '.enc' берём из secret_loader, а не повторяем строкой: дубликат
    разъехался бы с build_session при первом же изменении."""
    base = Path(root) / session
    return Path(derive_session_enc_path(str(base))).exists() or base.exists()


def build_plan(
    text: str, *, root: str,
    session_available: Callable[[str], bool],
    client_dir_exists: Callable[[str], bool],
) -> dict:
    """{'fatal': str|None, 'clients': [...]} — ПОЛНЫЙ список клиентов.

    Полный, а не только запускаемые: супервизору нужно не только кого поднять,
    но и кого остановить, если тот ещё жив."""
    try:
        entries = parse_registry(text)
    except RegistryError as exc:
        # Не поднимаем исключение наружу: гардиан обязан пережить сломанный
        # реестр и сообщить причину, а не умереть молча (DEV-18).
        return {"fatal": str(exc), "clients": []}

    runnable, issues = validate(
        entries, root=root, session_available=session_available,
        client_dir_exists=client_dir_exists)
    runnable_slugs = {e.slug for e in runnable}
    errors = {i.slug: i.error for i in issues}
    return {
        "fatal": None,
        "clients": [
            {
                "slug": e.slug,
                "desired": "enabled" if e.enabled else "disabled",
                "runnable": e.slug in runnable_slugs,
                "error": errors.get(e.slug),
                "personas": list(e.personas),
                "session": e.session,
                "db": e.db,
            }
            for e in entries
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="JSON-план реестра клиентов chatter")
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    p.add_argument("--root", default=str(ROOT))
    args = p.parse_args(argv)

    reg = Path(args.registry)
    if not reg.exists():
        print(json.dumps({"fatal": f"registry not found: {reg}", "clients": []}))
        return 0

    plan = build_plan(
        reg.read_text(encoding="utf-8"),
        root=args.root,
        session_available=lambda s: session_available(s, root=args.root),
        client_dir_exists=lambda slug: (
            Path(args.root) / "chatter" / "clients" / slug).is_dir(),
    )
    print(json.dumps(plan, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
