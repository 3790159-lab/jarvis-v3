# -*- coding: utf-8 -*-
"""Журнал событий панели: писатель (сторона ops_watchdog).

Спека: docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §2-§3.

⚠️ Тесты здесь НЕ имеют права тянуть app/ или chatter/: ops_watchdog standalone
и stdlib-only by design — он обязан работать, когда мёртво окружение бэкенда.
Импорт тяжёлого пакета в тесте не сломает прод, но скроет нарушение границы в
самом сторожевом коде.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ops_watchdog_under_test", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_spec)
sys.modules["ops_watchdog_under_test"] = ow
_spec.loader.exec_module(ow)


def _probe(ok, reason="r", detail="d"):
    return {"ok": ok, "detail": detail, "reason": reason}


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
