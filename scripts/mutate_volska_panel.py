# -*- coding: utf-8 -*-
"""Мутационный гейт арки «второй инстанс клиентской панели (volska)» (§6 спеки).

Мишени — РЕШЕНИЯ спеки, а не слова кода: порт из реестра против литерала, база
из реестра против слага, отсутствие объявления против умолчания, `no_instance`
против зелёного, конфликт портов против молчания, имя задачи из слага против
константы.

Сторожей писал ДРУГОЙ автор, от текста спеки, реализации не видя. Гейт
проверяет не код, а ИХ: ломаем решение и требуем, чтобы покраснел НАЗВАННЫЙ
сторож — `pytest файл::имя`, а не файл целиком. Прогон по файлу зеленел бы за
счёт соседа, и карта «решение -> сторож» врала бы при зелёном гейте.

🔴 ГЛАВНОЕ ПРО МИШЕНЬ §3b. Мутация базы обязана проверяться на VOLSKA: у
yarina выведенное из слага имя СОВПАДАЕТ с объявленным, и та же самая мутация
там не ловится ничем. Сторож на совпадающем слаге был бы зелен по построению
ровно в том месте, ради которого писалась поправка.

Правки пишутся БАЙТАМИ. `Path.write_text` под Windows перевёл бы LF-файл в CRLF
целиком, и «мутация» превратилась бы в правку всего файла. `.ps1` тут CRLF и с
BOM — фрагменты для него берутся в той же форме, иначе не найдутся.

Прогон: python scripts/mutate_volska_panel.py  (в worktree, дерево чистое)
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

RPC = "scripts/run_panel_client.py"
CR = "chatter/core/client_registry.py"
OW = "scripts/ops_watchdog.py"
REG = "scripts/register_panel_client_guardian.ps1"
TE = "app/services/task_encoding.py"
YML = "chatter/clients/registry.yaml"

GDB = "tests/test_volska_panel_db_from_registry.py"
GPORT = "tests/test_volska_panel_port_from_registry.py"
GNI = "tests/test_volska_panel_no_instance_verdict.py"
GCONF = "tests/test_volska_panel_port_conflict.py"
GTASK = "tests/test_volska_panel_tasks_match_registry.py"
GONE = "tests/test_panel_client_port_one_number.py"

MUTATIONS = [
    # ── §3b: база из реестра, а не из слага ───────────────────────────────
    ("база инстанса снова выводится из слага", RPC,
     [(b'"TAMAPI_DB": _abs_db(db_from_registry(slug, root=root), root),',
       b'"TAMAPI_DB": str(root / ".secrets" / ("%s.db" % slug)),')],
     GDB + "::test_the_db_of_a_diverging_slug_is_the_one_the_registry_declares"),

    ("необъявленная база подменяется умолчанием по слагу", RPC,
     [(b'                return entry.db if entry.db_declared else None',
       b'                return entry.db')],
     GDB + "::test_a_slug_without_a_db_line_is_refused"),

    ("пустое `db:` снова считается объявлением", CR,
     [(b'db_declared=bool(str(cfg.get("db") or "").strip()),',
       b'db_declared=cfg.get("db") is not None,')],
     GDB + "::test_an_empty_db_value_is_refused_too"),

    ("нечитаемый реестр отвечает умолчанием вместо отказа", RPC,
     [(b'                return entry.db if entry.db_declared else None\n'
       b'    except Exception:\n        return None',
       b'                return entry.db if entry.db_declared else None\n'
       b'    except Exception:\n        return str(root / ".secrets" / ("%s.db" % slug))')],
     GDB + "::test_a_registry_that_cannot_be_read_is_a_refusal_not_a_default"),

    # ── §6.7: два инстанса не делят порт ──────────────────────────────────
    ("конфликт портов панели не проверяется вовсе", CR,
     [(b'    for port, group in port_groups.items():', b'    for port, group in []:')],
     GCONF + "::test_two_enabled_clients_on_one_port_are_rejected"),

    ("жалоба о конфликте перестала называть сам порт", CR,
     [(b'f"registry conflict: panel port {port} shared with "',
       b'f"registry conflict: panel instances shared with "')],
     GCONF + "::test_the_complaint_names_the_port_the_two_share"),

    ("конфликтующие клиенты всё равно признаны runnable", CR,
     [(b'            bad.add(e.slug)\n\n    for e in enabled:',
       b'            pass\n\n    for e in enabled:')],
     GCONF + "::test_neither_side_of_the_port_conflict_is_runnable"),

    # ── §5.5 / §6.5: одна задача — один клиент ────────────────────────────
    ("имя задачи снова константа (вторая регистрация затрёт первую)", REG,
     [(b'    $TaskName = $BaseTaskName + $Slug.Substring(0,1).ToUpperInvariant()'
       b' + $Slug.Substring(1)',
       b'    $TaskName = $BaseTaskName')],
     GTASK + "::test_the_registrar_derives_the_task_name_from_the_slug"),

    ("вторая панельная задача выпала из литеральной таблицы", TE,
     [(b'    "JarvisPanelClientGuardianVolska": PROTECTION_PS_CONSOLE,\n', b'')],
     GTASK + "::test_the_number_of_guardian_tasks_equals_the_number_of_panel_sections"),

    # ── §6.1: порт из реестра, а не из литерала ───────────────────────────
    ("запуск снова садится на литеральное умолчание", RPC,
     [(b'        a.port = port_from_registry(a.slug) or DEFAULT_PORT',
       b'        a.port = DEFAULT_PORT')],
     GPORT + "::test_two_slugs_launched_in_turn_bind_two_different_ports"),
    # ↑ МИШЕНЬ ПЕРЕЦЕЛЕНА ДВАЖДЫ, и второй раз — уже после того, как автор
    # сторожей закрыл дыру. Первые два адреса (`..._resolves_a_slug_port...`
    # и `..._moves_the_launcher`) оставались ЗЕЛЁНЫМИ, и оба справедливо:
    # они зовут резолвер НАПРЯМУЮ, а мутация ломает место, где запуск этот
    # резолвер СПРАШИВАЕТ. Резолвер цел — его просто не вызывают, и снаружи
    # разницы нет. Красное обязан дать тот, кто меряет ИСХОД: порт, с
    # которым запуск дошёл до `uvicorn.run`.

    # ── §6.2: слаг без секции — КРАСНОЕ, а не зелёное ─────────────────────
    ("слаг без секции `panel` признан имеющим инстанс", OW,
     [(b'        has_instance = slug_port is not None', b'        has_instance = True')],
     GNI + "::test_the_reason_is_exactly_no_instance[escalation-escalation:]"),

    # ── §6.4: литеральный пин портов В ОБЕ СТОРОНЫ ────────────────────────
    ("секция `panel` volska исчезла из реестра", YML,
     [(b'    panel:\n      port: 8012\n', b'')],
     GONE + "::test_the_registry_declares_the_two_ports_the_spec_named_out_loud[volska-8012]"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`."""
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
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
         "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str, original: bytes) -> None:
    """Вернуть СОХРАНЁННЫЕ БАЙТЫ, а не `git checkout`.

    Шаблон гейта откатывал через git, и на `registry.yaml` это сорвалось:
    checkout отдал файл в CRLF там, где на диске был LF, — байты разошлись,
    хотя git считал дерево чистым (нормализация). Прогон умер на середине с
    «ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО», а мишень была законная.

    Запись сохранённых байт побайтова ПО ПОСТРОЕНИЮ и не зависит ни от
    .gitattributes, ни от autocrlf. Проверка после отката всё равно остаётся:
    она ловит уже не концы строк, а чужую руку в дереве.
    """
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
        # ФРАГМЕНТ ИЩЕТСЯ В ОБЕИХ ФОРМАХ КОНЦОВ СТРОК. В дереве живут и
        # LF-файлы, и CRLF (`.ps1` — ещё и с BOM), а `registry.yaml` git
        # отдаёт в CRLF при autocrlf=true. Мишень, записанная одной формой,
        # на другой просто «не найдётся» — и это выглядело бы оплошностью
        # автора гейта, хотя решение спеки цело. Ищем как есть; не нашли —
        # пробуем ту же строку с CRLF.
        edits = [(o, n) if o in mutated
                 else (o.replace(b"\n", b"\r\n"),
                       n.replace(b"\n", b"\r\n"))
                 for o, n in edits]
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # Не применившаяся мутация — НЕ «ok»: она не проверила ничего.
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
        after = path.read_bytes()
        if after != original:
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
    print("Все %d мутаций пойманы, файл возвращён побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
