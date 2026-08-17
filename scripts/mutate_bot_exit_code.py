"""DEV-26 для кода выхода бота: снимаем различитель и требуем КРАСНОГО.

Предмет — единственное число, которое отличает «бота убили» от «бот вышел
сам». Отпечаток 17.08 дал `killed`, но он по построению не различает
`TerminateProcess` и `os._exit()`. Сторож, не краснеющий на снятом различении,
вернул бы неделю поисков внешнего убийцы там, где бот уходит по своей команде.

Прогон: python scripts/mutate_bot_exit_code.py (в worktree, дерево чистое)
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

G = "scripts/bot_guardian_detached.ps1"
T = "tests/test_bot_guardian_exit_code.py"

MUTATIONS = [
    ("убийство больше не отличается от самовыхода", G,
     [('        1 { return "код 1 - УБИТ снаружи (taskkill /F = TerminateProcess)" }',
       '        1 { return "код 1 - вышел САМ" }')],
     f"{T}::test_code_one_reads_as_an_external_kill"),

    ("самовыход объявлен убийством — искать будут не там", G,
     [('        0 { return "код 0 - вышел САМ (os._exit(0): dev_task merge или /restart_bot)" }',
       '        0 { return "код 0 - УБИТ снаружи" }')],
     f"{T}::test_code_zero_reads_as_a_self_exit"),

    ("крах перестал быть отдельным показанием", G,
     [('                return "код $hex - КРАХ процесса (NTSTATUS)"',
       '                return "код $hex - вышел САМ"')],
     f"{T}::test_ntstatus_codes_read_as_a_crash"),

    ("отсутствие хэндла молча читается как чистый выход", G,
     [('    if ($null -eq $Code) { return "код выхода недоступен (хэндла нет)" }',
       '    if ($null -eq $Code) { return "код 0 - вышел САМ" }')],
     f"{T}::test_an_unavailable_code_says_so_instead_of_guessing"),

    ("вердикт не доезжает до строки журнала", G,
     [('    $tail = if ($ExitVerdict) { " | $ExitVerdict" } else { "" }', '    $tail = ""')],
     f"{T}::test_the_diagnosis_line_carries_the_exit_verdict"),

    ("недоступный код выхода снова молчит вместо объяснения", G,
     [("            } else {\n"
       "                # Живой случай 17.08 23:04: бота поднял ПРЕЖНИЙ экземпляр\n"
       "                # гардиана, у нынешнего хэндла нет — и строка молча выходила\n"
       "                # без вердикта. Молчание неотличимо от «не смотрели»; говорим\n"
       "                # вслух, что именно недоступно и почему.\n"
       "                $exitVerdict = (Get-ExitCodeVerdict $null) +\n"
       "                    \" — бот поднят другим экземпляром гардиана\"\n"
       "            }\n", "            }\n")],
     f"{T}::test_a_missing_handle_is_reported_out_loud_not_silently"),

    ("код выхода спрашивают у ЖИВОГО процесса", G,
     [("        if (-not $alive -and $script:BotProc -and $script:BotProc.HasExited) {",
       "        if ($script:BotProc) {")],
     f"{T}::test_a_live_process_is_never_asked_for_an_exit_code"),
]


def write_mutant(path: Path, text: str) -> None:
    # BOM обязателен для .ps1: без него PS 5.1 читает файл как cp1251 и падает
    # на кириллице — мутант не запустился бы вовсе, и гейт зачёл бы это себе.
    path.write_text("﻿" + text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`; encoding явный (cp1251 не знает байт 0x98)."""
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
