"""DEV-26 для DEV-31: ломаем дисциплину гейтов обратно и требуем КРАСНОГО.

Предмет здесь — не поведение прода, а сам предохранитель: сторож, который не
краснеет на снятом предохранителе, охраняет ровно ничего. Поэтому мутации
бьют по решениям, а не по словам: сравнение путей, различение запуска и
упоминания, исключение себя, поведение при неотвечающем git, отказ гасить
настоящую правку.

Отдельно мутируются сами гейты: правило «спроси сторожа» и «читай вывод в
UTF-8» обязано держаться на КАЖДОМ из них, а не на том, где о нём вспомнили.

Прогон: python scripts/mutate_worktree_discipline.py (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

GUARD = "scripts/gate_guard.py"
NEWWT = "scripts/new_worktree.py"
COMPO = "scripts/worktree_composition.txt"
STOPALL = "scripts/mutate_panels_stopall.py"
LOGIN = "scripts/mutate_panels_login_form.py"
T = "tests/test_gate_discipline.py"

MUTATIONS = [
    # ── чем дерево отличается от соседнего каталога ───────────────────────
    ("сравнение путей вернулось к префиксу строки", GUARD,
     [('    return p == r or p.startswith(r.rstrip("\\\\/") + os.sep)',
       "    return p.startswith(r)")],
     f"{T}::test_a_sibling_directory_with_the_same_prefix_is_not_inside_the_tree"),

    ("относительное имя снова доклеивается к текущему каталогу", GUARD,
     [("    if not raw or not os.path.isabs(raw):\n        return False",
       "    if not raw:\n        return False")],
     f"{T}::test_a_process_whose_exe_is_a_bare_name_is_not_placed_inside_the_tree"),

    # ── запуск против упоминания ─────────────────────────────────────────
    ("кто принял аргумент — больше не важно (открыл = исполнил)", GUARD,
     [("        if not is_interpreter(exe, cmdline):\n            continue", "        pass")],
     f"{T}::test_a_file_merely_OPENED_from_the_tree_is_not_a_deployment"),

    ("интерпретатором считается кто угодно", GUARD,
     [("    return stem in INTERPRETERS", "    return True")],
     f"{T}::test_a_file_merely_OPENED_from_the_tree_is_not_a_deployment"),

    # Парная: различитель не имеет права закрыться совсем — гардиана он
    # обязан по-прежнему видеть.
    ("интерпретатором не считается никто", GUARD,
     [("    return stem in INTERPRETERS", "    return False")],
     f"{T}::test_a_guardian_launching_prod_from_the_tree_blocks_the_gate"),

    ("аргумент -File больше не читается", GUARD,
     [('        if str(tok).lower() == "-file" and i + 1 < len(toks):',
       "        if False and i + 1 < len(toks):")],
     f"{T}::test_a_guardian_launching_prod_from_the_tree_blocks_the_gate"),

    ("скрипт запуска не ищется вовсе", GUARD,
     [("    toks = list(cmdline or [])[1:]", "    return None\n    toks = list(cmdline or [])[1:]")],
     f"{T}::test_a_guardian_launching_prod_from_the_tree_blocks_the_gate"),

    # ── раннер без пути скрипта виден только по интерпретатору ────────────
    ("интерпретатор из дерева перестал считаться признаком", GUARD,
     [("        if under(exe, root):", "        if False:")],
     f"{T}::test_an_interpreter_from_the_tree_blocks_the_gate_even_without_a_script_path"),

    # ── свой процесс ─────────────────────────────────────────────────────
    ("исключение своих PID молча выброшено", GUARD,
     [("    skip = {pid for pid in exclude}", "    skip = set()")],
     f"{T}::test_the_gate_does_not_count_ITSELF_a_deployer"),

    # ── неотвечающий git ─────────────────────────────────────────────────
    ("git не ответил — гейт разрешает мутировать", GUARD,
     [("    if not git_dir or not common:\n        return True",
       "    if not git_dir or not common:\n        return False")],
     f"{T}::test_a_tree_git_cannot_be_asked_about_counts_as_the_main_one"),

    # ── лежащая ферма ────────────────────────────────────────────────────
    ("главное дерево при лежащей ферме объявлено безопасным", GUARD,
     [("    if is_main:", "    if False:")],
     f"{T}::test_the_main_worktree_is_refused_even_when_the_farm_is_down"),

    # Парная: отказ не имеет права стать вечным — worktree обязан работать.
    ("отказ стал вечным — worktree тоже запрещён", GUARD,
     [("    if is_main:", "    if True:")],
     f"{T}::test_a_worktree_with_no_live_process_is_allowed"),

    # ── отказ без выхода = приглашение его обойти ─────────────────────────
    ("отказ перестал называть выход", GUARD,
     [('        "  .venv\\\\Scripts\\\\python.exe scripts/new_worktree.py --name <имя> "\n'
       '        "--branch <ветка>\\n"',
       '        "  сделай отдельное дерево и повтори\\n"')],
     f"{T}::test_the_refusal_names_the_way_out"),

    # ── свежий worktree: артефакт против настоящей правки ─────────────────
    ("любая правка объявлена артефактом нормализации", NEWWT,
     [("    return bool(blob) and blob == disk", "    return True")],
     f"{T}::test_a_file_that_really_differs_is_NOT_an_artifact"),

    ("настоящая правка гасится skip-worktree молча", NEWWT,
     [("        if not is_normalization_artifact(rel, tree):", "        if False:")],
     f"{T}::test_a_real_difference_in_a_FRESH_worktree_stops_the_procedure"),

    ("статус-буквы git снова уезжают в путь", NEWWT,
     [('            out.append(line[3:].strip().strip(\'"\'))',
       "            out.append(line.strip())")],
     f"{T}::test_the_status_letters_are_stripped_from_the_path"),

    ("комментарии состава снова читаются путями", NEWWT,
     [('        line = line.split("#", 1)[0].strip()', "        line = line.strip()")],
     f"{T}::test_the_composition_list_is_parsed_without_comments"),

    # ── доставка секретов: ссылка против копии ───────────────────────────
    ("маркер доставки не читается — всё едет копией", NEWWT,
     [('        if line.startswith(LINK_MARK):', "        if False:")],
     f"{T}::test_files_with_live_keys_are_delivered_by_LINK_not_by_copy"),

    ("маркер доставки не срезается с пути", NEWWT,
     [('            out.append(("link", line[len(LINK_MARK):].strip()))',
       '            out.append(("link", line))')],
     f"{T}::test_the_delivery_marker_is_stripped_from_the_path"),

    ("подмена ссылки копией замолчана", NEWWT,
     [('            return "copy-вместо-link (%s)" % exc.__class__.__name__',
       '            return "link"')],
     f"{T}::test_a_link_that_cannot_be_made_falls_back_but_SAYS_so"),

    ("провал ссылки роняет процедуру вместо доставки файла", NEWWT,
     [("        except OSError as exc:\n"
       "            shutil.copy2(found, target)",
       "        except OSError as exc:\n"
       "            pass")],
     f"{T}::test_a_link_that_cannot_be_made_falls_back_but_SAYS_so"),

    # ── состав ───────────────────────────────────────────────────────────
    ("состав опустел — baseline снова снимается другим набором файлов", COMPO,
     [("chatter/clients/*/requisites.yaml", "# chatter/clients/*/requisites.yaml")],
     f"{T}::test_the_composition_names_the_file_whose_absence_reddened_22_tests"),

    ("в состав попал трекаемый путь — список перестал отвечать на свой вопрос",
     COMPO,
     [("chatter/clients/*/requisites.yaml",
       "chatter/clients/*/requisites.yaml\nCLAUDE.md")],
     f"{T}::test_the_composition_lists_only_gitignored_paths"),

    # ── правило держится на КАЖДОМ гейте ─────────────────────────────────
    ("один гейт забыл спросить сторожа", STOPALL,
     [("    refuse_if_live_tree(ROOT)\n", "")],
     f"{T}::test_every_mutation_gate_asks_the_guard_before_it_mutates"),

    ("один гейт снова читает вывод локальной кодировкой", LOGIN,
     [("        cwd=ROOT, capture_output=True, text=True,\n"
       '        encoding="utf-8", errors="replace")',
       "        cwd=ROOT, capture_output=True, text=True)")],
     f"{T}::test_every_gate_reads_pytest_output_in_utf8"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed` в выводе: мутация, сломавшая СБОР,
    отвечает rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за
    пойманную, хотя ни одна проверка не выполнилась.

    encoding задан явно: `text=True` берёт cp1251, где байт `0x98` не
    определён, а он приходит из «И» и «‘» — одна буква в выводе упавшего
    теста роняет читающий поток, и пойманная мутация печатается слепой.
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
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — это НЕ «ok»: она ничего не проверила.
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
