"""DEV-26 для ротации boot-логов: снимаем предохранитель и требуем КРАСНОГО.

Предмет — не поведение бота, а возможность РАЗОБРАТЬ его смерть. Сторож,
который не краснеет на снятой ротации, охраняет ровно ничего: 17.08 мы уже
стояли перед четырьмя рестартами без единой улики.

Мутируются решения: сохранять ли непустой файл, ротировать ли пустой, есть ли
верхняя граница, освобождается ли путь, переживает ли подъём отказ ротации, и
зовёт ли её вообще путь запуска.

Прогон: python scripts/mutate_boot_log_rotation.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись (DEV-26): pytest сам байткод .ps1 не
# кэширует, но правило одно на все гейты — иначе исключение станет привычкой.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

G = "scripts/bot_guardian_detached.ps1"
T = "tests/test_bot_guardian_boot_log_rotation.py"

MUTATIONS = [
    ("пустой файл снова едет в архив — улику в нём не найти", G,
     [("    if (-not $item -or $item.Length -eq 0) { return }   # пустой хранить незачем\n",
       "")],
     f"{T}::test_an_empty_log_is_not_archived"),

    ("верхняя граница снята — диагностика съедает диск", G,
     [("    Get-ChildItem -Path $dir -Filter \"$base.*$ext\" -ErrorAction SilentlyContinue |\n"
       "        Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep |\n"
       "        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }\n",
       "")],
     f"{T}::test_the_archive_has_an_upper_bound"),

    ("столкновение имён не разводится — вторая улика затирает первую", G,
     [("    $n = 1\n"
       "    while (Test-Path $target) {\n"
       "        $target = Join-Path $dir \"$base.$stamp-$n$ext\"\n"
       "        $n++\n"
       "    }\n", "")],
     f"{T}::test_two_crashes_in_the_same_second_both_survive"),

    ("копия вместо переноса — путь занят, два экземпляра пишут в один файл", G,
     [("    try { Move-Item $Path $target -Force -ErrorAction Stop }",
       "    try { Copy-Item $Path $target -Force -ErrorAction Stop }")],
     f"{T}::test_a_previous_crash_trace_survives_the_next_launch"),

    ("отказ ротации снова роняет подъём бота", G,
     [("    try { Move-Item $Path $target -Force -ErrorAction Stop }\n"
       "    catch {",
       "    Move-Item $Path $target -Force -ErrorAction Stop\n"
       "    if ($false) {")],
     f"{T}::test_a_rotation_that_cannot_happen_does_not_stop_the_bot"),

    ("путь запуска больше не зовёт ротацию — функция есть, толку нет", G,
     [("    Rotate-BootLog $bOut\n    Rotate-BootLog $bErr\n", "")],
     f"{T}::test_the_launch_path_actually_rotates_before_it_redirects"),

    # Парная к предыдущей: одного вызова мало, трассировка живёт в stderr.
    ("ротируется только stdout — stderr по-прежнему обрезается", G,
     [("    Rotate-BootLog $bOut\n    Rotate-BootLog $bErr\n",
       "    Rotate-BootLog $bOut\n")],
     f"{T}::test_the_launch_path_actually_rotates_before_it_redirects"),
]


def write_mutant(path: Path, text: str) -> None:
    # BOM обязателен: PS 5.1 без него читает .ps1 как cp1251 и падает на
    # кириллице в комментариях — мутант не запустился бы вовсе, а гейт принял
    # бы это за пойманную мутацию.
    path.write_text("﻿" + text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed` в выводе: мутация, сломавшая СБОР,
    отвечает rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за
    пойманную, хотя ни одна проверка не выполнилась.

    encoding задан явно: `text=True` берёт cp1251, где байт `0x98` не
    определён, и одна буква в выводе упавшего теста печатает пойманную
    мутацию слепой.
    """
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
    """Откат идёт через `git checkout --`, то есть НЕЗАКОММИЧЕННОЕ он сотрёт."""
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
