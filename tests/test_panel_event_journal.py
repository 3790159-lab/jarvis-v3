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
import json
import os
import stat
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
    # Величина потолка пришпилена так же, как «30 сут» у возраста: маркер,
    # который врёт про порог, отправляет владельца искать шторм рестартов не
    # там, где он был. Асимметрия «про сутки краснеет, про потолок нет» — это
    # и есть дыра, а не экономия.
    assert ("сверх потолка в %d" % ow.JOURNAL_MAX_RECORDS) in why, why
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
    свежие записи — то есть ровно то, ради чего журнал заводился.

    Времена выживших РАЗВЕДЕНЫ и в файле лежат не по возрастанию (60 с, час,
    10 минут назад) — иначе сторож молчит о втором обещании докстринга,
    «порядок записей сохраняется как в файле»: при одинаковых `ts` и переворот
    выхода, и пересортировка журнала по времени проходят зелёными, потому что
    любая перестановка одинаковых времён стабильна."""
    now = 1_000_000.0
    recs = ([_rec(now - age, detail="свежая %d" % i)
             for i, age in enumerate((60.0, 3600.0, 600.0))]
            + [_rec(now - 10 * DAY, detail="старая %d" % i) for i in range(2)])
    kept, dropped, why = ow.journal_trim(recs, now, max_records=3)
    assert dropped == 2 and "потолка" in why, why
    assert [r["detail"] for r in kept] == ["свежая 0", "свежая 1", "свежая 2"], kept


def test_a_record_exactly_thirty_days_old_is_not_old_yet():
    """Граница возраста: `>` против `>=`. У потолка на границу заведён отдельный
    сторож, у возраста не было ничего — а цена та же и хуже: `>=` выбрасывает
    запись РОВНО на границе, то есть журнал теряет событие, которое обещал
    держать 30 суток, и докладывает о нём «старше 30 сут»."""
    now = 1_700_000_000.0
    kept, dropped, why = ow.journal_trim([_rec(now - 30 * DAY)], now)
    assert (len(kept), dropped, why) == (1, 0, ""), why


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


# ── дозапись, маркер ротации, маркер живости: §2.4-§2.5 ────────────────────
def _read(p):
    """Читать журнал ТЕМ ЖЕ делением, что и писатель: по "\\n".

    `splitlines()` здесь было бы не строгостью, а сообщничеством: он рвёт текст
    ещё и по U+2028/U+2029/U+0085, и сторож на этой границе оказался бы зелёным
    на файле, который панель прочитает иначе."""
    text = Path(p).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def _kinds(p):
    return [r.get("kind") for r in _read(p)]


def test_append_writes_one_line_per_transition(tmp_path):
    p = tmp_path / "j.jsonl"
    ok = ow.journal_append([_rec(1.0), _rec(2.0)], path=p, now=3.0)
    assert ok is True
    assert len(_read(p)) == 2


def test_rotation_leaves_a_marker_in_the_journal(tmp_path):
    """Молчаливой обрезки не бывает: мы чиним ровно этот класс дефекта —
    окно, из которого события уезжали без следа."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 40 * DAY)], path=p, now=now)
    ow.journal_append([_rec(now)], path=p, now=now)
    recs = _read(p)
    marks = [r for r in recs if r["kind"] == "rotated"]
    assert len(marks) == 1, recs
    assert "старше 30 сут" in marks[0]["detail"], marks[0]


def _marker(p):
    """Единственный маркер ротации в файле — и он обязан быть единственным."""
    marks = [r for r in _read(p) if r.get("kind") == "rotated"]
    assert len(marks) == 1, marks
    return marks[0]


