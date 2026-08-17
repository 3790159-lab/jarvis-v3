"""DEV-26 для правила «catch-up уважает намеренное молчание».

Предмет — единственное правило, у которого вред уже случился на живом
контакте: 17.08 рестарт заставил бота ответить клиенту через 13 ч 49 мин в
эскалированном диалоге, поверх человека. Сторож, который не краснеет на
снятом правиле, вернул бы ровно тот же случай, только молча.

Мутируются решения обеих сторон: чистое ядро (что пропускать и что при этом
говорить) и сборщик (какие состояния считаются молчанием ПО РЕШЕНИЮ).

Прогон: python scripts/mutate_catchup_silence.py (в worktree, дерево чистое)
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

R = "chatter/telethon_run.py"
T = "tests/chatter/test_telethon_run.py"

MUTATIONS = [
    # ── ядро: что пропускаем ──────────────────────────────────────────────
    ("правило снято — бот снова отвечает поверх человека", R,
     [('        if d.get("silent_by_decision"):', "        if False:")],
     f"{T}::test_C1_a_dialog_silent_by_decision_is_not_answered"),

    # Парная: пропуск не имеет права стать безусловным, иначе catch-up
    # перестанет делать то единственное, ради чего он есть.
    ("пропускаем ВСЁ — лид, написавший в лежащего бота, остался без ответа", R,
     [('        if d.get("silent_by_decision"):', "        if True:")],
     f"{T}::test_C2_an_ordinary_dialog_is_still_answered"),

    # ── ядро: что при этом говорим ────────────────────────────────────────
    ("пропуск стал молчаливым", R,
     [("            if on_skip is not None:\n"
       '                on_skip(d, str(d.get("silence_reason") or "silent_by_decision"))\n',
       "")],
     f"{T}::test_C6_every_skip_is_reported_with_its_reason"),

    ("проверка переехала ПЕРЕД отбором — журнал зарос пустыми пропусками", R,
     [('        if not picked:\n            continue\n        if d.get("silent_by_decision"):',
       '        if d.get("silent_by_decision"):')],
     f"{T}::test_a_skip_is_not_reported_for_a_dialog_that_had_nothing_to_answer"),

    # ── сборщик: какие состояния считаются решением ───────────────────────
    ("эскалация больше не считается молчанием по решению", R,
     [('        if (row or {}).get("state") == "escalated":\n            return (True, "escalated")\n',
       "")],
     f"{T}::test_C3_C4_the_collector_marks_escalated_takeover_and_pause"),

    ("вердикт о паузе выведен заново — истёкший снуз стал вечным", R,
     [('        if is_muted(row, kill_switch=False, now=time.time()):\n'
       '            return (True, "paused")',
       '        if (row or {}).get("paused"):\n            return (True, "paused")')],
     f"{T}::test_C5_an_expired_snooze_is_answered_again"),

    ("отказ БД глушит диалог — лид теряется тихо", R,
     [('            log.exception("catch-up: состояние контакта %s не прочиталось — "\n'
       '                          "считаю диалог обычным", sender_id)\n'
       '            return (False, "")',
       '            return (True, "unknown")')],
     f"{T}::test_a_state_that_cannot_be_read_does_not_silence_the_dialog"),

    # ── владелец узнаёт ───────────────────────────────────────────────────
    ("владельцу больше не говорят о пропущенном", R,
     [("        if skipped:\n            await self._notify_skipped_on_catchup(skipped)\n", "")],
     f"{T}::test_the_owner_is_told_once_about_everything_that_was_skipped"),

    ("уведомление уходит на каждом подъёме", R,
     [("        if skipped:\n            await self._notify_skipped_on_catchup(skipped)",
       "        await self._notify_skipped_on_catchup(skipped)")],
     f"{T}::test_no_notice_when_nothing_was_skipped"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text.lstrip("﻿"), encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: мутация, сломавшая СБОР, отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    encoding явный — cp1251 не знает байт 0x98 и печатает пойманную мутацию
    слепой.
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
