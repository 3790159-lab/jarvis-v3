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
    prev = {"worktree": {"fail": 1669, "alerted": True,
                         "alerted_reason": "dirty:a.yaml"}}
    probes = {"worktree": _probe(False, "dirty:a.yaml", "модифицировано 1")}
    trs, _st = ow.transitions(prev, probes, debounce=2, suppress_down=True)
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