def test_the_marker_is_a_record_of_the_same_five_fields_and_of_no_probe(tmp_path):
    """Форму МАРКЕРА не проверял никто — только `kind` и `detail`. Мутации
    «маркер несёт шестое поле (`pid`)» и «маркер приписан чужой пробе вместо
    служебного ключа» обе проходили гейт зелёными.

    Вторая опаснее первой: маркер с `check: "backend"` панель нарисует как
    инцидент НАСТОЯЩЕЙ пробы — сторож доложит о падении бэкенда, которого не
    было, и это ровно та ложь, за которой перестают следить вообще. §2.2
    называет ровно пять полей, §2.4 приводит маркер дословно с
    `"check": "_journal"` — служебным ключом, проверкой не являющимся (тот же
    приём, что и `BOOT_KEY`)."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 40 * DAY)], path=p, now=now)
    ow.journal_append([_rec(now)], path=p, now=now)

    mark = _marker(p)
    assert set(mark) == JOURNAL_FIELDS, sorted(mark)
    assert mark["check"] == ow.JOURNAL_SELF == "_journal", mark
    assert mark["kind"] == ow.JOURNAL_ROTATED == "rotated", mark
    assert mark["reason"] == "trim", mark


def test_the_marker_carries_the_time_of_now_not_a_constant(tmp_path):
    """Плановый читатель панели фильтрует журнал окном 72 ч. Маркер с `ts: 0.0`
    не попадёт на экран НИКОГДА — обрезка снова станет молчаливой, но теперь
    незаметнее прежнего: в файле маркер есть, а на экране его нет и не будет.
    Мутация `"ts": now` → `"ts": 0.0` проходила гейт зелёной."""
    p = tmp_path / "j.jsonl"
    now = 1_700_000_000.0                   # настоящее время, а не 10**6
    ow.journal_append([_rec(now - 40 * DAY)], path=p, now=now)
    ow.journal_append([_rec(now)], path=p, now=now)

    mark = _marker(p)
    assert isinstance(mark["ts"], float), mark
    assert mark["ts"] == now, mark

    # И то же самое от НАСТОЯЩИХ часов: `now=None` — как в живом цикле.
    p2 = tmp_path / "j2.jsonl"
    before = time.time()
    ow.journal_append([_rec(before - 40 * DAY)], path=p2)
    ow.journal_append([_rec(before)], path=p2)
    assert before <= _marker(p2)["ts"] <= time.time(), _marker(p2)


def test_no_marker_appears_when_nothing_was_lost(tmp_path):
    """Пара к предыдущему и к `test_trimming_nothing_reports_nothing`: маркер,
    который пишется на КАЖДОЙ дозаписи, — шум, а не сигнал, и первый экран он
    заливает так же, как его залили одиннадцать одинаковых грязных деревьев."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    for i in range(5):
        ow.journal_append([_rec(now - 60.0 + i)], path=p, now=now)
    assert _kinds(p) == ["down"] * 5, _read(p)


def test_at_the_ceiling_the_journal_does_not_turn_into_markers(tmp_path):
    """НЕСУЩИЙ сторож врезки к Task 3, воспроизведён фактом на плановом коде.

    Маркер ротации дописывается В ТОТ ЖЕ файл. Если он занимает слот потолка,
    то не вымывается никогда: потолок режет самые СТАРЫЕ записи, а маркер —
    всегда самая свежая. Журнал на потолке терял по ДВЕ настоящих записи за
    дозапись ради одной новой, и за восемь циклов в файле оказывалось восемь
    маркеров: ровно в шторме рестартов — сценарии, ради которого потолок и
    заведён, — журнал деградировал к «журнал обрезан ×8» вместо событий.

    Числа здесь — арифметика, а не вкус: потолок 20, восемь дозаписей по одной
    записи. Правильное поведение теряет РОВНО одну старую запись за дозапись,
    и маркер в файле всегда один."""
    p = tmp_path / "j.jsonl"
    now, ceiling = 1_000_000.0, 20
    ow.journal_append([_rec(now - 1000.0 + i, detail="старая %d" % i)
                       for i in range(ceiling)],
                      path=p, now=now, max_records=ceiling)
    assert _kinds(p) == ["down"] * ceiling, "ровно потолок — ещё не повод резать"

    for i in range(8):
        ow.journal_append([_rec(now + i, detail="новая %d" % i)],
                          path=p, now=now + i, max_records=ceiling)

    recs = _read(p)
    marks = [r for r in recs if r["kind"] == "rotated"]
    events = [r for r in recs if r["kind"] != "rotated"]
    assert len(marks) == 1, "маркеры копятся по одному за дозапись: %d" % len(marks)
    assert len(events) == ceiling, [r["detail"] for r in events]
    # Восемь новых вытеснили ровно восемь самых старых — не шестнадцать.
    assert [r["detail"] for r in events] == (
        ["старая %d" % i for i in range(8, ceiling)]
        + ["новая %d" % i for i in range(8)]), [r["detail"] for r in events]


