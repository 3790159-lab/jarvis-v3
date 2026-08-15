"""DEV-26 для знаменателей дашборда: ломаем каждую правку обратно.

Здесь слепой сторож стоит особенно дорого: цифры на этом экране читает КЛИЕНТ,
и первое расхождение стоит доверия ко всему экрану. Мутации написаны так, чтобы
вернуть ровно те дефекты, что нашлись 12.08 по скриншотам с телефона.

Прогон: python scripts/mutate_panels_denominators.py (дерево должно быть чистым).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнится байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

MET = "app/services/tamapi_metrics.py"
TD = "app/routers/tamapi_dashboard.py"
T = "tests/chatter/test_panels_denominators.py"

# (имя, файл, [(что заменить, на что), ...], какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── воронка считает людей ────────────────────────────────────────────
    ("«кваліфіковано» снова считает СТРОКИ переходов", MET,
     [("""    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM funnel_transitions "
        "WHERE to_state='hot' AND ts >= ? AND ts < ?", (a, b))}""",
       """    return {"%s" % r["rowid"] for r in conn.execute(
        "SELECT rowid FROM funnel_transitions "
        "WHERE to_state='hot' AND ts >= ? AND ts < ?", (a, b))}""")],
     f"{T}::test_repeated_events_do_not_inflate_the_funnel"),

    ("«передано» снова считает КАРТОЧКИ", MET,
     [("""    return {r["contact_id"] for r in conn.execute(
        "SELECT DISTINCT contact_id FROM console_cards "
        "WHERE kind='escalation' AND ts >= ? AND ts < ?", (a, b))}""",
       """    return {"%s" % r["rowid"] for r in conn.execute(
        "SELECT rowid FROM console_cards "
        "WHERE kind='escalation' AND ts >= ? AND ts < ?", (a, b))}""")],
     f"{T}::test_repeated_events_do_not_inflate_the_funnel"),

    ("когорта перестала ограничивать ступени", MET,
     [("        qualified = None if hot is None else float(len(cohort & hot))",
       "        qualified = None if hot is None else float(len(hot))")],
     f"{T}::test_events_outside_the_cohort_do_not_count"),

    ("оплаты считаются мимо когорты", MET,
     [("        pays = None if paid is None else float(len(cohort & paid))",
       "        pays = None if paid is None else float(len(paid))")],
     f"{T}::test_no_step_can_exceed_the_cohort"),

    ("доля снова считается от ПРЕДЫДУЩЕЙ ступени", TD,
     [("        if i and val is not None and cohort:\n"
       "            pct = f\"<div class='p'>{val / cohort * 100:.0f}%</div>\"",
       "        if i and val is not None and steps[i-1][1]:\n"
       "            pct = f\"<div class='p'>{val / steps[i-1][1] * 100:.0f}%</div>\"")],
     f"{T}::test_percent_is_the_share_of_the_cohort"),

    # ── нагрузка ─────────────────────────────────────────────────────────
    ("блок нагрузки считает людей, а не события", MET,
     [('    cards = conn.execute(\n'
       '        "SELECT COUNT(*) AS n FROM console_cards "',
       '    cards = conn.execute(\n'
       '        "SELECT COUNT(DISTINCT contact_id) AS n FROM console_cards "')],
     f"{T}::test_load_block_counts_events_not_people"),

    ("блок нагрузки пропал со страницы", TD,
     [("<h2>Навантаження · 7 днів</h2>\n<div class='card'>{_load_html(sm)}</div>\n\n", "")],
     f"{T}::test_the_screen_has_a_separate_load_block"),

    # ── пакет ────────────────────────────────────────────────────────────
    ("пакет вернулся к скользящему окну 30 дней", TD,
     [("    since = _month_start(now)", "    since = now - 30 * 86400")],
     f"{T}::test_package_counts_the_calendar_month_not_a_rolling_window"),

    ("доля перерасхода обнулена — полоске нечего рисовать", TD,
     [('            "over_pct": (over / limit * 100.0) if limit else 0.0,',
       '            "over_pct": 0.0,')],
     f"{T}::test_package_reports_overflow_share"),

    ("сегмент перерасхода не рисуется", TD,
     [("    over_seg = (f\"<b class='over' style='width:{min(pkg['over_pct'], 100):.0f}%'></b>\"\n"
       "                if pkg[\"over\"] else \"\")",
       '    over_seg = ""')],
     f"{T}::test_package_overflow_is_drawn_not_clamped"),

    ("подпись пакета снова «діалогів» — как у воронки", TD,
     [("{pkg['used']} / {pkg['limit']} унікальних лідів цього місяця",
       "{pkg['used']} / {pkg['limit']} діалогів")],
     f"{T}::test_funnel_and_package_are_named_differently"),

    ("первая ступень снова «Діалоги» без окна", TD,
     [('    steps = [("Ліди", f["dialogs"], "унікальні за 7 днів", ""),',
       '    steps = [("Діалоги", f["dialogs"], "", ""),')],
     f"{T}::test_funnel_and_package_are_named_differently"),

    # ── «Требує вас» ─────────────────────────────────────────────────────
    ("порог свежести раздут до месяца", MET,
     [("STALE_AFTER = 48 * 3600.0", "STALE_AFTER = 30 * 86400.0")],
     f"{T}::test_card_older_than_48h_is_stale"),

    ("застарелые снова лежат вперемешку со свежими", TD,
     [("    fresh = [i for i in items if not i.get(\"stale\")]\n"
       "    stale = [i for i in items if i.get(\"stale\")]",
       "    fresh = list(items)\n    stale = []")],
     f"{T}::test_stale_cards_collapse_into_a_counter"),

    ("мёртвый лид перестал помечаться в данных", MET,
     [('                "dead": bool(who is not None\n'
       '                             and (who["state"] or "") in TERMINAL_STATES),',
       '                "dead": False,')],
     f"{T}::test_dead_lead_with_an_open_card_is_flagged"),

    ("пометка мёртвого лида убрана со свежей карточки", TD,
     [('    dead = f" · <span class=\'wait\'>{DEAD_MARK}</span>" if it.get("dead") else ""',
       '    dead = ""')],
     f"{T}::test_dead_lead_is_marked_on_the_screen"),

    ("в свёртке застарелых пометка урезана до короткой", TD,
     [('            + (f" · {DEAD_MARK}" if it.get("dead") else "")',
       '            + (" · лід мертвий" if it.get("dead") else "")')],
     f"{T}::test_dead_lead_is_marked_the_same_way_when_stale"),

    ("второй возраст («чекає») исчез с карточки", TD,
     [("f\"<span class='sub'>підняв руку {esc(ago(it['card_ts']))}\"\n"
       "        f\" · чекає {esc(ago(it['last_ts']))}{dead}</span></div>\"",
       "f\"<span class='sub'>підняв руку {esc(ago(it['card_ts']))}{dead}</span></div>\"")],
     f"{T}::test_fresh_card_shows_the_age_of_the_last_inbound"),

    ("возраст последнего входящего выброшен из данных", MET,
     [('                "last_ts": (last["ts"] if last else None),',
       '                "last_ts": None,')],
     f"{T}::test_item_carries_both_ages"),

    # ── длительность ─────────────────────────────────────────────────────
    ("округление до десятых ДНЯ вернулось", MET,
     [("    return statistics.median(spans) if spans else None",
       "    return round(statistics.median(spans), 1) if spans else None")],
     f"{T}::test_ten_minute_dialog_is_not_rounded_to_zero"),

    ("основание выборки не считается", MET,
     [("            basis = _BASIS.get(key)", "            basis = None")],
     f"{T}::test_duration_series_reports_its_basis"),

    ("основание выборки не доезжает до плитки", TD,
     [("            if s.basis is not None:", "            if False:")],
     f"{T}::test_duration_tile_shows_the_basis"),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> bool:
    """True = тест зелёный.

    Кодировка задана ЯВНО: `text=True` берёт cp1251, где байт `0x98` НЕ
    ОПРЕДЕЛЁН, а он приходит из «И» (`D0 98`) и «‘» (`E2 80 98`). Одна
    заглавная «И» в выводе упавшего теста роняет читающий поток
    `UnicodeDecodeError`, вывод приходит пустым — и вердикт гейта меняется
    от буквы в чужом тексте ассерта (замерено 15.08 на mutate_ops_watchdog:
    «38 слепых из 100» при краснеющих сторожах).
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return p.returncode == 0


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
            green = run(test)
        finally:
            revert(rel)
        if green:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
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
