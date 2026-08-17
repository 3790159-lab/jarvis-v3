"""DEV-26 для отпечатка смерти: снимаем след и требуем КРАСНОГО.

Предмет — единственный инструмент, который отвечает на вопрос «от чего умер
бот». Шестнадцать смертей за 17.08 остались без ответа именно потому, что
следа не было; сторож, не краснеющий на снятом следе, вернул бы это же
состояние, но с ощущением, что диагностика есть.

Мутируются решения: дописывать или перезаписывать файл, ловить ли крах, метить
ли чистый выход, как читать вердикт по PID и когда вооружаться.

⚠️ Одну сторону сознательно НЕ мутируем: `all_threads=True` у faulthandler.
Различающего случая нет — при `False` крах в главном потоке всё равно даёт
трассировку, а крах в НЕглавном пришлось бы ловить тестом, чей вердикт зависит
от того, какие кадры Python успел напечатать. Такая мутация проверяла бы
устройство вывода, а не решение; по правилу владельца 17.08 она снимается с
записью причины, а не остаётся «для количества».

Прогон: python scripts/mutate_death_fingerprint.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

F = "app/services/death_fingerprint.py"
B = "tools/jarvis_smart_telegram_control.py"
T = "tests/test_death_fingerprint.py"

MUTATIONS = [
    ("файл снова перезаписывается — предыдущая смерть стёрта подъёмом", F,
     [('        handle = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered',
       '        handle = open(path, "w", encoding="utf-8", buffering=1)')],
     f"{T}::test_the_file_is_appended_so_the_previous_death_survives"),

    ("крах больше не ловится — жёсткое падение выглядит внешним убийством", F,
     [("        faulthandler.enable(file=handle, all_threads=True)\n", "")],
     f"{T}::test_a_hard_crash_leaves_a_traceback"),

    ("чистый выход не метится — «нет метки» перестаёт что-либо значить", F,
     [("        atexit.register(_say_clean_exit, handle, pid)\n", "")],
     f"{T}::test_a_clean_exit_leaves_the_clean_marker"),

    ("нет записей — считаем убитым: вердикт по отсутствию данных", F,
     [('    if not path.is_file():\n        return "unknown"',
       '    if not path.is_file():\n        return "killed"')],
     f"{T}::test_an_unknown_pid_is_unknown_not_killed"),

    ("окно процесса не закрывается следующим BOOT — вердикты перепутаются", F,
     [("    for i, ln in enumerate(tail):\n"
       "        if ln.startswith(BOOT):\n"
       "            tail = tail[:i]\n"
       "            break\n", "")],
     f"{T}::test_a_killed_process_stays_killed_even_if_the_next_one_crashes"),

    ("вооружение роняет процесс, если не смогло открыть файл", F,
     [('    except Exception as exc:  # noqa: BLE001 — диагностика не имеет права ронять бота\n'
       '        print(f"[death-fingerprint] не смог открыть {path}: {exc!r}", flush=True)\n'
       '        return None',
       "    except Exception:\n        raise")],
     f"{T}::test_arming_never_takes_the_process_down"),

    ("бот вооружается ПОСЛЕ локов — смерть на старте остаётся без следа", B,
     [("    global _DEATH_FINGERPRINT_HANDLE\n"
       "    try:\n"
       "        from app.services.death_fingerprint import arm as _arm_death_fingerprint\n\n"
       "        _DEATH_FINGERPRINT_HANDLE = _arm_death_fingerprint()\n"
       "    except Exception as exc:  # noqa: BLE001 — диагностика не роняет прод\n"
       '        print(f"[death-fingerprint] не вооружился: {exc!r}", flush=True)\n\n', "")],
     f"{T}::test_the_bot_arms_the_fingerprint_before_anything_else"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: сломанный СБОР отвечает rc 2/4/5, и «не ноль значит покраснел»
    принял бы его за пойманную мутацию. encoding явный — cp1251 не знает байт
    0x98 и печатает пойманную мутацию слепой."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8-sig")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name} -> сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