def test_a_journal_full_of_old_markers_collapses_on_the_first_rotation(tmp_path):
    """Файл переживает выкатку: к моменту этой правки в живом журнале уже могли
    накопиться маркеры прежнего писателя. Схлопывание обязано их вылечить, а не
    ждать, пока они истекут по возрасту."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    old = ([{"ts": now - 100.0 + i, "check": "_journal", "kind": "rotated",
             "reason": "trim", "detail": "отброшено %d записей сверх потолка в 3" % i}
            for i in range(8)]
           + [_rec(now - 50.0, detail="настоящая")])
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old))

    ow.journal_append([_rec(now, detail="новая")], path=p, now=now, max_records=1)
    recs = _read(p)
    assert [r["kind"] for r in recs] == ["down", "rotated"], recs
    assert recs[0]["detail"] == "новая"
    # Схлопнутые отметки названы числом: восемь прежних отметок — это восемь
    # прошлых обрезок, то есть НЕ МЕНЬШЕ восьми потерянных записей сверх той
    # одной, о которой говорит `why`.
    assert "8 прежних отметок об обрезке" in recs[1]["detail"], recs[1]["detail"]


def test_the_marker_does_not_understate_the_loss_fifty_fold(tmp_path):
    """Схлопывание маркеров было новой молчаливой потерей ТОГО ЖЕ класса, ради
    которого арка заведена: прежние `rotated` отсеиваются ДО обрезки, поэтому в
    `dropped` и `why` они не попадают вовсе.

    Воспроизведено фактом (потолок 20, 50 дозаписей): в файле оставался маркер
    «отброшено 1 запись сверх потолка в 20», хотя выброшено 50 настоящих записей
    и 49 прежних маркеров. Владелец читал «отброшено 1 запись» — ровно та ложь,
    против которой §2.4 написана.

    Шестого поля §2.2 не даёт, а разбирать собственный текст обратно в число
    писатель не станет, поэтому маркер отвечает честной НИЖНЕЙ ГРАНИЦЕЙ: за
    каждой схлопнутой отметкой стоит не меньше одной потерянной записи. В этом
    сценарии граница равна двум, а не пятидесяти, — и это не оговорка теста, а
    прямая цена схлопывания: в файле в каждый момент лежит РОВНО одна прежняя
    отметка. Ценность в качестве, а не в цифре: «отброшено 1 запись» читается
    как «потеря была одна за всю жизнь журнала», а так владелец видит, что
    обрезка уже случалась раньше."""
    p = tmp_path / "j.jsonl"
    now, ceiling = 1_000_000.0, 20
    ow.journal_append([_rec(now - 1000.0 + i, detail="старая %d" % i)
                       for i in range(ceiling)],
                      path=p, now=now, max_records=ceiling)
    for i in range(50):
        ow.journal_append([_rec(now + i, detail="новая %d" % i)], path=p,
                          now=now + i, max_records=ceiling)

    marks = [r for r in _read(p) if r["kind"] == "rotated"]
    assert len(marks) == 1, marks
    assert marks[0]["detail"] == (
        "отброшено 1 запись сверх потолка в 20; перезаписью стёрто ещё "
        "1 прежняя отметка об обрезке (каждая — не меньше одной потерянной "
        "записи)"), marks[0]["detail"]


def test_lines_the_rewrite_erases_are_named_and_not_vanished(tmp_path):
    """Перезапись при обрезке стирает и то, что `journal_read` не смог прочесть:
    огрызок убитой записи, строку без json, строку-не-запись. Воспроизведено
    фактом: три нечитаемых строки (включая огрызок настоящего события) ушли из
    файла без единого слова, а маркер рядом говорил только про возраст.

    Считаем их ЗДЕСЬ, а не в докстринге `journal_read`: докстринг сторожем не
    является, и молчаливая потеря осталась бы молчаливой. Панель зовёт
    `journal_read` и живёт без счётчика — считает только писатель, ровно в тот
    момент, когда он эти строки СТИРАЕТ."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(_rec(now - 40 * DAY, detail="древняя"),
                            ensure_ascii=False) + "\n")
        fh.write("не-json огрызок\n")
        fh.write('{"ts": 3.0, "check": "b", "kind": "do\n')   # убитая на середине
        fh.write("42\n")                                       # json, но не запись

    assert ow.journal_append([_rec(now, detail="свежая")], path=p, now=now) is True
    recs = _read(p)
    assert [r["detail"] for r in recs][:1] == ["свежая"], recs
    assert recs[-1]["kind"] == "rotated", recs
    assert recs[-1]["detail"] == (
        "отброшено 1 запись старше 30 сут; перезаписью стёрто ещё "
        "3 нечитаемые строки"), recs[-1]["detail"]


