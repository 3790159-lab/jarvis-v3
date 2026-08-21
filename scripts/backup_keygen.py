# -*- coding: utf-8 -*-
"""Одноразовый инструмент владельца: пара ключей клиентского набора (DEV-46 §3.3 B).

Владелец выбрал вариант B: **на хосте только ПУБЛИЧНЫЙ ключ, приватный — вне
машины.** Хост умеет ПИСАТЬ бэкап и НЕ умеет его ЧИТАТЬ. Модуль
``app/services/backup_crypto.py`` держит эту границу тем, что не пишет на диск
вообще — ни строчки. Но пару кто-то создать обязан, и делает это здесь человек
руками, один раз, глядя на вывод.

    python scripts/backup_keygen.py --private-out E:\\keys\\jarvis-backup.key

Отсюда всё поведение файла, и ни один пункт не украшение:

* **Умолчания у ``--private-out`` нет.** «Куда-нибудь по умолчанию» на
  арендованной машине означает «на арендованную машину» — то есть ровно то,
  против чего выбран вариант B.
* **Внутрь дерева репозитория приватный ключ не пишется.** Проверка идёт по
  РЕАЛЬНОМУ пути (``Path.resolve()``), а не по строке: путь вида
  ``C:\\jarvis\\..\\jarvis\\.secrets\\x.key`` — это внутри дерева, и строковое
  сравнение этого не видит. Инструмент существует ради того, чтобы приватный
  ключ оказался ВНЕ машины, и обязан не дать сделать наоборот.
* **Существующий файл не перезаписывается молча.** Затёртый ключ — это
  потерянный доступ ко ВСЕМ прошлым бэкапам сразу, а не одна испорченная
  строка. Нужен явный ``--force``.
* **Приватный ключ не печатается на stdout никогда.** Консоль уезжает в
  историю оболочки, в скроллбек и в логи запуска; секрет туда не едет. На
  stdout — только то, что кладётся в окружение ХОСТА, и оно не секретно.

Прав на файл инструмент не изображает: сужение ACL через ``icacls``
ПРОВЕРЯЕТСЯ листингом, и если сузить не удалось — на stdout идёт честное
предупреждение с причиной. Видимость защиты хуже её отсутствия: с ней человек
не унесёт ключ, решив, что тот и так закрыт.

Ни сети, ни чтения ``.env``, ни телеграма. Исключения не глотаются (DEV-18):
предвиденный отказ — громкий текст и код возврата 1, непредвиденное падает
трассировкой.
"""
from __future__ import annotations

import argparse
import base64
import locale
import os
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.services.backup_crypto import (  # noqa: E402
    generate_keypair,
    public_key_fingerprint,
)

_PUBLIC_KEY_ENV = "JARVIS_BACKUP_PUBLIC_KEY"
_SALT_ENV = "JARVIS_BACKUP_KEY_SALT"

# 32 случайных байта соли псевдонимов (§9.1). ``load_pseudonym_salt`` требует
# минимум 16; берём вдвое больше — соль не секрет по цене и живёт годами.
_SALT_BYTES = 32

# Любой предвиденный отказ — один и тот же ненулевой код. Ноль означает ровно
# одно: пара создана, приватный ключ лежит по названному пути.
_REFUSAL_EXIT = 1

_ICACLS_TIMEOUT = 30


class KeygenRefusal(Exception):
    """Предвиденный отказ инструмента: путь внутри дерева, файл уже есть и т.п.

    Отдельный класс, чтобы отказ печатался человеку строкой, а неожиданная
    ошибка падала трассировкой и была видна как дефект (DEV-18)."""


# --------------------------------------------------------------------------
# Куда НЕЛЬЗЯ: корни дерева репозитория
# --------------------------------------------------------------------------

def repo_roots() -> list[Path]:
    """Корни, под которыми приватному ключу лежать запрещено.

    Первый — дерево, из которого запущен сам скрипт. Второй появляется, когда
    это дерево — worktree: файл ``.git`` тогда указывает на
    ``<главное дерево>/.git/worktrees/<имя>``, и писать ключ в ГЛАВНОЕ дерево
    из worktree — та же ошибка, просто этажом выше. Разбирается только точная
    форма маркера; всё прочее оставляет один корень, и это честнее, чем
    гадать."""
    roots = [_ROOT]
    marker = _ROOT / ".git"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = _ROOT / gitdir
            parts = gitdir.resolve().parts
            if len(parts) >= 3 and parts[-2] == "worktrees" and parts[-3] == ".git":
                main_root = Path(*parts[:-3])
                if main_root not in roots:
                    roots.append(main_root)
    return roots


