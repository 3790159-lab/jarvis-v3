"""DEV-52: мутационный гейт на подсказку «кто съел память».

Предмет — отчёт ремня, который до этой правки называл виноватым ТЕКУЩИЙ ТЕСТ
там, где память съели снаружи. Сторож, не краснеющий на снятой подсказке,
вернул бы ровно ту потерю времени, ради которой всё делалось: 21.08 два часа,
23.08 двадцать минут ручного поиска пожирателя.

Спека: docs/superpowers/specs/2026-08-23-dev52-belt-names-the-eater.md

Прогон: python scripts/mutate_suite_ram_belt_top_rss.py (в worktree, дерево чистое)
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

# Уникальный mtime на каждую мутацию: без него интерпретатор берёт СТАРЫЙ
# байткод из __pycache__, и гейт зачитывает себе чужой прогон (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

M = "app/services/suite_ram_belt.py"
T = "tests/test_suite_ram_belt_top_rss.py"

MUTATIONS = [
    ("на REASON_FREE снова винят тест — весь предмет задачи снят", M,
     [('        blame = "  Память съели процессы, названные выше, — тест тут ни при чём."',
       '        blame = "  Смотреть надо тест, названный выше."')],
     f"{T}::test_pin1_free_branch_shows_processes_and_drops_the_blame_on_the_test"),

    ("на REASON_RSS подменена прежняя закрывающая строка", M,
     [('    if reason != REASON_FREE:\n'
       '        blame = "  Смотреть надо тест, названный выше."',
       '    if reason != REASON_FREE:\n'
       '        blame = "  Смотреть надо процессы, названные выше."')],
     f"{T}::test_pin2_rss_branch_keeps_the_old_closing_line_verbatim"),

    ("закрывающая строка снова слепа к отказу перебора — ссылка в никуда", M,
     [("    elif top_rss and top_rss_error is None:", "    elif top_rss:")],
     f"{T}::test_amendmentZ_error_outranks_a_delivered_list_for_the_closing_line_too"),

    ("«не снимали» склеено с «сняли, пусто» — три состояния стали двумя", M,
     [('    if top_rss is None:\n'
       '        return ["  список процессов не снимался"]',
       '    if top_rss is None:\n'
       '        return ["  перебор ничего не вернул"]')],
     f"{T}::test_pin4_none_and_empty_do_not_borrow_each_others_wording"),

    ("причина отказа перебора проглочена — отказ выглядит как «не снимали»", M,
     [('    if error is not None:\n'
       '        return ["  список процессов не снялся: %s" % error]\n', '')],
     f"{T}::test_amendmentA_error_outranks_the_not_sampled_state"),

    ("пометка «ЭТОТ ПРОГОН» снята — свой pytest не отличить от пожирателя", M,
     [('        mark = "  <- ЭТОТ ПРОГОН" if row.is_self else ""',
       '        mark = ""')],
     f"{T}::test_pin5_self_row_is_marked_in_its_own_line"),

    ("psutil затащен на уровень модуля — чистое ядро перестало быть чистым", M,
     [("import threading\nimport time\n",
       "import threading\nimport time\nimport psutil\n")],
     f"{T}::test_pin6_module_does_not_import_psutil_at_module_level"),

    ("отказ по ОДНОМУ процессу отменяет весь перебор", M,
     [("            except Exception:                 # noqa: BLE001\n"
       "                # Один процесс — не весь перебор.\n"
       "                continue\n",
       "            except Exception:                 # noqa: BLE001\n"
       "                raise\n")],
     f"{T}::test_pin7_access_denied_on_one_process_does_not_cancel_the_rest"),

    ("обрыв перебора летит наружу — отчёт теряется целиком", M,
     [("    except Exception as exc:                  # noqa: BLE001\n"
       "        if status is not None:\n"
       '            status["error"] = "%r" % (exc,)\n',
       "    except Exception as exc:                  # noqa: BLE001\n"
       "        raise\n")],
     f"{T}::test_pin7_failing_sweep_does_not_propagate_out_of_the_collector"),

    ("названо пять процессов вместо трёх — отчёт перестал влезать в экран", M,
     [("TOP_RSS_COUNT = 3", "TOP_RSS_COUNT = 5")],
     f"{T}::test_collector_sorts_by_rss_descending_and_keeps_exactly_three"),

    ("сортировка по возрастанию — названы самые МЕЛКИЕ", M,
     [("    rows.sort(key=lambda r: r.rss_gb, reverse=True)",
       "    rows.sort(key=lambda r: r.rss_gb)")],
     f"{T}::test_collector_sorts_by_rss_descending_and_keeps_exactly_three"),

    ("свой процесс больше не опознаётся", M,
     [("                    is_self=(pid == self_pid),",
       "                    is_self=False,")],
     f"{T}::test_collector_marks_the_running_pytest_as_self"),

    ("top_rss стал обязательным — прежние вызовы отчёта сломаны", M,
     [("                  top_rss: Optional[list] = None,",
       "                  top_rss: Optional[list],")],
     f"{T}::test_pin8_render_report_still_works_without_the_new_argument"),
    ("бюджет снят — перебор больше не ограничен по времени", M,
     [("TOP_RSS_BUDGET_S = 0.5", "TOP_RSS_BUDGET_S = 1e9")],
     f"{T}::test_amendmentB_top_rss_from_signature_is_literally_as_specified"),

    ("отметка об урезании не выставляется — неполный список выглядит полным", M,
     [("                if status is not None:\n"
       '                    status["truncated"] = True\n', "")],
     f"{T}::test_amendmentV_exhausted_budget_sets_truncated_and_hands_over_what_it_got"),

    ("Sampler перестал доносить подсказку до отчёта — шов разорван", M,
     [("                top_rss=top_rss,\n", "                top_rss=None,\n")],
     f"{T}::test_seam_sampler_carries_the_collected_rows_into_the_report"),

    ("падение самого collect_top валит замер целиком", M,
     [("                except Exception as exc:      # noqa: BLE001\n"
       '                    top_rss, top_err, top_cut = None, "%r" % (exc,), False\n',
       "                except Exception as exc:      # noqa: BLE001\n"
       "                    raise\n")],
     f"{T}::test_seam_sampler_does_not_die_when_collect_top_itself_explodes"),
]


def write_mutant(path: Path, text: str) -> None:
    # .py: BOM ЗАПРЕЩЁН (ast.parse краснеет на нём), и переводы строк обязаны
    # остаться LF — Path.write_text на Windows молча сделал бы CRLF и погасил
    # бы мутацию, а гейт зачёл бы это себе как пойманное.
    path.write_bytes(text.encode("utf-8"))
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: rc 2 это «сбор упал», а не «сторож покраснел», и засчитывать его
    значит поверить в сторожа, которого не запускали."""
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


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def assert_target_matches_code() -> None:
    """Мишени обязаны существовать В КОДЕ до всякой мутации.

    Иначе гейт «проходит» на разъехавшейся мишени: фрагмент не найден, мутация
    не применилась, сторож не краснел — и это выглядит как зелёное. Сверка
    стоит секунды и ловит тот класс, где код уехал, а гейт остался.
    """
    missing = []
    for name, rel, edits, _test in MUTATIONS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for old, _new in edits:
            if old not in text:
                missing.append((name, rel))
    if missing:
        lines = "\n".join("  %s -> %s" % (n, r) for n, r in missing)
        raise SystemExit(
            "ОТКАЗ: мишени мутаций разъехались с кодом, гейт ничего не "
            "доказывает:\n" + lines)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    assert_target_matches_code()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        mutated = text
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == text:
            print(f"[!] МУТАЦИЯ НЕ ИЗМЕНИЛА ФАЙЛ: {name}")
            blind.append((name, "файл не изменился"))
            continue
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