def test_one_unreadable_line_is_counted_in_the_singular(tmp_path):
    """Пара к предыдущему: число и слово рядом с ним согласованы и здесь —
    маркер, читающийся как опечатка, теряет доверие ровно там, где он заведён,
    чтобы ему верили."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(_rec(now - 40 * DAY, detail="древняя"),
                            ensure_ascii=False) + "\n")
        fh.write("не-json огрызок\n")

    ow.journal_append([_rec(now, detail="свежая")], path=p, now=now)
    assert _read(p)[-1]["detail"].endswith("ещё 1 нечитаемая строка"), _read(p)[-1]


def test_the_liveness_marker_is_touched_after_a_successful_write(tmp_path):
    """Свежий маркер + пустой журнал = настоящая тишина. Без маркера эти два
    случая на экране неразличимы."""
    beat = tmp_path / "beat"
    assert ow.touch_beat(path=beat) is True
    assert beat.exists()


def test_a_failed_write_does_not_refresh_the_marker(tmp_path, capsys):
    """Провал записи обязан показывать себя протухающим маркером, а не тонуть
    в тишине. Touch делается ТОЛЬКО после успеха (DEV-18)."""
    unwritable = tmp_path / "нет" / "такого" / "каталога" / "j.jsonl"
    # Родителя намеренно не создаём и запрещаем создание.
    ok = ow.journal_append([_rec(1.0)], path=unwritable, now=2.0, mkdir=False)
    assert ok is False
    assert not (tmp_path / "beat").exists()
    assert "журнал" in capsys.readouterr().err, "провал записи утонул в тишине"


def test_the_marker_of_liveness_fails_loudly_too(tmp_path, capsys):
    """Пара к предыдущему с другого конца: маркер, который не записался,
    молчать не имеет права — иначе панель увидит протухший маркер и объявит
    писателя мёртвым, а в логе не будет ни слова о причине."""
    busy = tmp_path / "занято"
    busy.write_text("я файл, а не каталог", encoding="utf-8")
    assert ow.touch_beat(path=busy / "beat") is False
    assert "маркер живости" in capsys.readouterr().err


def test_a_corrupt_journal_line_does_not_stop_the_writer(tmp_path):
    """Панель читает файл, который дописывает другой процесс. Битая строка не
    имеет права остановить ни писателя, ни ротацию."""
    p = tmp_path / "j.jsonl"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write('{"ts": 1.0, "kind": "down"}\nне-json\n')
    assert ow.journal_append([_rec(2.0)], path=p, now=3.0) is True


def test_reading_does_not_stop_at_the_first_broken_line(tmp_path):
    """`continue` против `break`: строка, которую не разобрали, стоит ОДНОЙ
    записи, а остановка чтения — всего журнала после неё. Ротация после этого
    переписала бы файл, оставив в нём только то, что успела прочитать."""
    p = tmp_path / "j.jsonl"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("мусор\n"
                 '{"ts": 1.0, "check": "a", "kind": "down"}\n'
                 "42\n"                       # JSON, но не запись
                 "\n"                         # пустая строка
                 '["список", "тоже не запись"]\n'
                 '{"ts": 2.0, "check": "b", "kind": "down"}\n')
    assert [r["check"] for r in ow.journal_read(p)] == ["a", "b"]


def test_a_byte_order_mark_does_not_eat_the_first_record(tmp_path):
    """Файл живёт 30 суток и переживает открытие человеком: Блокнот и
    PowerShell `>` оставляют BOM, а `json.loads("\\ufeff{...}")` бросает. Ценой
    была бы первая запись файла — самая старая, то есть та, ради которой в
    журнал и заглядывают."""
    p = tmp_path / "j.jsonl"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write('{"ts": 1.0, "check": "a", "kind": "down"}\n')
    assert [r["check"] for r in ow.journal_read(p)] == ["a"]


def test_a_torn_line_does_not_swallow_the_next_record(tmp_path):
    """Оборванная дозапись оставляет огрызок без "\\n" — этот случай и создаётся
    смертью процесса посреди записи. Без шва следующая строка приклеивается к
    огрызку, и гибнет НЕ ТОЛЬКО старое событие, но и новое: одна оборванная
    запись стоила бы двух."""
    p = tmp_path / "j.jsonl"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write('{"ts": 1.0, "check": "a", "kind": "down"}\n{"ts": 2.0, "che')
    assert ow.journal_append([_rec(3.0, check="целая")], path=p, now=4.0) is True
    assert [r["check"] for r in ow.journal_read(p)] == ["a", "целая"]


def test_half_a_utf8_sequence_does_not_kill_the_reader(tmp_path):
    """Настоящий результат kill'а посреди записи — ПОЛОВИНА UTF-8
    последовательности, а не обрубок ASCII: `detail` приходит из проб, где
    кириллица обычна («нет ответа», «процес раннера не знайдено»).

    Цена несоразмерна: `UnicodeDecodeError` — подкласс `ValueError`, `except
    OSError` вокруг чтения его НЕ ловит, `main()` тоже не ловит ничего. Сторож
    остаётся с живым heartbeat (его пишет обёртка ДО цикла) и без единого
    алерта — навсегда. Существующий сторож на огрызок писал чистый ASCII, то
    есть проверял не тот случай."""
    p = tmp_path / "j.jsonl"
    good = json.dumps(_rec(1.0, detail="целая"), ensure_ascii=False)
    torn = '{"ts": 2.0, "check": "b", "detail": "нет отв'
    with open(p, "wb") as fh:               # байты, а не текст: рвём В СЕРЕДИНЕ
        fh.write(good.encode("utf-8") + b"\n")
        fh.write(torn.encode("utf-8") + "е".encode("utf-8")[:1])
    assert p.read_bytes().endswith(b"\xd0"), "предпосылка сломана: хвост целый"

    assert [r["detail"] for r in ow.journal_read(p)] == ["целая"]
    assert ow.journal_append([_rec(3.0, detail="новая")], path=p, now=4.0) is True
    assert [r["detail"] for r in ow.journal_read(p)] == ["целая", "новая"]


def test_a_line_separator_inside_a_detail_does_not_split_the_record(tmp_path):
    """U+2028 `json.dumps(ensure_ascii=False)` пишет В СЫРОМ ВИДЕ, а
    `str.splitlines()` по нему РЕЖЕТ — проверено фактом. Запись уезжала в файл
    одной строкой и читалась как две битых, то есть событие терялось молча.
    `detail` приходит из проб (пути, вывод git, тела ответов), фантазии здесь
    нет.

    Кавычки, обратный слэш и перевод строки в том же входе: их `json.dumps`
    экранирует сам, и сторож на этом стоит рядом — граница одна."""
    p = tmp_path / "j.jsonl"
    detail = 'строка\u2028вторая\u2029третья\u0085четвёртая "кавычка" \\ и \n перевод'
    assert ow.journal_append([_rec(1.0, detail=detail)], path=p, now=2.0) is True
    recs = ow.journal_read(p)
    assert len(recs) == 1, recs
    assert recs[0]["detail"] == detail


def test_nothing_to_write_does_not_even_create_the_file(tmp_path):
    """Обычный цикл сторожа — цикл БЕЗ переходов. Заводить ради него файл (и
    трогать диск раз в 30 с) незачем, а пустой журнал у панели читается так же,
    как отсутствующий."""
    p = tmp_path / "j.jsonl"
    assert ow.journal_append([], path=p, now=1.0) is True
    assert not p.exists()


def test_two_appends_in_a_row_keep_both(tmp_path):
    """Между дозаписями чтения нет — писатель не держит журнал в памяти. Если
    вторая дозапись переоткрывает файл на запись, а не на дозапись, первая
    исчезнет."""
    p = tmp_path / "j.jsonl"
    ow.journal_append([_rec(1.0, detail="первая")], path=p, now=2.0)
    ow.journal_append([_rec(2.0, detail="вторая")], path=p, now=3.0)
    assert [r["detail"] for r in _read(p)] == ["первая", "вторая"]


def test_a_read_only_journal_is_loud_and_not_a_lie(tmp_path, capsys):
    """Право на запись отбирают и антивирус, и бэкап, и неудачный `icacls`.
    Молча вернуть True значило бы обновить маркер живости на журнале, который
    не пишется, — единственное, что панель не имеет права показать."""
    p = tmp_path / "j.jsonl"
    ow.journal_append([_rec(1.0)], path=p, now=2.0)
    os.chmod(p, stat.S_IREAD)
    try:
        assert ow.journal_append([_rec(3.0)], path=p, now=4.0) is False
        assert "журнал" in capsys.readouterr().err
    finally:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def test_a_record_that_cannot_be_serialised_is_loud_and_not_lost_silently(
        tmp_path, capsys):
    """`json.dumps` БРОСАЕТ на bytes, множестве и цикличной ссылке, а
    `main()` исключение не ловит: обёртка пишет heartbeat ДО цикла, и сторож,
    который никогда больше не алертит, выглядит здоровым. Остальные записи
    цикла обязаны уехать, провал — прозвучать, а маркер живости — НЕ
    обновиться (`False`)."""
    p = tmp_path / "j.jsonl"
    loop = {}
    loop["сам"] = loop
    ok = ow.journal_append([_rec(1.0, detail="целая"), loop], path=p, now=2.0)
    assert ok is False, "потеря записи выдана за успешную запись"
    assert [r["detail"] for r in _read(p)] == ["целая"]
    assert "журнал" in capsys.readouterr().err


def test_a_record_that_is_not_a_record_is_refused_by_the_writer(tmp_path, capsys):
    """`json.dumps(42)` пишет «42» без единой жалобы, а `journal_read` такую
    строку пропускает: записью она не станет никогда. Молча положить её в файл
    значит отчитаться об успехе о событии, которого в журнале нет, — писатель
    обязан отвечать симметрично своему же чтению."""
    p = tmp_path / "j.jsonl"
    junk = [None, 42, "строка", ["список"], 3.5, True]
    assert ow.journal_append(junk + [_rec(1.0, detail="целая")], path=p,
                             now=2.0) is False
    assert [r["detail"] for r in _read(p)] == ["целая"]
    assert capsys.readouterr().err.count("не сериализуется") == len(junk)


def test_a_field_with_a_non_string_key_is_loud_instead_of_disappearing(
        tmp_path, capsys):
    """`skipkeys=True` было ЕДИНСТВЕННОЙ молчаливой потерей во всём писателе:
    поле с нестроковым ключом исчезало из записи без слова — stderr пуст,
    возврат `True`, маркер живости обновлён, — тогда как каждая соседняя ветка
    громкая. Мутация «снят только `skipkeys`» пережила все 51 сторож, а соседняя
    «снят только `default=repr`» краснела: охраняло писателя не то.

    `default=repr` и `skipkeys` — не одно и то же, хотя докстринг описывал их
    одной фразой: первый оставляет значение ВИДИМЫМ (своим `repr`), второй
    СТИРАЛ поле. Поэтому такая запись идёт по уже существующему громкому пути:
    `TypeError` → stderr → `False`."""
    p = tmp_path / "j.jsonl"
    rec = dict(_rec(1.0, detail="целая"))
    # Ключ-кортеж `json.dumps` не умеет вовсе (int/float/bool/None он привёл бы
    # к строке сам, без всякого skipkeys).
    rec[("проба", "поле")] = "значение, которое исчезало молча"

    assert ow.journal_append([rec, _rec(2.0, detail="соседняя")], path=p,
                             now=3.0) is False, "потеря поля выдана за успех"
    assert [r["detail"] for r in _read(p)] == ["соседняя"], \
        "соседняя запись цикла обязана уехать"
    assert "не сериализуется" in capsys.readouterr().err


def test_an_exotic_value_travels_as_its_repr_instead_of_killing_the_cycle(tmp_path):
    """Граница мягче предыдущей: bytes/множество `json.dumps` не умеет, но
    выбрасывать из-за них ВСЮ запись значит терять событие целиком. Такой ценой
    сторож не платит — поле уезжает своим `repr`."""
    p = tmp_path / "j.jsonl"
    rec = dict(_rec(1.0), detail=b"\xd0\xb1\xd0\xb0\xd0\xb9\xd1\x82\xd1\x8b")
    assert ow.journal_append([rec], path=p, now=2.0) is True
    assert len(_read(p)) == 1


def test_a_journal_that_is_not_a_journal_does_not_kill_the_writer(tmp_path):
    """Каталога `state/` может не быть вовсе (свежий клон, worktree), а на
    месте журнала может лежать что угодно, включая каталог."""
    fresh = tmp_path / "state" / "глубже" / "j.jsonl"
    assert ow.journal_append([_rec(1.0)], path=fresh, now=2.0) is True
    assert len(_read(fresh)) == 1

    as_dir = tmp_path / "каталог.jsonl"
    as_dir.mkdir()
    assert ow.journal_read(as_dir) == []
    assert ow.journal_append([_rec(1.0)], path=as_dir, now=2.0) is False


def test_a_leftover_tmp_from_an_aborted_run_is_overwritten(tmp_path):
    """`.tmp` остаётся ровно от прогона, убитого между записью и заменой.
    Открывать его на дозапись значило бы склеить два журнала в один битый."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write("огрызок прошлого прогона\n" * 50)

    ow.journal_append([_rec(now - 40 * DAY), _rec(now)], path=p, now=now)
    assert [r["kind"] for r in _read(p)] == ["down", "rotated"], _read(p)