def _is_inside(path: Path, root: Path) -> bool:
    """`path` лежит под `root` (или равен ему) — по разобранным путям."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def check_target(raw: str) -> Path:
    """Разобрать `--private-out` и отказать, если писать туда нельзя.

    Сравнение идёт по ``resolve()``: ``..`` внутри строки схлопывается, и
    ``C:\\jarvis\\..\\jarvis\\.secrets\\x.key`` опознаётся как дерево
    репозитория, хотя строкой на него не похож."""
    if not raw.strip():
        raise KeygenRefusal(
            "--private-out пуст: путь к приватному ключу называет человек, "
            "умолчания у этого аргумента нет намеренно (DEV-46 §3.3 B)")

    target = Path(raw).expanduser().resolve()
    for root in repo_roots():
        if _is_inside(target, root):
            raise KeygenRefusal(
                f"приватный ключ нельзя класть в дерево репозитория.\n"
                f"  указан путь:      {raw}\n"
                f"  реальный путь:    {target}\n"
                f"  корень дерева:    {root}\n"
                "Вариант B (§3.3) выбран ради того, чтобы приватного ключа на "
                "этой машине не было: хост арендованный, «заберут вместе с "
                "диском» — не гипотеза. Ключ в дереве отменяет весь смысл "
                "схемы, а .gitignore от кражи диска не спасает. Назовите путь "
                "на съёмном носителе или на машине владельца.")

    if target.is_dir():
        raise KeygenRefusal(
            f"{target} — это каталог, а нужен путь к ФАЙЛУ ключа "
            f"(например {target / 'jarvis-backup.key'})")

    parent = target.parent
    if not parent.is_dir():
        raise KeygenRefusal(
            f"каталог {parent} не существует.\n"
            f"  указан путь:      {raw}\n"
            f"  реальный путь:    {target}\n"
            "Каталог здесь не создаётся сам: опечатка в букве диска молча "
            "создала бы дерево не там, где владелец собирался хранить ключ.")
    return target


# --------------------------------------------------------------------------
# Запись файла приватного ключа
# --------------------------------------------------------------------------

def private_key_file_body(private_raw: bytes, fingerprint: str,
                          created: str) -> str:
    """Содержимое файла: шапка-комментарий и base64 в ОДНУ строку.

    Шапка нужна через год: по ней видно, к какому набору объектов ключ
    подходит (отпечаток ПУБЛИЧНОГО ключа тот же, что уезжает в манифест) и
    когда он создан. Без неё две флешки с двумя ключами неразличимы."""
    encoded = base64.b64encode(private_raw).decode("ascii")
    return (
        "# jarvis DEV-46 (§3.3 B) — ПРИВАТНЫЙ ключ клиентского набора бэкапа\n"
        f"# создан: {created}\n"
        f"# отпечаток ПУБЛИЧНОГО ключа: {fingerprint}\n"
        "# формат: X25519, 32 сырых байта, base64 одной строкой ниже\n"
        "# Этим ключом открываются объекты backups/client/*. Другого ключа к "
        "ним нет.\n"
        "# Место этого файла — вне машины-хоста. Дрил восстановления (§9.2) "
        "гоняется только там, где он лежит.\n"
        f"{encoded}\n"
    )


def write_private_key(target: Path, body: str, *, force: bool) -> None:
    """Записать файл ключа, не затирая чужой молча.

    Без ``--force`` открытие идёт с ``O_EXCL``: даже если файл появится между
    проверкой и открытием, запись провалится, а не затрёт. Молчаливое
    затирание — это потеря доступа ко всем прошлым бэкапам."""
    if target.exists() and not force:
        raise KeygenRefusal(
            f"файл {target} уже существует.\n"
            "Перезапись молча — это потерянный доступ ко ВСЕМ бэкапам, "
            "зашифрованным прежней парой: новый ключ старые объекты не "
            "открывает. Если прежний ключ действительно не нужен — повторите "
            "с --force; если не уверены — сначала унесите старый файл.")

    flags = os.O_CREAT | os.O_WRONLY
    flags |= os.O_TRUNC if force else os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    # 0o600 на Windows правами не управляет (там решает ACL, см.
    # restrict_permissions), но на POSIX закрывает файл сразу при создании —
    # то есть до того, как в него лягут байты ключа.
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body.encode("utf-8"))


# --------------------------------------------------------------------------
# Права на файл: сузить и ПРОВЕРИТЬ, иначе сказать вслух
# --------------------------------------------------------------------------

def _decode_console(raw: bytes) -> str:
    return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _icacls_principals(listing: str, target: Path) -> list[str]:
    """Кому даёт права листинг ``icacls`` — список субъектов ACE.

    Первая строка листинга начинается с самого пути, дальше идут строки с
    отступом; хвост — «Successfully processed ...». Путь с первой строки
    срезается, иначе двоеточие диска уедет в разбор субъекта."""
    principals: list[str] = []
    for index, line in enumerate(listing.splitlines()):
        text = line.rstrip()
        if not text.strip():
            continue
        if index == 0 and text.startswith(str(target)):
            text = text[len(str(target)):]
        elif not line[:1].isspace():
            # Строка без отступа и не путь — это уже итоговая сводка.
            break
        text = text.strip()
        head, sep, _ = text.rpartition(":(")
        if not sep or not head:
            continue
        principals.append(head)
    return principals


def restrict_permissions(target: Path) -> list[str]:
    """Сузить права до владельца. Возвращает список ПРЕДУПРЕЖДЕНИЙ.

    Пустой список означает не «мы попытались», а «сужено И проверено
    листингом». Всё, что не удалось, приезжает текстом и печатается человеку:
    изображать защиту, которой нет, опаснее, чем её не иметь — с ней ключ
    останется на диске «потому что он и так закрыт»."""
    if os.name != "nt":
        os.chmod(target, 0o600)
        mode = stat.S_IMODE(target.stat().st_mode)
        if mode != 0o600:
            return [f"права файла {oct(mode)}, ожидалось 0o600 — сузить "
                    "не удалось, файл читается не только владельцем"]
        return []

    user = os.environ.get("USERNAME", "").strip()
    if not user:
        return ["переменная USERNAME пуста — некому выдать права, ACL файла "
                "остался унаследованным от каталога"]

    # Два написания субъекта, потому что на машине вне домена `USERDOMAIN`
    # равен `WORKGROUP`, и `WORKGROUP\<user>` icacls не разрешает в SID
    # (код 1332). Домен-квалифицированное имя пробуется первым — на доменной
    # машине оно точнее, — а голое имя подхватывает случай рабочей группы.
    domain = os.environ.get("USERDOMAIN", "").strip()
    candidates = [f"{domain}\\{user}"] if domain else []
    candidates.append(user)

    failures: list[str] = []
    for principal in candidates:
        try:
            granted = subprocess.run(
                ["icacls", str(target), "/inheritance:r", "/grant:r",
                 f"{principal}:F"],
                capture_output=True, timeout=_ICACLS_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return [f"icacls не отработал ({type(exc).__name__}: {exc}) — ACL "
                    "файла остался унаследованным от каталога"]
        if granted.returncode == 0:
            break
        failures.append(
            f"{principal}: код {granted.returncode}, "
            f"{_decode_console(granted.stderr).strip() or '(без текста)'}")
    else:
        return [f"icacls не выдал права ни одному написанию имени владельца "
                f"({'; '.join(failures)}) — ACL файла остался унаследованным "
                "от каталога"]

    try:
        listed = subprocess.run(["icacls", str(target)],
                                capture_output=True, timeout=_ICACLS_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"права выданы, но проверить их не удалось "
                f"({type(exc).__name__}: {exc}) — считайте ACL неизвестным"]
    if listed.returncode != 0:
        return [f"права выданы, но листинг icacls вернул код "
                f"{listed.returncode} — считайте ACL неизвестным"]

    principals = _icacls_principals(_decode_console(listed.stdout), target)
    if not principals:
        return ["листинг icacls не разобрался — подтвердить сужение прав "
                "нечем, считайте ACL неизвестным"]
    strangers = [name for name in principals
                 if name.split("\\")[-1].strip().lower() != user.lower()]
    if strangers:
        return [f"кроме владельца, к файлу допущены: {', '.join(strangers)} — "
                "сужение прав НЕ состоялось"]
    return []


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------

def env_lines(public_raw: bytes, salt: bytes) -> list[str]:
    """Две строки для окружения ХОСТА. Секретного здесь нет по построению."""
    return [
        f"{_PUBLIC_KEY_ENV}={base64.b64encode(public_raw).decode('ascii')}",
        f"{_SALT_ENV}={base64.b64encode(salt).decode('ascii')}",
    ]


def memo_lines(target: Path, fingerprint: str) -> list[str]:
    """Памятка. Печатается в конце, потому что дочитывают последнее."""
    return [
        "ПАМЯТКА",
        f"  1. Приватный ключ лежит в {target}. Унесите его С ЭТОЙ МАШИНЫ —",
        "     на съёмный носитель или в менеджер паролей. На хосте приватного",
        "     ключа не должно быть никогда: ни для дрила, ни «временно, на",
        "     время восстановления» (§3.3 B).",
        "  2. Без этого файла клиентские бэкапы не читаются ВООБЩЕ — ни нами,",
        "     ни провайдером хранилища. Потеря ключа = потеря всех объектов",
        "     backups/client, сколько бы их ни накопилось за год.",
        "  3. Дрил восстановления (§9.2) запускается только там, где лежит этот",
        "     файл, — на машине владельца. Хост расшифровать не может по",
        "     построению, и зелёный дрил на хосте означал бы, что вариант B",
        "     сломан.",
        "  4. Инструмент одноразовый. Повторный запуск делает ДРУГУЮ пару;",
        "     объекты, зашифрованные прежним публичным ключом, новой парой не",
        f"     открываются. Отпечаток текущей пары: {fingerprint}",
    ]


def _emit(lines: list[str]) -> None:
    for line in lines:
        print(line)


def run(raw_target: str, *, force: bool) -> int:
    """Весь сценарий. Путь проверяется ДО генерации пары."""
    target = check_target(raw_target)

    private_raw, public_raw = generate_keypair()
    fingerprint = public_key_fingerprint(public_raw)
    salt = os.urandom(_SALT_BYTES)
    created = datetime.now().astimezone().isoformat(timespec="seconds")

    write_private_key(
        target,
        private_key_file_body(private_raw, fingerprint, created),
        force=force)
    warnings = restrict_permissions(target)

    _emit([
        "# DEV-46 §3.3 B — две строки ниже кладутся в окружение ХОСТА (.env).",
        "# Секретного в них нет: публичный ключ не секрет, соль псевдонимов",
        "# живёт на хосте рядом с ним (§9.1).",
    ])
    _emit(env_lines(public_raw, salt))
    _emit([
        f"# отпечаток публичного ключа (public_key_fingerprint): {fingerprint}",
        "#   он же в шапке файла приватного ключа и в манифесте бэкапа —",
        "#   сверьте, когда манифест появится.",
        f"# приватный ключ записан: {target}",
        "#   на stdout он не печатается ни при каких условиях: консоль уезжает",
        "#   в историю оболочки, в скроллбек и в логи запуска.",
        "",
    ])

    if warnings:
        _emit(["ПРЕДУПРЕЖДЕНИЕ: права на файл приватного ключа НЕ сужены."])
        for text in warnings:
            _emit([f"  - {text}"])
        _emit([
            "  Файл лежит с правами каталога, в котором создан. Это не повод",
            "  оставить его здесь «до завтра»: унесите ключ сейчас.",
            "",
        ])
    else:
        _emit([
            "Права на файл приватного ключа сужены до владельца и проверены "
            "листингом.",
            "",
        ])

    _emit(memo_lines(target, fingerprint))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup_keygen.py",
        description=(
            "Одноразовый инструмент владельца: пара ключей клиентского набора "
            "бэкапа (DEV-46 §3.3, вариант B). Публичный ключ и соль печатаются "
            "для окружения хоста, приватный ключ кладётся туда, куда скажет "
            "владелец, — и на stdout не попадает никогда."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Пример:\n"
            "  python scripts/backup_keygen.py "
            "--private-out E:\\keys\\jarvis-backup.key\n"))
    parser.add_argument(
        "--private-out", required=True, metavar="ПУТЬ",
        help=("куда положить приватный ключ. Умолчания нет намеренно: "
              "«куда-нибудь по умолчанию» на арендованной машине означает «на "
              "арендованную машину». Путь внутри дерева репозитория "
              "отвергается."))
    parser.add_argument(
        "--force", action="store_true",
        help=("перезаписать существующий файл ключа. Без него перезапись "
              "отвергается: затёртый ключ — потерянный доступ ко всем прошлым "
              "бэкапам."))
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Консоль под cp866/cp1251 иначе роняет вывод на первом же тире —
            # и отказ, который человек обязан прочитать, не доедет до него.
            reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return run(args.private_out, force=args.force)
    except KeygenRefusal as exc:
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return _REFUSAL_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
