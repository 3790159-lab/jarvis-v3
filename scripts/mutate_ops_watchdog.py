"""DEV-26 для ops_watchdog: ломаем фикс обратно и требуем КРАСНОГО.

Отдельный харнесс от `mutate_payments_phase0.py`, потому что предмет другой:
здесь охраняется КАНАЛ АЛЕРТА, а не деньги. Цена ложного зелёного та же —
сторож, который молчит, неотличим от сторожа, которому нечего сказать.

Механика и предохранители — те же: точечная замена, откат через git, отказ
работать на грязном дереве.

⚠️ 13.08: здесь стояла дыра DEV-26, которую до того чинили в платежах. Все
семнадцать мутаций бьют в ОДИН файл, поэтому окно совпадения тут самое широкое
из всех гейтов: две мутации одинакового размера, записанные в одну секунду,
неотличимы для кэша байткода, и вторая исполняется байткодом первой. Гейт при
этом печатает `[ok]`. Цифры прогонов до этой правки недостоверны.
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер исходника).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

WATCHDOG = "scripts/ops_watchdog.py"
T_EVAL = "tests/test_ops_watchdog.py"
T_TREE = "tests/test_ops_watchdog_tree.py"
T_CHAT = "tests/test_ops_watchdog_chatter.py"
T_SEC = "tests/test_ops_watchdog_secrets.py"
T_JOURNAL = "tests/test_panel_event_journal.py"

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

    ("дерево: причина не называет ветку", WATCHDOG,
     '        reasons.append("branch:%s" % branch)',
     '        reasons.append("branch")',
     T_TREE + "::test_two_different_wrong_branches_are_different_reasons"),

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

    # ── переходы журнала: `transitions()` ──────────────────────────────────
    # 14.08 гейт целился ТОЛЬКО в дедуп и в причины проб: ни `fire()`, ни `ts`,
    # ни `ALERTING_KINDS` не были под мутацией, хотя докстринг `evaluate()`
    # обещал «на них стоят тесты и мутационный гейт». Пять мутаций ниже
    # переживали все 138 сторожей — значит проверялось не то.
    ("переход: падение записано видом `changed`", WATCHDOG,
     '                    fire(check, "down", res, reason)',
     '                    fire(check, "changed", res, reason)',
     T_JOURNAL + "::test_a_down_transition_carries_structure_not_text"),

    ("переход: подавленное падение записано видом `down`", WATCHDOG,
     '                    fire(check, "suppressed", res, reason)',
     '                    fire(check, "down", res, reason)',
     T_JOURNAL + "::test_a_suppressed_fall_is_a_transition_but_not_an_alert"),

    ("переход: `ts` — константа, а не показание часов", WATCHDOG,
     '        t = {"ts": now, "check": check, "kind": kind,',
     '        t = {"ts": 0.0, "check": check, "kind": kind,',
     T_JOURNAL + "::test_the_timestamp_comes_from_the_clock_and_is_not_a_constant"),

    ("переход: лишнее поле сверх пяти из §2.2", WATCHDOG,
     '             "reason": reason, "detail": res.get("detail", "")}',
     '             "reason": reason, "detail": res.get("detail", ""), "hb_age": 0}',
     T_JOURNAL + "::test_a_transition_carries_exactly_the_five_fields_and_no_sixth"),

    ("`suppressed` попал в ALERTING_KINDS — молчание стало алертом", WATCHDOG,
     'ALERTING_KINDS = ("down", "recovered", "changed")',
     'ALERTING_KINDS = ("down", "recovered", "changed", "suppressed")',
     T_JOURNAL + "::test_a_suppressed_fall_is_a_transition_but_not_an_alert"),

    ("окно загрузки: дедуп `suppressed` снят — запись каждый цикл", WATCHDOG,
     '                if not st.get("alerted") and st["fail"] == debounce:',
     '                if not st.get("alerted") and st["fail"] >= debounce:',
     T_JOURNAL + "::test_one_fall_in_the_boot_window_writes_exactly_two_records"),

    ("окно загрузки: `suppressed` пишется о том, про что уже сказали", WATCHDOG,
     '                if not st.get("alerted") and st["fail"] == debounce:',
     '                if st["fail"] == debounce:',
     T_JOURNAL + "::test_a_fall_the_owner_already_heard_about_is_not_written_as_suppressed"),

    ("исход «поднялось само» не пишется в журнал", WATCHDOG,
     '            elif isinstance(st.get("fail"), (int, float)) and st["fail"] > 0:',
     "            elif False:",
     T_JOURNAL + "::test_a_suppressed_fall_that_came_back_up_leaves_its_outcome_in_the_journal"),

    ("подъём после ПОДАВЛЕННОГО падения ушёл алертом владельцу", WATCHDOG,
     '                fire(check, "recovered", res, reason, journal_only=True)',
     '                fire(check, "recovered", res, reason)',
     T_JOURNAL + "::test_the_owner_hears_no_recovery_of_a_fall_he_was_never_told_about"),

    # Граница stdlib-only (§2.1, ловушка 1). Под pytest корень репозитория и так
    # на `sys.path`, поэтому мутация ниже НЕ ломает загрузку модуля и проходит
    # все прочие сторожа зелёной. Ловит её только подпроцесс без корня на пути.
    ("граница stdlib: сторож потянул app/", WATCHDOG,
     "import json\nimport re",
     "from app.services import jarvis_farm  # noqa: F401\nimport json\nimport re",
     T_JOURNAL + "::test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable"),
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


def write_mutant(path: Path, text: str) -> None:
    """Записать мутанта и выдать ему СВОЙ mtime.

    Без подписи два мутанта одинакового размера в одну секунду делят один
    байткод, и второй прогон проверяет первый код. Сторож `[ok]` — ложный.
    """
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


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
        write_mutant(path, text.replace(old, new, 1))
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