def test_a_failing_swap_leaves_the_journal_whole(tmp_path, monkeypatch, capsys):
    """Диск отдаёт OSError ровно в момент замены. Половины журнала не бывает:
    либо приехал новый файл целиком, либо остался прежний целиком.

    И провал ОБРЕЗКИ — не провал ЗАПИСИ. Разница не теоретическая: на Windows
    `os.replace` бросает `[WinError 5] Отказано в доступе`, если приёмник ОТКРЫТ
    любым читателем, а читатель этого журнала — панель (проверено фактом:
    открытый на чтение `j.jsonl` даёт PermissionError, файл при этом цел, `.tmp`
    остаётся). Возврат `False` здесь означал бы для владельца «🚨 журнал не
    пишется» и НЕ обновлённый маркер живости — то есть ещё 180 с панель пишет
    «писатель молчит» из-за миллисекундного пересечения с собственным читателем.
    Вероятность максимальна ровно в шторме рестартов: журнал на потолке —
    ротация на КАЖДОЙ дозаписи, а панель в этот момент обновляют непрерывно.

    Поэтому: `True` (записи доехали, обрезку догоним в следующем цикле) плюс
    ГРОМКИЙ stderr. Молчание было бы третьим, худшим вариантом — журнал,
    который не режется, растёт без единого слова."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 20.0, detail="старая 0"),
                       _rec(now - 10.0, detail="старая 1")],
                      path=p, now=now, max_records=2)

    def _boom(src, dst):
        raise OSError("диск сказал нет")

    monkeypatch.setattr(ow.os, "replace", _boom)
    assert ow.journal_append([_rec(now, detail="свежая")], path=p, now=now,
                             max_records=2) is True, \
        "провал обрезки выдан за провал записи — владельцу уйдёт ложная 🚨"
    assert "не обрезан" in capsys.readouterr().err, "обрезка не состоялась молча"
    # Дозапись состоялась, обрезка — нет. Это законное состояние: следующий
    # цикл дорежет. Незаконным было бы потерять хоть одну из трёх.
    assert [r["detail"] for r in _read(p)] == ["старая 0", "старая 1", "свежая"]


def test_a_failing_swap_still_does_not_hide_a_lost_record(tmp_path, capsys,
                                                          monkeypatch):
    """Пара к предыдущему: «обрезка не удалась» смягчает возврат ТОЛЬКО за
    обрезку. Если в этом же цикле потеряна запись, ответ обязан остаться
    `False` — иначе смягчение А1 стало бы амнистией для настоящей потери."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 20.0, detail="старая 0"),
                       _rec(now - 10.0, detail="старая 1")],
                      path=p, now=now, max_records=2)

    def _boom(src, dst):
        raise OSError("диск сказал нет")

    monkeypatch.setattr(ow.os, "replace", _boom)
    assert ow.journal_append([_rec(now, detail="свежая"), "не запись"],
                             path=p, now=now, max_records=2) is False
    err = capsys.readouterr().err
    assert "не сериализуется" in err and "не обрезан" in err, err


