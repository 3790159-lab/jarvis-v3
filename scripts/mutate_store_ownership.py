# -*- coding: utf-8 -*-
"""Мутационный гейт арки «`Store` не переживает свой отказ» (спека §6).

Мишени — РЕШЕНИЯ спеки, а не слова кода: закрывать ли на провале, ловить ли
`BaseException`, перебрасывать ли голым `raise`, закрывать ли на УСПЕХЕ,
владеет ли каждая ручка панели своим соединением, и покрывает ли AST-сторож
живое дерево `app/`.

Сторожей писал ДРУГОЙ заход, от текста спеки, реализации не видя. Гейт
проверяет не код, а ИХ: ломаем решение и требуем красного от НАЗВАННОГО
сторожа (`файл::имя`), а не от файла — прогон по файлу зеленел бы за счёт
соседа, и карта «решение -> сторож» врала бы при зелёном гейте.

Харнесс взят у гейта арки панели вместе с обеими его поправками: откат по
СОХРАНЁННЫМ БАЙТАМ (git checkout врёт на autocrlf) и поиск фрагмента в обеих
формах концов строк.

Прогон: python scripts/mutate_store_ownership.py  (в worktree, дерево чистое)
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

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

DB = "chatter/storage/db.py"
TD = "app/routers/tamapi_dashboard.py"
PC = "app/panel_client.py"

G = "tests/test_store_ownership.py"

MUTATIONS = [
    # ── §2.1 конструктор закрывает за собой ──────────────────────────────
    ("провал конструктора снова не закрывает соединение", DB,
     [(b'            self._conn.close()\n            # \xd0\x93\xd0\xbe\xd0\xbb\xd1\x8b\xd0\xb9 raise',
       b'            # \xd0\x93\xd0\xbe\xd0\xbb\xd1\x8b\xd0\xb9 raise')],
     G + "::test_the_statutory_refusal_also_releases_the_file"),

    ("ловится только Exception — Ctrl+C проходит мимо", DB,
     [(b'        except BaseException:', b'        except Exception:')],
     G + "::test_keyboard_interrupt_mid_migration_releases_the_file"),

    ("лечение ГЛОТАЕТ исключение вместо голого raise", DB,
     [(b'\xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0 \xd0\xbf\xd1\x80\xd0\xb5\xd0\xb2\xd1\x80\xd0\xb0\xd1\x89\xd0\xb0\xd1\x82\xd1\x8c\xd1\x81\xd1\x8f \xd0\xb2 \xd0\xbf\xd1\x80\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x82\xd1\x8b\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5 \xd0\xbe\xd1\x88\xd0\xb8\xd0\xb1\xd0\xba\xd0\xb8.\n            raise\n',
       b'\xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0 \xd0\xbf\xd1\x80\xd0\xb5\xd0\xb2\xd1\x80\xd0\xb0\xd1\x89\xd0\xb0\xd1\x82\xd1\x8c\xd1\x81\xd1\x8f \xd0\xb2 \xd0\xbf\xd1\x80\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x82\xd1\x8b\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5 \xd0\xbe\xd1\x88\xd0\xb8\xd0\xb1\xd0\xba\xd0\xb8.\n            pass\n')],
     G + "::test_the_original_exception_reaches_the_caller_unchanged"),

    # ВСТРЕЧНАЯ ПОЛОВИНА. Без неё «закрывать всегда» прошло бы гейт: пять
    # мишеней выше остались бы зелёными, а `Store` стал бы бесполезен.
    ("соединение закрывается и на УСПЕХЕ тоже", DB,
     [(b'        except BaseException:', b'        finally:'),
      (b'\xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0 \xd0\xbf\xd1\x80\xd0\xb5\xd0\xb2\xd1\x80\xd0\xb0\xd1\x89\xd0\xb0\xd1\x82\xd1\x8c\xd1\x81\xd1\x8f \xd0\xb2 \xd0\xbf\xd1\x80\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x82\xd1\x8b\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5 \xd0\xbe\xd1\x88\xd0\xb8\xd0\xb1\xd0\xba\xd0\xb8.\n            raise\n',
       b'\xd0\xbf\xd1\x80\xd0\xb0\xd0\xb2\xd0\xb0 \xd0\xbf\xd1\x80\xd0\xb5\xd0\xb2\xd1\x80\xd0\xb0\xd1\x89\xd0\xb0\xd1\x82\xd1\x8c\xd1\x81\xd1\x8f \xd0\xb2 \xd0\xbf\xd1\x80\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x82\xd1\x8b\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5 \xd0\xbe\xd1\x88\xd0\xb8\xd0\xb1\xd0\xba\xd0\xb8.\n            pass\n')],
     G + "::test_a_successful_store_keeps_its_connection_open"),

    # ── §2.2 обе ручки панели владеют соединением ────────────────────────
    ("лампа снова берёт голый Store и надеется на `del`", TD,
     [(b'        with Store(_db_path()) as s:\n            killed = (s.get_runtime_flag("kill_switch") or "0").strip() == "1"',
       b'        s = Store(_db_path())\n        killed = (s.get_runtime_flag("kill_switch") or "0").strip() == "1"')],
     G + "::test_the_status_lamp_closes_its_connection"),

    # Мутация НЕ ломает отступы: `with store._conn:` — это транзакция sqlite,
    # она соединение не закрывает. Иначе мутант упал бы на СБОРЕ (rc 2/4), а
    # такое `run()` за пойманное не считает.
    ("мутирующая ручка снова не владеет соединением", TD,
     [(b'    with Store(_db_path()) as store:',
       b'    store = Store(_db_path())\n    with store._conn:')],
     G + "::test_the_action_handler_closes_its_connection"),

    # ── §2.3 сторож по AST смотрит в ЖИВОЕ дерево, а не только в синтетику ──
    ("голый Store просочился в app/ — скан обязан увидеть", PC,
     [(b'REQUIRED_INSTANCE_VARS = ("TAMAPI_DB", "TAMAPI_SLUG")',
       b'REQUIRED_INSTANCE_VARS = ("TAMAPI_DB", "TAMAPI_SLUG")\n\n\ndef _gate_probe_never_called():\n    return Store("x")')],
     G + "::test_no_bare_store_in_app"),
]


def write_mutant(path: Path, blob: bytes) -> None:
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
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
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
