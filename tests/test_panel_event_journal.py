# -*- coding: utf-8 -*-
"""Журнал событий панели: писатель (сторона ops_watchdog).

Спека: docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §2-§3.

⚠️ Тесты здесь НЕ имеют права тянуть app/ или chatter/: ops_watchdog standalone
и stdlib-only by design — он обязан работать, когда мёртво окружение бэкенда.
Импорт тяжёлого пакета в тесте не сломает прод, но скроет нарушение границы в
самом сторожевом коде. Комментарий, впрочем, сторожем не является — им является
`test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable`.
"""
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ops_watchdog_under_test", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_spec)
# В `sys.modules` модуль НЕ регистрируется намеренно: загрузка по пути работает
# и без этого (так же грузят три соседних test_ops_watchdog*.py), а протечка
# через `sys.modules` в этом репозитории — корень отдельного класса аварий.
_spec.loader.exec_module(ow)

# §2.2 называет РОВНО пять полей и прямым текстом запрещает PID, hb_age и
# age_days: они меняются каждый цикл и не значат ничего.
JOURNAL_FIELDS = {"ts", "check", "kind", "reason", "detail"}


def _probe(ok, reason="r", detail="d"):
    return {"ok": ok, "detail": detail, "reason": reason}


def _one_of_each_kind():
    """По одному переходу каждого вида — для сторожей на общий формат записи."""
    fresh = {"web": {"fail": 1, "alerted": False}}
    alerted = {"web": {"fail": 3, "alerted": True, "alerted_reason": "old"}}
    return {
        "down": ow.transitions(fresh, {"web": _probe(False)}, debounce=2)[0][0],
        "recovered": ow.transitions(alerted, {"web": _probe(True)}, debounce=2)[0][0],
        "changed": ow.transitions(alerted, {"web": _probe(False, "new")},
                                  debounce=2)[0][0],
        "suppressed": ow.transitions(fresh, {"web": _probe(False)}, debounce=2,
                                     suppress_down=True)[0][0],
    }


def test_a_down_transition_carries_structure_not_text():
    """Журналу нужны check/kind/reason/ts, а не готовая фраза с эмодзи:
    по тексту нельзя ни отсортировать, ни сгруппировать, ни схлопнуть."""
    st = {"backend": {"fail": 1, "alerted": False}}
    trs, _new = ow.transitions(st, {"backend": _probe(False, "no_response", "нет ответа")},
                               debounce=2)
    assert len(trs) == 1, trs
    t = trs[0]
    assert t["check"] == "backend"
    assert t["kind"] == "down"
    assert t["reason"] == "no_response"
    assert t["detail"] == "нет ответа"
    assert isinstance(t["ts"], float)


def test_evaluate_still_returns_the_same_alert_texts():
    """`evaluate()` под мутационным гейтом: её поведение обязано остаться
    байт-в-байт тем же, иначе рефакторинг заплатит алертами владельцу."""
    st = {"backend": {"fail": 1, "alerted": False}}
    alerts, _new = ow.evaluate(st, {"backend": _probe(False, "no_response", "нет ответа")},
                               debounce=2)
    assert alerts == ["🚨 DOWN: BACKEND (:8010 /health). нет ответа"]


def test_a_suppressed_fall_is_a_transition_but_not_an_alert():
    """Загрузочное окно: считаем, но молчим. В журнал это ОБЯЗАНО попасть —
    событие, о котором владельцу не сообщили, и есть то, ради чего журнал
    заводится (DEV-18: подавление не молчит)."""
    st = {"backend": {"fail": 1, "alerted": False}}
    probes = {"backend": _probe(False, "no_response")}
    trs, _new = ow.transitions(st, probes, debounce=2, suppress_down=True)
    assert [t["kind"] for t in trs] == ["suppressed"]

    alerts, _new2 = ow.evaluate(st, probes, debounce=2, suppress_down=True)
    assert alerts == [], "о подавленном падении отправлен алерт"


def test_recovered_and_changed_are_distinct_kinds():
    """Три разных перехода — три разных вида записи. Слить `changed` с `down`
    значит вернуть дефект 2026-08-11: вторая причина не звучала вовсе."""
    alerted = {"web": {"fail": 3, "alerted": True, "alerted_reason": "old"}}
    trs, _ = ow.transitions(alerted, {"web": _probe(True)}, debounce=2)
    assert [t["kind"] for t in trs] == ["recovered"]

    trs2, _ = ow.transitions(alerted, {"web": _probe(False, "new")}, debounce=2)
    assert [t["kind"] for t in trs2] == ["changed"]