_KILL_CHILD = '''\
# -*- coding: utf-8 -*-
"""Процесс, убитый РОВНО между записью tmp и `os.replace`."""
import importlib.util
import os
import sys

WATCHDOG, JOURNAL = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("ops_watchdog_killed", WATCHDOG)
ow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ow)


def _die(src, dst):
    os._exit(9)          # ни finally, ни flush, ни атексита — как настоящий kill


os.replace = _die
rec = {"ts": 1000000.0, "check": "новая", "kind": "down",
       "reason": "r", "detail": "новая"}
ow.journal_append([rec], path=JOURNAL, now=1000000.0, max_records=2)
print("НЕ УБИТ: замена не вызывалась")
'''


def test_a_kill_between_the_temp_file_and_the_swap_loses_nothing(tmp_path):
    """Прямой ответ на «что будет, если процесс убьют посреди ротации».

    Дозапись уже в файле, `.tmp` дописан, замена не случилась. Журнал обязан
    остаться ЦЕЛЫМ и НЕОБРЕЗАННЫМ: лишние записи — не потеря, следующий цикл
    дорежет, а вот потеря — необратима. Именно поэтому пишется отдельный `.tmp`
    и `os.replace`, а не `open(p, "w")` поверх живого файла: во втором случае
    этот же kill оставил бы журнал усечённым записью, которая не доехала."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 20.0, detail="старая 0"),
                       _rec(now - 10.0, detail="старая 1")],
                      path=p, now=now, max_records=2)

    child = tmp_path / "killed_writer.py"
    with open(child, "w", encoding="utf-8", newline="") as fh:
        fh.write(_KILL_CHILD)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(child),
         str(ROOT / "scripts" / "ops_watchdog.py"), str(p)],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 9, (proc.returncode, proc.stdout, proc.stderr)

    assert [r["detail"] for r in _read(p)] == ["старая 0", "старая 1", "новая"], \
        "kill посреди ротации оставил полужурнал"
    assert p.with_name(p.name + ".tmp").exists(), \
        "предпосылка сторожа сломана: до замены дело не дошло"

    # И следующий живой цикл доводит ротацию до конца поверх чужого `.tmp`.
    ow.journal_append([_rec(now + 1.0, detail="после")], path=p, now=now + 1.0,
                      max_records=2)
    recs = _read(p)
    assert [r["kind"] for r in recs] == ["down", "down", "rotated"], recs
    assert [r["detail"] for r in recs][:2] == ["новая", "после"]


# ── граница stdlib-only: §2.1 и ловушка 1 спеки ────────────────────────────


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

WATCHDOG, REPO_ROOT, WORK = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3]

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

# Писатель журнала — тот же сторожевой путь, и он тоже обязан работать, когда
# окружение бэкенда мертво. Пути ЯВНЫЕ и внутри временного каталога: живой
# `state/` читает прод-сторож каждые 30 с.
JOURNAL, BEAT = os.path.join(WORK, "j.jsonl"), os.path.join(WORK, "beat")
NOW = 1000000.0


def _rec(ts, detail):
    return {"ts": ts, "check": "backend", "kind": "down",
            "reason": "no_response", "detail": detail}


# Первая же дозапись проходит ВЕСЬ путь: запись, чтение файла, обрезка (запись
# древняя), маркер, `.tmp`, `os.replace` — от неё в файле остаётся один маркер.
assert ow.journal_append([_rec(NOW - 40 * 86400, "древняя")], path=JOURNAL,
                         now=NOW) is True
assert ow.journal_append([_rec(NOW, "свежая")], path=JOURNAL, now=NOW) is True
kinds = [r["kind"] for r in ow.journal_read(JOURNAL)]
assert kinds == ["rotated", "down"], kinds
assert ow.touch_beat(path=BEAT, now=NOW) is True
print("STDLIB-ONLY OK")
'''


def test_the_watchdog_path_runs_where_app_and_third_party_are_unimportable(tmp_path):
    """Несущее требование, а не стиль: сторож обязан сообщить о смерти бэкенда
    именно тогда, когда мертво его окружение.

    Подпроцесс зовёт и ПИСАТЕЛЯ журнала: прежде он гонял только
    `transitions`/`evaluate`, поэтому ленивый `from app...` внутри
    `journal_append`/`journal_read`/`touch_beat` этот сторож не поймал бы —
    а именно ленивый импорт и переживает все прочие тесты зелёным (под pytest
    корень репозитория лежит на `sys.path`). Журнал пишется во ВРЕМЕННЫЙ путь:
    живой `state/` читает прод-сторож каждые 30 с."""
    child = tmp_path / "isolated_watchdog_probe.py"
    with open(child, "w", encoding="utf-8", newline="") as fh:
        fh.write(_ISOLATED_CHILD)
    proc = subprocess.run(
        # -I: ни PYTHONPATH, ни user-site. -X utf8 командной строкой, а не
        # переменной окружения, — -I стёр бы PYTHONUTF8 вместе с остальными.
        [sys.executable, "-I", "-X", "utf8", str(child),
         str(ROOT / "scripts" / "ops_watchdog.py"), str(ROOT), str(tmp_path)],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, (
        "сторожевой путь не пережил среду без app/, chatter/ и сторонних "
        "пакетов:\n--- stdout ---\n%s\n--- stderr ---\n%s"
        % (proc.stdout, proc.stderr))
    assert "STDLIB-ONLY OK" in proc.stdout, proc.stdout
