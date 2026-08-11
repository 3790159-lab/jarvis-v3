"""DEV-26 для ops_watchdog: ломаем фикс обратно и требуем КРАСНОГО.

Отдельный харнесс от `mutate_payments_phase0.py`, потому что предмет другой:
здесь охраняется КАНАЛ АЛЕРТА, а не деньги. Цена ложного зелёного та же —
сторож, который молчит, неотличим от сторожа, которому нечего сказать.

Механика и предохранители — те же: точечная замена, откат через git, отказ
работать на грязном дереве.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WATCHDOG = "scripts/ops_watchdog.py"
T_EVAL = "tests/test_ops_watchdog.py"
T_TREE = "tests/test_ops_watchdog_tree.py"
T_CHAT = "tests/test_ops_watchdog_chatter.py"
T_SEC = "tests/test_ops_watchdog_secrets.py"

# (имя, файл, что заменить, на что, какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    # ── ядро дедупа ────────────────────────────────────────────────────────
    ("дедуп: смена причины не замечается", WATCHDOG,
     '            elif st["alerted_reason"] != reason:',
     "            elif False:",
     T_EVAL + "::test_a_second_different_reason_alerts_again"),

    ("дедуп: сравнивается текст алерта, а не причина", WATCHDOG,
     '        reason = str(res.get("reason") or check)',
     '        reason = str(res.get("detail") or check)',
     T_EVAL + "::test_a_changing_detail_with_a_stable_reason_stays_silent"),

    ("дедуп: причина без пробы считается каждый раз новой", WATCHDOG,
     '        reason = str(res.get("reason") or check)',
     '        reason = str(res.get("reason") or id(res))',
     T_EVAL + "::test_a_probe_without_a_reason_keeps_the_one_shot_behaviour"),

    ("миграция: старый стейт даёт шторм на выкатке", WATCHDOG,
     '            elif "alerted_reason" not in st:',
     "            elif False:",
     T_EVAL + "::test_legacy_state_without_a_reason_does_not_alert_on_upgrade"),

    ("восстановление: причина не забывается", WATCHDOG,
     '            st = {"fail": 0, "alerted": False}',
     '            st = {"fail": 0, "alerted": False,\n'
     '                  "alerted_reason": st.get("alerted_reason", "")}',
     T_EVAL + "::test_recovery_clears_the_remembered_reason"),

    ("окно загрузки: причина, о которой промолчали, считается объявленной",
     WATCHDOG,
     "                # Окно загрузки: считаем, но молчим.",
     '                st["alerted_reason"] = reason\n'
     "                # Окно загрузки: считаем, но молчим.",
     T_EVAL + "::test_suppressed_down_remembers_nothing"),

    ("текст: вторая причина выглядит вторым падением", WATCHDOG,
     '        return "🚨 Новая причина: %s. %s" % (label, detail)',
     '        return "🚨 DOWN: %s. %s" % (label, detail)',
     T_EVAL + "::test_the_second_alert_says_it_is_a_new_reason_not_a_new_outage"),

    # ── причины проб ───────────────────────────────────────────────────────
    ("дерево: причина — количество файлов, а не пути", WATCHDOG,
     '        reasons.append("dirty:" + "|".join(sorted(\n'
     '            line.strip().split(None, 1)[-1] for line in dirty)))',
     '        reasons.append("dirty:%d" % len(dirty))',
     T_TREE + "::test_the_file_count_alone_is_not_the_reason"),

    ("дерево: причина обрезана до DIRTY_SHOWN, как текст", WATCHDOG,
     "            line.strip().split(None, 1)[-1] for line in dirty)))",
     "            line.strip().split(None, 1)[-1] for line in dirty[:DIRTY_SHOWN])))",
     T_TREE + "::test_the_reason_survives_more_files_than_the_alert_shows"),

    ("дерево: статус-буквы git попали в причину", WATCHDOG,
     "            line.strip().split(None, 1)[-1] for line in dirty)))",
     "            line.strip() for line in dirty)))",
     T_TREE + "::test_the_same_dirty_file_keeps_the_same_reason"),

    ("дерево: ветка и грязь схлопнуты в одну причину", WATCHDOG,
     '        reasons.append("branch:%s" % branch)',
     '        reasons.append("dirty:")',
     T_TREE + "::test_wrong_branch_and_dirty_tree_are_different_reasons"),

    ("диск: гигабайты попали в причину", WATCHDOG,
     '            "reason": "low_space",',
     '            "reason": "low_space:%.1f" % free_gb,',
     T_EVAL + "::test_disk_reason_is_stable_while_free_space_drifts"),

    ("бэкенд: «не отвечает» и «отвечает 500» схлопнуты", WATCHDOG,
     '        "reason": "no_response" if status is None else "http:%s" % status,',
     '        "reason": "down",',
     T_EVAL + "::test_backend_down_and_backend_erroring_are_different_reasons"),

    ("раннер: «процесса нет» и «завис» схлопнуты", WATCHDOG,
     '        return {"ok": False, "reason": "stale_heartbeat",\n'
     '                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}\n'
     '    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (alive[0].get("pid"), beat_age)}',
     '        return {"ok": False, "reason": "no_process",\n'
     '                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}\n'
     '    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (alive[0].get("pid"), beat_age)}',
     T_CHAT + "::test_runner_gone_and_runner_frozen_are_different_reasons"),

    ("раннер: секунды heartbeat попали в причину", WATCHDOG,
     '        return {"ok": False, "reason": "stale_heartbeat",\n'
     '                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}\n'
     '    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (alive[0].get("pid"), beat_age)}',
     '        return {"ok": False, "reason": "stale:%.0f" % beat_age,\n'
     '                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}\n'
     '    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (alive[0].get("pid"), beat_age)}',
     T_CHAT + "::test_runner_reason_is_stable_while_the_beat_age_grows"),

    ("бандл: «нет копии» и «копия отстала» схлопнуты", WATCHDOG,
     '        reasons.append("no_bundle")', '        reasons.append("stale")',
     T_SEC + "::test_missing_bundle_and_stale_bundle_are_different_reasons"),

    ("бандл: дни отставания попали в причину", WATCHDOG,
     '        return {"ok": False, "reason": "stale", "detail":',
     '        return {"ok": False, "reason": "stale:%.1f" % lag_days, "detail":',
     T_SEC + "::test_stale_reason_is_stable_while_the_lag_grows"),
]


def run(test: str) -> bool:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    """Откат идёт через `git checkout --`, то есть незакоммиченные правки он
    сотрёт. Один раз этого уже хватило, чтобы потерять готовую работу."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def main() -> int:
    assert_clean()
    blind = []
    for name, rel, old, new, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент не найден" % name)
            blind.append((name, "фрагмент не найден"))
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            green = run(test)
        finally:
            revert(rel)
        if green:
            print("[СЛЕП] %s\n        %s остался ЗЕЛЁНЫМ" % (name, test))
            blind.append((name, test))
        else:
            print("[ok]   %s -> сторож покраснел" % name)
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