# ── формат записи: §2.2 ────────────────────────────────────────────────────
def test_the_timestamp_comes_from_the_clock_and_is_not_a_constant():
    """`assert isinstance(ts, float)` пропускает константу `0.0`, а журнал по
    `ts` сортируется, группируется по суткам (§4.2) и режется ротацией «старше
    30 суток» (§2.4). Константа сломала бы всё три, оставшись зелёной."""
    before = time.time()
    trs, _ = ow.transitions({"backend": {"fail": 1, "alerted": False}},
                            {"backend": _probe(False)}, debounce=2)
    after = time.time()
    assert isinstance(trs[0]["ts"], float)
    assert before <= trs[0]["ts"] <= after, (before, trs[0]["ts"], after)


def test_every_kind_carries_reason_and_detail_not_just_down():
    """`reason` — ключ группировки §2.2, `detail` у `suppressed` — единственный
    человеческий текст записи, которую панель показывает как инцидент.
    Закреплены они были только у `down`."""
    alerted = {"web": {"fail": 3, "alerted": True, "alerted_reason": "old"}}
    rec, _ = ow.transitions(alerted, {"web": _probe(True, "back", "снова 200")},
                            debounce=2)
    assert (rec[0]["reason"], rec[0]["detail"]) == ("back", "снова 200")

    chg, _ = ow.transitions(alerted, {"web": _probe(False, "new", "теперь 500")},
                            debounce=2)
    assert (chg[0]["reason"], chg[0]["detail"]) == ("new", "теперь 500")

    sup, _ = ow.transitions(
        {"web": {"fail": 1, "alerted": False}},
        {"web": _probe(False, "no_process", "процес раннера не знайдено")},
        debounce=2, suppress_down=True)
    assert (sup[0]["reason"], sup[0]["detail"]) == ("no_process",
                                                    "процес раннера не знайдено")


def test_a_transition_carries_exactly_the_five_fields_and_no_sixth():
    """Проверка полей ПО ОДНОМУ пропускает лишние: мутация, добавляющая в
    переход `pid`/`hb_age`, проходила весь гейт. §2.2 называет ровно пять полей
    и запрещает те, что меняются каждый цикл и не значат ничего."""
    for kind, t in _one_of_each_kind().items():
        assert set(t) == JOURNAL_FIELDS, (kind, sorted(t))
        assert t["kind"] == kind


def test_the_debounce_argument_actually_reaches_the_core():
    """Все прежние вызовы шли с `debounce=2` — ровно с умолчанием `DEBOUNCE`,
    поэтому проброс аргумента не проверял никто: код, игнорирующий параметр и
    берущий константу, был бы зелёным."""
    assert ow.DEBOUNCE == 2, "предпосылка теста сломана: умолчание изменилось"
    st = {"backend": {"fail": 1, "alerted": False}}
    probes = {"backend": _probe(False)}

    assert [t["kind"] for t in ow.transitions(st, probes, debounce=2)[0]] == ["down"]
    assert ow.transitions(st, probes, debounce=3)[0] == [], \
        "при debounce=3 второе подряд падение ещё не повод для 🚨"
    assert ow.evaluate(st, probes, debounce=3)[0] == []
    assert len(ow.evaluate(st, probes, debounce=1)[0]) == 1, \
        "при debounce=1 алерт обязан уйти с первого же падения"


