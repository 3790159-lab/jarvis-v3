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

⚠️ 15.08: у той же дыры была вторая половина — красным считался ЛЮБОЙ ненулевой
rc, поэтому мутация, сломавшая СБОР тестов (rc 2/4/5), тоже печаталась как
`[ok]`. Живой пример: мутация границы stdlib на копии дерева дала `rc=4`
(«found no collectors») — гейт принял бы её за пойманную. Теперь красным
считается РОВНО `rc 1` плюс `failed` в выводе, см. `run`.
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

    # ── ротация журнала: `journal_trim()` (§2.4) ───────────────────────────
    # Обрезка — единственное место, где сторож ТЕРЯЕТ данные, и потеря эта
    # молчалива по природе: файл просто становится короче. Поэтому под мутацию
    # ставится не только «режет ли», но и «называет ли, что именно отрезал».
    ("ротация: возраст записи не смотрится вовсе", WATCHDOG,
     "        elif (now - ts) > max_age_s:",
     "        elif False:",
     T_JOURNAL + "::test_records_older_than_thirty_days_are_dropped"),

    ("ротация: потолок по числу записей снят", WATCHDOG,
     "JOURNAL_MAX_RECORDS = 5000", "JOURNAL_MAX_RECORDS = 10**9",
     T_JOURNAL + "::test_the_record_ceiling_catches_a_restart_storm"),

    ("ротация: потолок режет первые ПО ФАЙЛУ, а не старые ПО ВРЕМЕНИ", WATCHDOG,
     "        order = sorted(range(len(kept)), key=lambda i: kept[i][0])\n"
     "        doomed = set(order[:by_count])\n"
     "        kept = [pair for i, pair in enumerate(kept) if i not in doomed]",
     "        kept = kept[by_count:]",
     T_JOURNAL + "::test_the_ceiling_drops_the_oldest_not_the_first_in_the_file"),

    ("ротация: потолок срабатывает РОВНО на потолке — маркер каждый раз",
     WATCHDOG,
     "    by_count = max(0, len(kept) - max_records)",
     "    by_count = max(0, len(kept) - max_records + 1)",
     T_JOURNAL + "::test_exactly_the_ceiling_is_not_a_reason_to_trim"),

    ("ротация режет молча — маркера о потере нет", WATCHDOG,
     '    return kept, dropped, " и ".join(parts)',
     '    return kept, dropped, ""',
     T_JOURNAL + "::test_records_older_than_thirty_days_are_dropped"),

    ("ротация называет только ПЕРВУЮ из причин", WATCHDOG,
     '    return kept, dropped, " и ".join(parts)',
     "    return kept, dropped, parts[0]",
     T_JOURNAL + "::test_both_limits_at_once_are_both_named_in_one_marker"),

    ("ротация докладывает о потере, которой не было", WATCHDOG,
     "    dropped = by_age + by_no_ts + by_count",
     "    dropped = len(records)",
     T_JOURNAL + "::test_trimming_nothing_reports_nothing"),

    ("ротация: запись без времени объявлена старой", WATCHDOG,
     "            by_no_ts += 1               # не «старая» — про неё нечего сказать",
     "            by_age += 1",
     T_JOURNAL + "::test_a_record_with_no_usable_time_is_dropped_but_not_called_old"),

    ("ротация: нечитаемое время роняет цикл сторожа целиком", WATCHDOG,
     "    try:\n        ts = float(ts)\n"
     "    except (TypeError, ValueError, OverflowError):\n        return None",
     "    ts = float(ts)",
     T_JOURNAL + "::test_a_record_with_no_usable_time_is_dropped_but_not_called_old"),

    ("ротация: время из будущего принято за самое старое", WATCHDOG,
     "        elif (now - ts) > max_age_s:",
     "        elif abs(now - ts) > max_age_s:",
     T_JOURNAL + "::test_a_timestamp_from_the_future_is_not_mistaken_for_an_ancient_one"),

    ("ротация: читаемое время в строке выброшено как нечитаемое", WATCHDOG,
     "    if ts is None or isinstance(ts, bool):",
     "    if ts is None or isinstance(ts, (bool, str)):",
     T_JOURNAL + "::test_a_timestamp_that_arrived_as_a_string_is_still_a_time"),

    # Ниже — мутации по итогам двух ревью: каждая пережила все 25 прежних
    # сторожей, то есть охраняла ротацию не она, а видимость.
    ("ротация: большое ЦЕЛОЕ время роняет цикл сторожа", WATCHDOG,
     "    except (TypeError, ValueError, OverflowError):",
     "    except (TypeError, ValueError):",
     T_JOURNAL + "::test_a_record_with_no_usable_time_is_dropped_but_not_called_old"),

    ("ротация: ±inf принят за время — бессмертная запись", WATCHDOG,
     "    if ts != ts or ts == _TS_INF or ts == -_TS_INF:",
     "    if ts != ts:",
     T_JOURNAL + "::test_a_record_with_no_usable_time_is_dropped_but_not_called_old"),

    ("ротация: порядок записей на выходе перевёрнут", WATCHDOG,
     "    kept = [rec for _ts, rec in kept]",
     "    kept = [rec for _ts, rec in reversed(kept)]",
     T_JOURNAL + "::test_the_ceiling_drops_the_oldest_not_the_first_in_the_file"),

    ("ротация заодно пересортировывает журнал по времени", WATCHDOG,
     "    kept = [rec for _ts, rec in kept]",
     "    kept = [rec for _ts, rec in sorted(kept, key=lambda p: p[0])]",
     T_JOURNAL + "::test_the_ceiling_drops_the_oldest_not_the_first_in_the_file"),

    ("ротация: маркер врёт про величину потолка", WATCHDOG,
     "                        max_records))",
     "                        0))",
     T_JOURNAL + "::test_the_record_ceiling_catches_a_restart_storm"),

    ("ротация: возраст режет РОВНО на границе 30 суток", WATCHDOG,
     "        elif (now - ts) > max_age_s:",
     "        elif (now - ts) >= max_age_s:",
     T_JOURNAL + "::test_a_record_exactly_thirty_days_old_is_not_old_yet"),

    ("ротация: маркер потерял слово «записей» из образца §2.4", WATCHDOG,
     '        parts.append("%d %s старше %d сут"\n'
     '                     % (by_age, _plural(by_age, "запись", "записи", "записей"),\n'
     "                        int(max_age_s // 86400)))",
     '        parts.append("%d старше %d сут" % (by_age, int(max_age_s // 86400)))',
     T_JOURNAL + "::test_the_marker_reads_like_the_sample_in_the_spec"),

    ("ротация: число и слово рядом с ним не согласованы", WATCHDOG,
     "    tail %= 10\n    if tail == 1:\n        return one\n"
     "    return few if 2 <= tail <= 4 else many",
     "    return many",
     T_JOURNAL + "::test_the_number_and_the_word_next_to_it_agree"),

    ("ротация: не-запись названа записью наравне с битым `ts`", WATCHDOG,
     '                     % (by_no_ts, _plural(by_no_ts, "строка", "строки", "строк")))',
     '                     % (by_no_ts, _plural(by_no_ts, "запись", "записи", "записей")))',
     T_JOURNAL + "::test_a_record_with_no_usable_time_is_dropped_but_not_called_old"),

    # ── дозапись, маркер ротации, маркер живости (Task 3) ──────────────────
    #
    # Врезка плана: маркер дописывается в ТОТ ЖЕ файл и, занимая слот потолка,
    # не вымывается никогда — потолок режет самые старые записи, а маркер
    # всегда самый свежий. Обе мутации ниже воспроизводят это копление.
    ("журнал: маркер ротации занимает слот потолка и вытесняет события",
     WATCHDOG,
     '        events = [r for r in on_disk if r.get("kind") != JOURNAL_ROTATED]',
     "        events = list(on_disk)",
     T_JOURNAL + "::test_at_the_ceiling_the_journal_does_not_turn_into_markers"),

    ("журнал: прежние маркеры переживают ротацию и копятся", WATCHDOG,
     "        kept, dropped, why = journal_trim(events, now, max_age_s, max_records)",
     "        kept, dropped, why = journal_trim(events, now, max_age_s, max_records)\n"
     '        kept = [r for r in on_disk if r.get("kind") == JOURNAL_ROTATED] + kept',
     T_JOURNAL + "::test_a_journal_full_of_old_markers_collapses_on_the_first_rotation"),

    # Б1/Б2: форму САМОГО маркера не проверял никто, кроме `kind` и `detail`.
    ("журнал: маркер несёт шестое поле сверх пяти из §2.2", WATCHDOG,
     '                         "reason": "trim",\n',
     '                         "reason": "trim", "pid": os.getpid(),\n',
     T_JOURNAL + "::test_the_marker_is_a_record_of_the_same_five_fields_and_of_no_probe"),

    ("журнал: маркер приписан чужой пробе — панель нарисует чужой инцидент",
     WATCHDOG,
     '"check": JOURNAL_SELF', '"check": "backend"',
     T_JOURNAL + "::test_the_marker_is_a_record_of_the_same_five_fields_and_of_no_probe"),

    ("журнал: маркер без нынешнего времени — окно 72 ч его не покажет никогда",
     WATCHDOG,
     '            kept.append({"ts": now, "check": JOURNAL_SELF',
     '            kept.append({"ts": 0.0, "check": JOURNAL_SELF',
     T_JOURNAL + "::test_the_marker_carries_the_time_of_now_not_a_constant"),

    ("журнал: маркер ставится на КАЖДОЙ дозаписи, а не при потере", WATCHDOG,
     "        if dropped:\n            kept.append(",
     "        if True:\n            kept.append(",
     T_JOURNAL + "::test_no_marker_appears_when_nothing_was_lost"),

    # Атомарность: `open(p, "w")` поверх живого журнала выглядит рабочим на
    # любом тесте, который не убивает процесс посреди перезаписи.
    ("журнал: перезапись поверх живого файла вместо атомарной замены", WATCHDOG,
     '            tmp = p.with_name(p.name + ".tmp")',
     "            tmp = p",
     T_JOURNAL + "::test_a_kill_between_the_temp_file_and_the_swap_loses_nothing"),

    ("журнал: `.tmp` от убитого прогона дописывается, а не перезаписывается",
     WATCHDOG,
     '            with open(tmp, "w", encoding="utf-8", newline="") as f:',
     '            with open(tmp, "a", encoding="utf-8", newline="") as f:',
     T_JOURNAL + "::test_a_leftover_tmp_from_an_aborted_run_is_overwritten"),

    ("журнал: шва после оборванной строки нет — гибнут ОБЕ записи", WATCHDOG,
     '            f.write(("\\n" if _needs_seam(p) else "") + "".join(lines))',
     '            f.write("".join(lines))',
     T_JOURNAL + "::test_a_torn_line_does_not_swallow_the_next_record"),

    ("журнал: строки режутся splitlines и рвутся по U+2028", WATCHDOG,
     '    for line in text.split("\\n"):',
     "    for line in text.splitlines():",
     T_JOURNAL + "::test_a_line_separator_inside_a_detail_does_not_split_the_record"),

    ("журнал: BOM съедает первую запись файла", WATCHDOG,
     '        line = line.strip(" \\t\\r\\ufeff")',
     "        line = line.strip()",
     T_JOURNAL + "::test_a_byte_order_mark_does_not_eat_the_first_record"),

    ("журнал: чтение останавливается на первой битой строке", WATCHDOG,
     "        except ValueError:\n            unreadable += 1\n            continue",
     "        except ValueError:\n            unreadable += 1\n            break",
     T_JOURNAL + "::test_reading_does_not_stop_at_the_first_broken_line"),

    # А2/B4: перезапись уносит не только посчитанное `journal_trim` — уходят
    # прежние маркеры и нечитаемые строки. Обе потери молчали.
    ("журнал: схлопнутые прежние отметки не названы — потеря занижена",
     WATCHDOG,
     "        collapsed = len(on_disk) - len(events)",
     "        collapsed = 0",
     T_JOURNAL + "::test_the_marker_does_not_understate_the_loss_fifty_fold"),

    ("журнал: стёртые перезаписью нечитаемые строки не названы", WATCHDOG,
     "        on_disk, unreadable = _journal_read_counted(p)",
     "        on_disk, unreadable = journal_read(p), 0",
     T_JOURNAL + "::test_lines_the_rewrite_erases_are_named_and_not_vanished"),

    ("журнал: строка-не-запись стирается перезаписью, но в маркер не попадает",
     WATCHDOG,
     "        else:\n            unreadable += 1             "
     "# `42` в файле записью не станет никогда",
     "        else:\n            pass",
     T_JOURNAL + "::test_lines_the_rewrite_erases_are_named_and_not_vanished"),

    ("журнал: число и слово в приписке маркера не согласованы", WATCHDOG,
     '                    % (unreadable, _plural(unreadable, "нечитаемая строка",',
     '                    % (unreadable, _plural(unreadable, "нечитаемых строк",',
     T_JOURNAL + "::test_one_unreadable_line_is_counted_in_the_singular"),

    # DEV-18: провал записи обязан быть виден и обязан вернуть False — маркер
    # живости обновляется ТОЛЬКО при True.
    ("журнал: провал дозаписи выдан за успех", WATCHDOG,
     '        print("[ops_watchdog] журнал не записан: %s" % exc, file=sys.stderr)\n'
     "        return False",
     "        return True",
     T_JOURNAL + "::test_a_failed_write_does_not_refresh_the_marker"),

    # А1: провал обрезки больше НЕ провал записи (возврат смягчён до `not lost`),
    # поэтому прежняя мутация «вернуть True» стала бы тождественной правке и
    # ослепла бы. Под мутацию встаёт то, что теперь несёт всю нагрузку, — ГОЛОС.
    ("журнал: провал обрезки замолчан — журнал растёт без единого слова",
     WATCHDOG,
     '        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)\n'
     "    return not lost",
     # `pass`, а не пустая строка: пустой `except` — это IndentationError,
     # модуль не грузится, и гейт получил бы rc 4 (сбор), а не rc 1 (красный
     # сторож). Ровно та подмена, из-за которой §В требует строгого критерия.
     "        pass\n"
     "    return not lost",
     T_JOURNAL + "::test_a_failing_swap_leaves_the_journal_whole"),

    ("журнал: провал обрезки снова выдан за провал записи — ложная 🚨 владельцу",
     WATCHDOG,
     '        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)\n'
     "    return not lost",
     '        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)\n'
     "        return False\n"
     "    return not lost",
     T_JOURNAL + "::test_a_failing_swap_leaves_the_journal_whole"),

    ("журнал: потеря записи амнистирована заодно с провалом обрезки", WATCHDOG,
     '        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)\n'
     "    return not lost",
     '        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)\n'
     "        return True\n"
     "    return not lost",
     T_JOURNAL + "::test_a_failing_swap_still_does_not_hide_a_lost_record"),

    ("журнал: потерянная запись выдана за записанную", WATCHDOG,
     "    return not lost",
     "    return True",
     T_JOURNAL + "::test_a_record_that_cannot_be_serialised_is_loud_and_not_lost_silently"),

    ("журнал: несериализуемая запись роняет цикл сторожа", WATCHDOG,
     "    except (TypeError, ValueError, RecursionError):",
     "    except OSError:",
     T_JOURNAL + "::test_a_record_that_cannot_be_serialised_is_loud_and_not_lost_silently"),

    ("журнал: не-запись записана и потеряна молча", WATCHDOG,
     "    if not isinstance(rec, dict):\n        return None",
     "    if False:\n        return None",
     T_JOURNAL + "::test_a_record_that_is_not_a_record_is_refused_by_the_writer"),

    ("журнал: экзотическое поле стоит всей записи о падении", WATCHDOG,
     ', default=repr) + "\\n"',
     ') + "\\n"',
     T_JOURNAL + "::test_an_exotic_value_travels_as_its_repr_instead_of_killing_the_cycle"),

    # А3: `skipkeys=True` стирал поле с нестроковым ключом МОЛЧА — единственная
    # молчаливая потеря во всём писателе, пережившая все 51 сторож.
    ("журнал: поле с нестроковым ключом снова стирается молча", WATCHDOG,
     ', default=repr) + "\\n"',
     ', default=repr,\n                          skipkeys=True) + "\\n"',
     T_JOURNAL + "::test_a_field_with_a_non_string_key_is_loud_instead_of_disappearing"),

    # А4: kill посреди кириллицы оставляет ПОЛОВИНУ UTF-8 последовательности,
    # а UnicodeDecodeError — подкласс ValueError: `except OSError` его не ловит.
    ("журнал: половина UTF-8 последовательности роняет цикл сторожа", WATCHDOG,
     '        text = Path(path).read_text(encoding="utf-8", errors="replace")',
     '        text = Path(path).read_text(encoding="utf-8")',
     T_JOURNAL + "::test_half_a_utf8_sequence_does_not_kill_the_reader"),

    ("журнал: пустой цикл всё равно ходит на диск", WATCHDOG,
     "    if not records:\n        return True",
     "    if False:\n        return True",
     T_JOURNAL + "::test_nothing_to_write_does_not_even_create_the_file"),

    ("маркер живости: аргумент `now` не доезжает до записи", WATCHDOG,
     "    stamp = time.time() if now is None else now",
     "    stamp = time.time()",
     T_JOURNAL + "::test_the_liveness_marker_is_touched_after_a_successful_write"),

    ("маркер живости: не записался и промолчал", WATCHDOG,
     '        print("[ops_watchdog] маркер живости не обновлён: %s" % exc,'
     " file=sys.stderr)\n        return False",
     "        return False",
     T_JOURNAL + "::test_the_marker_of_liveness_fails_loudly_too"),

    # Граница stdlib-only (§2.1, ловушка 1). Под pytest корень репозитория и так
    # на `sys.path`, поэтому мутация ниже НЕ ломает загрузку модуля и проходит
    # все прочие сторожа зелёной. Ловит её только подпроцесс без корня на пути.
    #
    # Фрагмент — ВЕРХ блока импортов, и он живой: Task 3 добавил `import os`
    # (нужен `os.replace`), прежний `import json\nimport re` перестал
    # находиться, и гейт молча считал бы мутацию неприменившейся.
    ("граница stdlib: сторож потянул app/", WATCHDOG,
     "import json\nimport os",
     "from app.services import jarvis_farm  # noqa: F401\nimport json\nimport os",
     T_JOURNAL + "::test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable"),

    # Б3: ЛЕНИВЫЙ импорт в шапке не виден — он виден только на вызове. Пока
    # подпроцесс звал одни `transitions`/`evaluate`, три мутации ниже проходили
    # бы гейт зелёными: под pytest корень репозитория и так на `sys.path`.
    ("граница stdlib: ленивый app/ внутри journal_append", WATCHDOG,
     "    now = time.time() if now is None else now\n    if not records:",
     "    now = time.time() if now is None else now\n"
     "    from app.services import jarvis_farm  # noqa: F401\n"
     "    if not records:",
     T_JOURNAL + "::test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable"),

    ("граница stdlib: ленивый app/ внутри чтения журнала", WATCHDOG,
     "    out, unreadable = [], 0",
     "    from app.services import jarvis_farm  # noqa: F401\n"
     "    out, unreadable = [], 0",
     T_JOURNAL + "::test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable"),

    ("граница stdlib: ленивый app/ внутри touch_beat", WATCHDOG,
     "    p = Path(path or JOURNAL_BEAT)",
     "    from app.services import jarvis_farm  # noqa: F401\n"
     "    p = Path(path or JOURNAL_BEAT)",
     T_JOURNAL + "::test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable"),
]


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — это РОВНО `rc 1` плюс `failed` в выводе, а не «любой ненулевой rc»
    (техдолг DEV-26, и он живой: мутация, сломавшая СБОР тестов, отвечает rc 2 /
    4 / 5 и печаталась бы как `[ok]`). Проверено фактом на копии дерева: мутация
    границы дала `rc=4` — «found no collectors» — и прежним критерием была бы
    принята за пойманную. Сторож, которого не существует, — худший вид зелёного:
    гейт отчитывается за него как за живого.

    `failed` в выводе, а не только rc: с `-q` pytest пишет итог строкой
    «N failed, M passed», и её отсутствие при rc 1 означает, что упал не тест.
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


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
            caught, answer = run(test)
        finally:
            revert(rel)
        if caught:
            print("[ok]   %s -> сторож покраснел" % name)
        else:
            # Ответ pytest печатается ЦЕЛИКОМ: «остался зелёным» и «сбор
            # сломался» — разные беды, и чинят их по-разному.
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, answer))
            blind.append((name, answer))
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
