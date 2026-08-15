# -*- coding: utf-8 -*-
"""Приёмка бандла секретов (`.jrvbak`) — РАСШИФРОВКОЙ, а не фактом создания файла.

Зачем. Экспорт печатает «OK», когда файл записался. Но собирается бандл из того,
что лежит на диске (`collect_secrets`), — и если материал устарел, файл выйдет
ровно такой же на вид: то же имя, тот же порядок веса, свежий mtime. 2026-08-10
`.env.enc` был от 23.07: переэкспорт дал бы бандл БЕЗ `R2_BACKUP_BUCKET`, и «OK»
при экспорте этого не показал бы. Девятая проверка `ops_watchdog` тоже не
показывает — она сравнивает ДАТЫ, а не содержимое (PROBLEMS P26). Единственное
доказательство пригодности — открыть бандл паролем и посмотреть, что внутри.

Отсюда правило: после КАЖДОГО экспорта прогнать этот инструмент и заменять
старый бандл только по вердикту «пригоден» (PROBLEMS P26, фикс «а»).

Что проверяется (всё разом, чтобы не чинить по одной причине за прогон):
  * бандл вообще открывается этим паролем;
  * в нём есть `.env` и в нём есть обязательные ключи (`--require` добавляет свои);
  * есть хотя бы одна сессия — без них восстановятся настройки, но не клиенты;
  * есть `entropy.bin` — без него DPAPI-материал не расшифруется на новой машине.

Пароль спрашивается ТОЛЬКО промптом: в argv он засветился бы в списке процессов
и в истории шелла — по той же причине так сделан и сам экспорт. Следствие:
инструмент ИНТЕРАКТИВНЫЙ. `getpass` на Windows читает консоль напрямую (msvcrt)
и пайп игнорирует, поэтому из фонового/детач-запуска он не «упадёт с ошибкой»,
а повиснет молча. Запускать руками; в автоматику не ставить.

Значения секретов НЕ печатаются никогда — только имена ключей и факты наличия:
вывод уезжает в транскрипт сессии.

Коды возврата: 0 — бандл пригоден; 1 — непригоден или не открылся; 2 — ошибка
вызова (нет пути, нет файла).

Запуск:
    python scripts/verify_bundle.py "C:\\Users\\Admin\\OneDrive\\jarvis-recovery\\<файл>.jrvbak"
    python scripts/verify_bundle.py <файл>.jrvbak --require FAL_KEY
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatter.security.recovery import (  # noqa: E402
    RecoveryError, import_bundle_from_file)

# Ключи, без которых бандл бесполезен. Список короткий намеренно: он про
# «восстановление вообще не поедет», а не про «мы бы хотели, чтобы было».
# 2026-08-10 отсутствовал ровно `R2_BACKUP_BUCKET` — с него список и начался.
REQUIRED_ENV: tuple[str, ...] = ("R2_BACKUP_BUCKET",)

ENV_KEY_RE = re.compile(rb"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=")

_SESSION_SUFFIX = ".session"
_ENTROPY_KEY = "entropy.bin"
_ENV_KEY = ".env"


def env_key_names(env: bytes) -> set[str]:
    """Имена ключей из байтов env-файла. Закомментированная строка ключом НЕ
    считается: `# R2_BACKUP_BUCKET=x` — это отсутствующий ключ, а не наличие."""
    return {m.group(1).decode("ascii") for m in ENV_KEY_RE.finditer(env)}


def inspect_bundle(bundle: Mapping[str, bytes], *,
                   required_env: Sequence[str] = REQUIRED_ENV) -> dict:
    """Разобрать УЖЕ расшифрованный бандл и назвать все причины непригодности.

    Причины собираются списком, а не возвращаются по первой: владелец вводит
    пароль руками, и второй заход ради второй причины он делать не станет."""
    keys = sorted(bundle)
    sessions = sorted(k[:-len(_SESSION_SUFFIX)] for k in keys
                      if k.endswith(_SESSION_SUFFIX))
    has_entropy = _ENTROPY_KEY in bundle
    env = bundle.get(_ENV_KEY)

    problems: list[str] = []
    env_names: set[str] = set()
    missing_env: list[str] = []

    if env is None:
        problems.append("в бандле нет %s — восстанавливать настройки нечем" % _ENV_KEY)
    else:
        env_names = env_key_names(env)
        missing_env = [k for k in required_env if k not in env_names]
        if missing_env:
            problems.append(
                "в %s нет обязательных ключей: %s — материал бандла устарел"
                % (_ENV_KEY, ", ".join(missing_env)))

    if not sessions:
        problems.append(
            "в бандле нет ни одной сессии — клиенты будут логиниться заново")
    if not has_entropy:
        problems.append(
            "в бандле нет %s — DPAPI-материал не расшифруется на новой машине"
            % _ENTROPY_KEY)

    return {
        "items": len(keys),
        "keys": keys,
        "sessions": sessions,
        "has_entropy": has_entropy,
        "env_bytes": None if env is None else len(env),
        "env_keys": None if env is None else len(env_names),
        "required_env": list(required_env),
        "missing_env": missing_env,
        "problems": problems,
        "ok": not problems,
    }


def render(report: Mapping, *, name: str, size: int) -> list[str]:
    """Строки отчёта. Только имена и факты — ни одного значения секрета."""
    lines = [
        "файл          : %s (%d байт)" % (name, size),
        "элементов     : %d" % report["items"],
        "ключи бандла  : %s" % ", ".join(report["keys"]),
        "сессии        : %s" % (", ".join(report["sessions"])
                                if report["sessions"] else "НЕТ НИ ОДНОЙ"),
        "%-16s: %s" % (_ENTROPY_KEY, "есть" if report["has_entropy"] else "НЕТ"),
    ]
    if report["env_keys"] is None:
        lines.append("%-14s: НЕТ" % _ENV_KEY)
    else:
        lines.append(".env в бандле : %d байт, %d ключей"
                     % (report["env_bytes"], report["env_keys"]))
        for key in report["required_env"]:
            lines.append("%-16s: %s" % (
                key, "ОТСУТСТВУЕТ" if key in report["missing_env"] else "ЕСТЬ"))
    for problem in report["problems"]:
        lines.append("  ! %s" % problem)
    lines.append("")
    lines.append("ВЕРДИКТ: " + ("бандл пригоден" if report["ok"] else
                                "БАНДЛ НЕПРИГОДЕН — не заменять им старый"))
    return lines


def main(argv: Sequence[str] | None = None, *, ask_password=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_bundle", add_help=True,
        description="Проверить бандл секретов расшифровкой (пароль — промптом).")
    # `nargs="?"` вместо обязательного позиционного: argparse на нехватке
    # аргументов зовёт sys.exit(), а `main` обязан ВОЗВРАЩАТЬ код — иначе его
    # не вызвать из теста и не обернуть на месте вызова.
    parser.add_argument("path", nargs="?", help="путь к .jrvbak")
    parser.add_argument("--require", action="append", default=[], metavar="KEY",
                        help="дополнительный обязательный ключ .env (можно повторять)")
    # Лишние позиционные аргументы молча игнорируем: пароль в argv не читается
    # НИКОГДА, и падать из-за случайно дописанного слова здесь не за что.
    args, _ignored = parser.parse_known_args(list(argv) if argv is not None else None)

    if not args.path:
        print("укажи путь к .jrvbak")
        return 2

    path = Path(args.path)
    if not path.exists():
        print("СТОП: файла нет: %s" % path)
        return 2

    password = ask_password("Пароль бэкапа (владельца): ")
    try:
        bundle = import_bundle_from_file(path, password)
    except RecoveryError as exc:
        print("СТОП: бандл не открылся: %s" % exc)
        return 1

    report = inspect_bundle(bundle,
                            required_env=tuple(REQUIRED_ENV) + tuple(args.require))
    for line in render(report, name=path.name, size=path.stat().st_size):
        print(line)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
