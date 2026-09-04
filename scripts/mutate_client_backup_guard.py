# -*- coding: utf-8 -*-
"""Мутационный гейт: решения заставы клиентской заливки.

Застава закрывает необратимое действие наружу, и её отказ должен быть слышен
в обе стороны: пропустить суиту в боевой бакет — это порча бэкапов, а
заблокировать ночную заливку — это клиенты без бэкапа вовсе. Обе ошибки
проверяются здесь мутантами, а не рассуждением.

Мишени — РЕШЕНИЯ, а не строки: по какому признаку различается тестовое
окружение, с ЧЕМ сравнивается загрузчик (объект против атрибута модуля), и
доезжает ли отказ до вызывающего, не превратившись в строку сводки.

Прогон: python scripts/mutate_client_backup_guard.py  (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись (DEV-26): байткод-кэш считает пару
# (mtime в целых секундах, размер) достаточной, и две мутации одного размера
# в одну секунду неотличимы — вторая исполнилась бы байткодом первой.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

S = "app/services/state_backup.py"
M = "scripts/state_backup.py"

G = "tests/test_client_backup_live_guard.py"
_BLOCKED = G + "::test_under_pytest_with_the_real_uploader_is_blocked"
_INJECTED = G + "::test_injected_uploader_is_not_blocked"
_SILENT = G + "::test_without_the_pytest_marker_the_guard_stays_silent"
_E2E = G + "::test_the_script_path_that_corrupted_the_bucket_now_hits_the_guard"
_ATTR = G + "::test_patching_the_module_attribute_does_not_slip_past_the_guard"

MUTATIONS = [
    # -- признак тестового окружения ---------------------------------------
    ("1. признак не спрашивается — застава срабатывает ВСЕГДА", S,
     [(b'    if os.environ.get(_PYTEST_MARKER) and upload_file is _REAL_UPLOAD:',
       b'    if True and upload_file is _REAL_UPLOAD:')],
     _SILENT),

    ("2. застава отключена — суита снова льёт в боевой бакет", S,
     [(b'    if os.environ.get(_PYTEST_MARKER) and upload_file is _REAL_UPLOAD:',
       b'    if False and upload_file is _REAL_UPLOAD:')],
     _BLOCKED),

    # -- с ЧЕМ сравнивается загрузчик --------------------------------------
    ("3. сравнение с АТРИБУТОМ модуля вместо захваченного объекта", S,
     [(b'    if os.environ.get(_PYTEST_MARKER) and upload_file is _REAL_UPLOAD:',
       b'    if os.environ.get(_PYTEST_MARKER) and upload_file is r2_storage.upload_file:')],
     _ATTR),

    ("4. загрузчик не спрашивается — законные тесты заблокированы", S,
     [(b'    if os.environ.get(_PYTEST_MARKER) and upload_file is _REAL_UPLOAD:',
       b'    if os.environ.get(_PYTEST_MARKER):')],
     _INJECTED),

    # -- доезжает ли отказ до вызывающего ----------------------------------
    ("5. main() не пробрасывает отказ — он тонет в строке сводки", M,
     [(b'    except sb.LiveClientBackupBlocked:',
       b'    except _NeverRaised:')],
     _E2E),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`.

    Принимает И байты, И строку: мета-сторож на гейты зовёт этот метод строкой,
    и суженная до байтов сигнатура ломает ЗАМЕР гейта, а не сам гейт — то есть
    выглядит как «гейт не проверен», что в этом доме опаснее красного.
    """
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    Мишень 5 намеренно ссылается на несуществующее имя `_NeverRaised`: она
    обязана дать `failed`, а не `error` сбора, поэтому ловится тем же
    критерием — если вдруг даст rc 2, гейт назовёт её слепой, и это честно.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str, original: bytes) -> None:
    path = ROOT / rel
    path.write_bytes(original)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n" + out)


def head_hash(rel: str) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD:" + rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()[:12]


def disk_hash(rel: str) -> str:
    return subprocess.run(["git", "hash-object", rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()[:12]


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        edits = [(o, n) if o in mutated
                 else (o.replace(b"\n", b"\r\n"), n.replace(b"\n", b"\r\n"))
                 for o, n in edits]
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент не найден" % name)
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print("[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: %s" % name)
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel, original)
        if path.read_bytes() != original:
            raise SystemExit("ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: %s" % rel)
        if not caught:
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, why))
            blind.append((name, test))
        else:
            print("[ok]   %s -> сторож покраснел" % name)

    print("\nХэши после отката (mtime гейт НЕ восстанавливает — судить по хэшу):")
    for rel in (S, M):
        d, h = disk_hash(rel), head_hash(rel)
        print("  %-34s disk=%s head=%s %s" % (rel, d, h, "OK" if d == h else "РАЗОШЛИСЬ"))

    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
