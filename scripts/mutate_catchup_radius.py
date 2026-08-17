"""DEV-26 для замера радиуса переответа: снимаем предохранитель, требуем КРАСНОГО.

Предмет — правило «сначала замерь, потом поднимай», заведённое владельцем
17.08 после того, как рестарт Ярины ответил клиенту через 13 ч 49 мин поверх
человека, который вёл диалог. Правило потребовали положить в СКРИПТ, а не в
память; значит и сторожа обязаны стоять на скрипте.

Мутируются решения обоих концов: что считается риском (`chatter_catchup_
radius.py`) и что с этим делает дверь подъёма (`chatter_client.ps1`).

Прогон: python scripts/mutate_catchup_radius.py (в worktree, дерево чистое)
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

R = "scripts/chatter_catchup_radius.py"
C = "scripts/chatter_client.ps1"
TR = "tests/test_chatter_catchup_radius.py"
TC = "tests/test_chatter_client_start_measures_radius.py"

MUTATIONS = [
    # ── что считается риском ──────────────────────────────────────────────
    ("роль последнего сообщения больше не важна — тревога звучит всегда", R,
     [('        if role != "user":\n            continue\n', "")],
     f"{TR}::test_a_dialog_the_bot_already_answered_is_not_a_risk"),

    ("возрастной порог снят — тревога по диалогам, которых catch-up не тронет", R,
     [("        if age > max_age_seconds:\n"
       "            # Старше порога catch-up не тронет — и мы не тревожим зря.\n"
       "            continue\n", "")],
     f"{TR}::test_a_message_older_than_the_catchup_cap_is_not_a_risk"),

    ("пауза и взятый человеком диалог больше не считаются молчанием по решению", R,
     [('            "deliberate_silence": bool(\n'
       '                (state or "") in SILENT_BY_DECISION or paused or took_over),',
       '            "deliberate_silence": bool((state or "") in SILENT_BY_DECISION),')],
     f"{TR}::test_paused_and_human_took_over_count_as_deliberate_silence"),

    ("вердикт о паузе выведен заново — истёкший снуз стал вечным", R,
     [("        muted = is_muted({\"paused\": paused, \"pause_until\": pause_until},\n"
       "                         kill_switch=False, now=now)",
       "        muted = bool(paused)")],
     f"{TR}::test_an_expired_snooze_is_not_deliberate_silence"),

    ("снуз перестал считаться молчанием по решению вовсе", R,
     [("        muted = is_muted({\"paused\": paused, \"pause_until\": pause_until},\n"
       "                         kill_switch=False, now=now)",
       "        muted = False")],
     f"{TR}::test_an_unexpired_snooze_is_deliberate_silence"),

    ("порядок вывода потерян — главное больше не сверху", R,
     [('    return sorted(out, key=lambda r: (-int(r["deliberate_silence"]), r["age_hours"]))',
       "    return out")],
     f"{TR}::test_the_riskiest_dialogs_are_printed_first"),

    ("битая БД возвращает пустой список вместо отказа", R,
     [('    except sqlite3.Error as exc:\n'
       '        raise RadiusError(f"БД не читается ({path}): {exc}") from exc',
       "    except sqlite3.Error:\n        return []")],
     f"{TR}::test_a_measurement_that_did_not_happen_is_not_a_clean_one"),

    ("путь к БД без записи в реестре выводится своей формулой", R,
     [("        from chatter.telethon_run import derive_db_path\n\n"
       "        return root / derive_db_path(slug, root / \".secrets\")",
       '        return root / ".secrets" / f"{slug}_db.sqlite"')],
     f"{TR}::test_a_registry_without_an_explicit_db_falls_back_like_the_runner"),

    # ── что делает дверь подъёма ──────────────────────────────────────────
    ("несостоявшийся замер снова читается как разрешение", C,
     [("    if ($rc -eq 0) { return }", "    if ($rc -ne 1) { return }")],
     f"{TC}::test_a_measurement_that_cannot_run_stops_the_start"),

    ("подъём больше не зовёт замер — правило снова живёт в чьей-то памяти", C,
     [("            Assert-CatchupRadius -TargetSlug $Slug\n", "")],
     f"{TC}::test_start_refuses_when_catchup_would_answer_over_a_human"),

    ("отказ перестал называть выход", C,
     [('        Write-Host "  подними осознанно: -Action start -Force"',
       '        Write-Host "  подними позже"')],
     f"{TC}::test_start_refuses_when_catchup_would_answer_over_a_human"),

    # Парная: замер не имеет права запрещать ВСЁ — иначе его обойдут мимо
    # скрипта, и правило снова станет знанием.
    ("замер запрещает подъём всегда", C,
     [("    if ($rc -eq 0) { return }", "    if ($false) { return }")],
     f"{TC}::test_start_goes_through_when_there_is_nothing_to_re_answer"),
]


def write_mutant(path: Path, text: str) -> None:
    if path.suffix == ".ps1":
        # BOM обязателен: PS 5.1 без него читает .ps1 как cp1251 и падает на
        # кириллице — мутант не запустился бы вовсе, и гейт принял бы это за
        # пойманную мутацию.
        path.write_text("﻿" + text.lstrip("﻿"), encoding="utf-8")
    else:
        # А .py с BOM Python исполняет молча, зато ast.parse краснеет — зеркало
        # того же правила, поймано 17.08 полным гейтом.
        path.write_text(text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: мутация, сломавшая СБОР, отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    encoding задан явно — cp1251 не знает байт 0x98 и печатает пойманную
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