# ── подавленное падение: ловушка 4 и §4.6 ──────────────────────────────────
def test_one_fall_in_the_boot_window_writes_exactly_two_records():
    """Ловушка 4 спеки обещает РОВНО ДВЕ записи об одном падении: `suppressed`
    в окне и настоящий `down` после него.

    Дедупа у `suppressed` не было вовсе — `alerted` в этой ветке не ставится по
    построению, — поэтому запись уходила КАЖДЫЙ цикл, пока идёт окно. При
    BOOT_GRACE_S=300 и цикле 30 с это девять записей на пробу, а после ребута
    красны все девять проб. §4.6 схлопывает на экране ПАРУ, а не девятку: восемь
    лишних строк уехали бы на первый экран с «исход пока неизвестен»."""
    cycles = int(ow.BOOT_GRACE_S // 30)
    assert cycles == 10, "предпосылка теста сломана: окно или цикл изменились"
    probes = {"backend": _probe(False, "no_response", "нет ответа")}
    journal, state = [], {}
    for _ in range(cycles):
        trs, state = ow.transitions(state, probes, debounce=2, suppress_down=True)
        journal += trs
    # Окно кончилось, сервис так и не поднялся — вот теперь 🚨 по-настоящему.
    trs, state = ow.transitions(state, probes, debounce=2, suppress_down=False)
    journal += trs
    assert [t["kind"] for t in journal] == ["suppressed", "down"], journal


def test_a_fall_the_owner_already_heard_about_is_not_written_as_suppressed():
    """§2.3 определяет `suppressed` как «событие, о котором владельцу НЕ
    сообщили». Ветка не смотрела на `alerted`, а состояние переживает ребут:
    проверка, объявленная красной днями раньше (чек worktree простоял красным
    1669 циклов), в загрузочном окне снова порождала `suppressed`, и §4.6
    рисовал НОВЫЙ инцидент про старое падение."""
    probes = {"worktree": _probe(False, "dirty:a.yaml", "модифицировано 1")}

    long_red = {"worktree": {"fail": 1669, "alerted": True,
                             "alerted_reason": "dirty:a.yaml"}}
    assert ow.transitions(long_red, probes, debounce=2, suppress_down=True)[0] == []

    # Вторая форма держит РОВНО условие «уже сообщили», а не дедуп по счётчику:
    # здесь порог пересекается прямо сейчас, то есть дедуп из А1 пропустил бы
    # запись, и молчит только проверка `alerted`. Стейт читается с диска, и его
    # форма важнее её происхождения — файл переживает и ребут, и выкатку.
    about_to_cross = {"worktree": {"fail": 1, "alerted": True,
                                   "alerted_reason": "dirty:a.yaml"}}
    trs, _st = ow.transitions(about_to_cross, probes, debounce=2, suppress_down=True)
    assert trs == [], trs


def test_a_suppressed_fall_that_came_back_up_leaves_its_outcome_in_the_journal():
    """§4.6 объявляет ТРИ исхода подавленного падения, а писатель умел два:
    `recovered` стоял за `alerted`, которого `suppress_down` намеренно не
    ставит. Инцидент, рассосавшийся сам, навсегда оставался на экране как
    «исход пока неизвестен»."""
    st = {"backend": {"fail": 1, "alerted": False}}
    trs, st = ow.transitions(st, {"backend": _probe(False, "no_response")},
                             debounce=2, suppress_down=True)
    assert [t["kind"] for t in trs] == ["suppressed"]

    trs2, st2 = ow.transitions(st, {"backend": _probe(True, "up", "HTTP 200")},
                               debounce=2, suppress_down=True)
    assert [t["kind"] for t in trs2] == ["recovered"], trs2
    assert trs2[0]["detail"] == "HTTP 200"
    assert st2["backend"] == {"fail": 0, "alerted": False}


def test_the_owner_hears_no_recovery_of_a_fall_he_was_never_told_about():
    """Пара к предыдущему, и она же — граница §3: в ЖУРНАЛ подъём пишется, в
    КАНАЛ АЛЕРТА нет. ✅ о подъёме того, о падении чего молчали, — это ✅ ни о
    чём, и контракт `evaluate()` оно бы сломало."""
    st = {"backend": {"fail": 5, "alerted": False}}
    probes = {"backend": _probe(True)}
    trs, _ = ow.transitions(st, probes, debounce=2)
    assert [t["kind"] for t in trs] == ["recovered"], trs
    assert ow.evaluate(st, probes, debounce=2)[0] == []


# ── кривой стейт: цена падения здесь несоразмерна ──────────────────────────
def test_a_non_dict_entry_in_the_state_file_does_not_kill_the_cycle():
    """`detect_reboot()` в том же файле такой стейт переживает
    (`dict(v) if isinstance(v, dict) else v`), а `transitions()` падала на
    голом `dict(v)`.

    Цена несоразмерна причине: `main()` исключение не ловит, а обёртка
    `ops_watchdog_detached.ps1` пишет heartbeat ДО цикла и ловит ошибку в
    `catch` — петля жива, heartbeat свеж, все наблюдатели видят ЗДОРОВЫЙ
    сторож, а алертов нет НИКОГДА. Сегодня латентно (живой стейт — одни
    словари), но журнал кладёт в тот же файл служебные ключи, и первый же
    скалярный ключ убил бы сторожа."""
    probes = {"backend": _probe(False, "no_response", "нет ответа")}
    down_text = "🚨 DOWN: BACKEND (:8010 /health). нет ответа"

    # (а) чужой служебный ключ рядом с проверками — не трогаем и не спотыкаемся
    for junk in (None, 0, 1, 3.5, True, "строка", [], ["мусор"]):
        prev = {"backend": {"fail": 1, "alerted": False}, "_journal_seq": junk}
        trs, new = ow.transitions(prev, probes, debounce=2)
        assert [t["kind"] for t in trs] == ["down"], (junk, trs)
        assert new["_journal_seq"] == junk, junk
        assert ow.evaluate(prev, probes, debounce=2)[0] == [down_text], junk

    # (б) запись САМОЙ проверки не словарь — `dict(scalar)` падал бы в цикле
    for junk in (None, 0, "строка", 3.5, True):
        trs, new = ow.transitions({"backend": junk}, probes, debounce=1)
        assert [t["kind"] for t in trs] == ["down"], (junk, trs)
        assert new["backend"] == {"fail": 1, "alerted": True,
                                  "alerted_reason": "no_response"}, junk


# ── ротация журнала: §2.4 ──────────────────────────────────────────────────
DAY = 86400.0


def _rec(ts, check="backend", kind="down", detail="d"):
    return {"ts": ts, "check": check, "kind": kind, "reason": "r", "detail": detail}


def test_records_older_than_thirty_days_are_dropped():
    now = 1_000_000.0
    recs = [_rec(now - 40 * DAY), _rec(now - 31 * DAY), _rec(now - 1 * DAY)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert len(kept) == 1 and dropped == 2
    assert "сут" in why, why


def test_the_record_ceiling_catches_a_restart_storm():
    """Чистые 30 суток безопасны при обычном темпе, но шторм рестартов набьёт
    тысячи строк за сутки — предохранитель по объёму на этот случай."""
    now = 1_000_000.0
    recs = [_rec(now - 60.0) for _ in range(5400)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert len(kept) == ow.JOURNAL_MAX_RECORDS
    assert dropped == 400
    assert "записей" in why, why
    assert kept[-1]["ts"] == recs[-1]["ts"], "обрезали новые вместо старых"


def test_trimming_nothing_reports_nothing():
    """Парный сторож: маркер, который пишется на каждой дозаписи, — это шум,
    а не сигнал. Ротация без потерь обязана молчать."""
    now = 1_000_000.0
    kept, dropped, why = ow.journal_trim([_rec(now - 60.0)], now)
    assert dropped == 0 and why == ""
    assert len(kept) == 1


def test_an_empty_journal_is_trimmed_to_nothing_and_says_nothing():
    """Вырожденный вход — обычный: в первые же сутки после выкатки журнала
    файла либо нет, либо он пуст, и ротация вызывается на пустом списке."""
    assert ow.journal_trim([], 1_000_000.0) == ([], 0, "")


def test_the_ceiling_drops_the_oldest_not_the_first_in_the_file():
    """НЕСУЩИЙ сторож потолка. `kept[by_count:]` режет первые записи ПО ФАЙЛУ,
    и это то же самое, что «самые старые», ТОЛЬКО если файл отсортирован.

    Гарантии сортировки нет ниоткуда: файл переживает ребуты и прыжки часов
    (NTP правит время ровно после загрузки), а после ротации в него дописывают
    дальше. На неотсортированном входе наивный срез выбрасывает как раз
    свежие записи — то есть ровно то, ради чего журнал заводился."""
    now = 1_000_000.0
    recs = ([_rec(now - 60.0, detail="свежая %d" % i) for i in range(3)]
            + [_rec(now - 10 * DAY, detail="старая %d" % i) for i in range(2)])
    kept, dropped, why = ow.journal_trim(recs, now, max_records=3)
    assert dropped == 2 and "потолка" in why, why
    assert [r["detail"] for r in kept] == ["свежая 0", "свежая 1", "свежая 2"], kept


def test_exactly_the_ceiling_is_not_a_reason_to_trim():
    """Граница: `>` против `>=` здесь стоит маркера «журнал обрезан» на каждой
    дозаписи ровно на потолке — того самого шума, от которого §2.4 защищает."""
    now = 1_000_000.0
    recs = [_rec(now - 60.0) for _ in range(ow.JOURNAL_MAX_RECORDS)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert (len(kept), dropped, why) == (ow.JOURNAL_MAX_RECORDS, 0, "")


def test_both_limits_at_once_are_both_named_in_one_marker():
    """Обе границы срабатывают в одном вызове после долгого простоя писателя.
    Маркер обязан назвать ОБЕ причины: «отброшено 400» без второй причины
    отправит владельца искать шторм рестартов там, где его не было."""
    now = 1_000_000.0
    recs = ([_rec(now - 31 * DAY, detail="древняя") for _ in range(2)]
            + [_rec(now - 60.0, detail="свежая") for _ in range(5)])
    kept, dropped, why = ow.journal_trim(recs, now, max_records=3)
    assert len(kept) == 3 and dropped == 4
    assert "старше 30 сут" in why and "сверх потолка" in why, why
    assert {r["detail"] for r in kept} == {"свежая"}, kept


def test_the_marker_reads_like_the_sample_in_the_spec():
    """§2.4 приводит маркер ДОСЛОВНО: «отброшено 812 записей старше 30 сут».
    Слово «записей» там не украшение — писатель подставляет `why` в текст для
    владельца, и «отброшено 812 старше 30 сут» приезжает к нему обрубком."""
    now = 1_000_000.0
    recs = [_rec(now - 40 * DAY) for _ in range(812)] + [_rec(now - 60.0)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert why == "812 записей старше 30 сут", why
    assert (len(kept), dropped) == (1, 812)


def test_the_number_and_the_word_next_to_it_agree():
    """Числа в маркере не константы: потолок приходит аргументом, и на
    нестандартном потолке выходило «в 3 записей». В сторожевом сообщении
    несогласование читается как опечатка, а не как факт, — а маркер заводился
    ровно затем, чтобы ему верили."""
    now = 1_000_000.0

    def why_for(n):
        return ow.journal_trim([_rec(now - 40 * DAY) for _ in range(n)], now)[2]

    assert why_for(1) == "1 запись старше 30 сут"
    assert why_for(2) == "2 записи старше 30 сут"
    assert why_for(5) == "5 записей старше 30 сут"
    assert why_for(11) == "11 записей старше 30 сут", "11-14 идут по «многим»"

    recs = [_rec(now - 60.0) for _ in range(4)]
    assert ow.journal_trim(recs, now, max_records=3)[2] == "1 запись сверх потолка в 3"


def test_a_record_with_no_usable_time_is_dropped_but_not_called_old():
    """Файл читает и дописывает несколько поколений кода, и `float()` на чужом
    поле БРОСАЕТ. Цена несоразмерна: `main()` исключение не ловит, обёртка
    пишет heartbeat ДО цикла — наблюдатели видят здоровый сторож, который
    молчит навсегда.

    Такую запись выбрасываем (панель по ней не сгруппирует и не отсортирует),
    но в маркере называем СВОИМ именем: «старше 30 суток» про запись без
    времени — ложь, а маркер заводился ровно затем, чтобы не врать.

    Три последних входа приходят из НАСТОЯЩЕЙ строки jsonl, а не из фантазии:
    `float(10**400)` бросает OverflowError (а не ValueError, который ловился),
    и цикл сторожа улетал бы наружу; `json.loads('{"ts": 1e400}')` и `"1e400"`
    дают `inf` — проверено. `+inf` по возрасту не истечёт никогда и под
    потолком сортируется как самая свежая, вытесняя настоящую запись, а `-inf`
    уезжает с маркером «старше 30 сут», хотя времени у неё нет."""
    now = 1_000_000.0
    broken = [{"check": "x", "kind": "down"},          # ключа `ts` нет вовсе
              _rec(None), _rec("вчера"), _rec([]), _rec(float("nan")),
              _rec(True), "строка вместо записи",
              _rec(10 ** 400), _rec(float("inf")), _rec(float("-inf")),
              _rec("1e400")]
    kept, dropped, why = ow.journal_trim(broken + [_rec(now - 60.0)], now)
    assert [r["detail"] for r in kept] == ["d"], kept
    assert dropped == len(broken), why
    # «строк», а не «записей»: среди отброшенного есть и то, что записью не
    # является вовсе (None, строка, список), — назвать это «записями» значило
    # бы соврать ровно там, где маркер заводился, чтобы не врать.
    assert why == "%d строк без пригодного времени" % len(broken), why
    assert "старше" not in why, why


def test_a_timestamp_that_arrived_as_a_string_is_still_a_time():
    """Строку `float()` читает — значит запись пригодна, и выбрасывать её как
    «без времени» значило бы терять читаемые события."""
    now = 1_700_000_000.0
    fresh, ancient = "%.1f" % (now - 60.0), "%.1f" % (now - 40 * DAY)
    kept, dropped, why = ow.journal_trim([_rec(fresh), _rec(ancient)], now)
    assert dropped == 1 and "старше" in why, why
    assert [r["ts"] for r in kept] == [fresh], "запись переписали при обрезке"


def test_a_timestamp_from_the_future_is_not_mistaken_for_an_ancient_one():
    """Часы прыгают ровно тогда, когда журнал нужнее всего: NTP правит время
    после загрузки, а записи о ребуте пишутся в первые же минуты. `now - ts`
    у такой записи отрицателен — «не старая», а не «старее всех».

    Обе дистанции названы намеренно. Час вперёд — обычный сдвиг до
    синхронизации; сорок суток вперёд — машина, поднявшаяся с сорванными
    часами (дата из BIOS). Сторож только на близком будущем пропускал
    `abs(now - ts)` — проверено фактом на харнессе мутаций, — а под этой
    формулой записи с сорванных часов уезжают как «старше 30 сут», то есть
    события ребута теряются вместе с маркером, который об этом соврал."""
    now = 1_000_000.0
    kept, dropped, why = ow.journal_trim([_rec(now + 3600.0, detail="час вперёд"),
                                          _rec(now + 40 * DAY, detail="дата из BIOS")],
                                         now)
    assert (len(kept), dropped, why) == (2, 0, ""), why


def test_a_clock_before_the_epoch_does_not_kill_the_cycle():
    """Отрицательное `now` — не фантазия: то же сползание часов до синхронизации.
    Арифметика обязана остаться арифметикой, а не отдельной веткой."""
    kept, dropped, why = ow.journal_trim([_rec(-2000.0), _rec(0.0)], -1000.0)
    assert (len(kept), dropped, why) == (2, 0, "")


# ── граница stdlib-only: §2.1 и ловушка 1 спеки ────────────────────────────
# Под pytest корень репозитория и так лежит на `sys.path`, поэтому «модуль
# загрузился по пути» не доказывает НИЧЕГО: `from app.services import ...` в
# шапке сторожа прошёл бы все сторожа зелёным. Нарушение границы проявляется не
# красным тестом, а ТИШИНОЙ сторожа ровно в тот момент, ради которого он
# существует, — поэтому путь гоняется в подпроцессе, где корня репозитория нет
# на пути, а `__import__` пропускает только stdlib.
_ISOLATED_CHILD = '''\
# -*- coding: utf-8 -*-
import builtins
import importlib.util
import os
import sys

WATCHDOG, REPO_ROOT = sys.argv[1], os.path.abspath(sys.argv[2])

sys.path[:] = [p for p in sys.path
               if os.path.abspath(p or os.getcwd()) != REPO_ROOT]

_real_import = builtins.__import__
_ALLOWED = set(sys.stdlib_module_names) | {"ops_watchdog_isolated"}


def _guard(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and name.partition(".")[0] not in _ALLOWED:
        raise ImportError("ГРАНИЦА stdlib-only нарушена: %s" % name)
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _guard

spec = importlib.util.spec_from_file_location("ops_watchdog_isolated", WATCHDOG)
ow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ow)

fresh = {"backend": {"fail": 1, "alerted": False}}
probes = {"backend": {"ok": False, "detail": "нет ответа", "reason": "no_response"}}
trs, _st = ow.transitions(fresh, probes, debounce=2)
assert [t["kind"] for t in trs] == ["down"], trs
alerts, _st2 = ow.evaluate(fresh, probes, debounce=2)
assert alerts == ["\\U0001F6A8 DOWN: BACKEND (:8010 /health). нет ответа"], alerts
print("STDLIB-ONLY OK")
'''


def test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable(tmp_path):
    """Несущее требование, а не стиль: сторож обязан сообщить о смерти бэкенда
    именно тогда, когда мертво его окружение."""
    child = tmp_path / "isolated_watchdog_probe.py"
    with open(child, "w", encoding="utf-8", newline="") as fh:
        fh.write(_ISOLATED_CHILD)
    proc = subprocess.run(
        # -I: ни PYTHONPATH, ни user-site. -X utf8 командной строкой, а не
        # переменной окружения, — -I стёр бы PYTHONUTF8 вместе с остальными.
        [sys.executable, "-I", "-X", "utf8", str(child),
         str(ROOT / "scripts" / "ops_watchdog.py"), str(ROOT)],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, (
        "сторожевой путь не пережил среду без app/, chatter/ и сторонних "
        "пакетов:\n--- stdout ---\n%s\n--- stderr ---\n%s"
        % (proc.stdout, proc.stderr))
    assert "STDLIB-ONLY OK" in proc.stdout, proc.stdout
