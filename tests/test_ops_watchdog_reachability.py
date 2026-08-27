# -*- coding: utf-8 -*-
"""§5.1 спеки `2026-08-27-alarm-channel-outside-the-broken-transport.md`:
проба `reachability` — РАЗДЕЛЬНЫЕ вердикты на каждый провод.

ПОВОД. 26.08 13:23 — 27.08 14:13 (20 ч 37 мин) машина была жива и полностью
отрезана от внешнего мира. Девятнадцать проб были зелёные: все они меряли
«жив», ни одна — «на связи». Избыточность считалась по числу проб (19), а
надо было считать по числу проводов (1).

ЧТО СТЕРЕЖЁТСЯ. Спека называет три провода поимённо (§5.1):

    | tg_api  | `getMe` к Bot API прошёл             |
    | llm_api | минимальный вызов Anthropic прошёл   |
    | r2      | `list_objects` по бакету бэкапов     |

и требует: «Вердикты РАЗДЕЛЬНЫЕ, не агрегат: „два из трёх“ обязано читаться
как „два из трёх“, а не как зелёное». Агрегат «сеть жива» — ровно та
успокаивающая лампа, с которой всё и началось.

ПОЧЕМУ СПИСОК ЗДЕСЬ ЛИТЕРАЛЬНЫЙ, а не выведенный из кода. Интроспекция
(«взять все ключи, начинающиеся на reachability») согласна с кодом ПО
ОПРЕДЕЛЕНИЮ и промолчит ровно там, где код забыл провод
([[jarvis-literal-lists-not-introspection]]). Поэтому `WIRES` — литерал из
таблицы спеки, и сверка идёт РАВЕНСТВОМ МНОЖЕСТВ: ни одного лишнего, ни
одного забытого. Забытый провод — это дыра размером в аварию; лишний —
признак того, что состав проводов разъехался со спекой.

ГДЕ СТОИТ СТОРОЖ. На ВЫВОДЕ `probe_all()` и на текстах, которые получит
владелец, — а не на внутреннем устройстве пробы. Форма снимка спекой не
зафиксирована, и сторож, стоящий на ней, краснел бы на выборе имени поля, а
не на смысле. Снимок ниже поэтому намеренно ИЗБЫТОЧЕН (провода лежат и
плоско, и под `terms`), чтобы красное означало логику.

Сторожа писались ОТ ТЕКСТА СПЕКИ, реализации автор не видел
([[jarvis-guards-not-by-the-plan-author]]). На момент написания пробы
`reachability` в модуле НЕТ — файл ОБЯЗАН быть красным.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_reachability", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ow
_SPEC.loader.exec_module(ow)


# ── контракт спеки, литералом ──────────────────────────────────────────────
# Таблица §5.1, слово в слово. Правка этого кортежа обязана быть осознанной:
# он — единственное место, где состав проводов зафиксирован НЕЗАВИСИМО от кода.
WIRES = ("tg_api", "llm_api", "r2")
# Форма ключа по контракту: `reach:<провод>` — как `escalation:volska` и
# `outgoing:volska` у соседних семейств. Точка вместо двоеточия принимается
# тоже: §6 спеки пишет `reachability.tg_api`, и сторож стоит на РАЗДЕЛЬНОСТИ
# вердиктов, а не на пунктуации.
FAMILY = "reach"
_KEY_RE = re.compile(r"^%s[.:](?P<wire>.+)$" % FAMILY)

# По чему в СООБЩЕНИИ владельцу узнаётся провод. Не одно слово: ярлык пробы
# вправе назвать провод по-человечески («Telegram Bot API»), и требовать в нём
# ровно `tg_api` значило бы требовать имя переменной в тексте для человека.
# Требуется одно: сообщение обязано отличать провод от двух других.
WIRE_MARKERS = {
    "tg_api": ("tg_api", "telegram", "bot api"),
    "llm_api": ("llm_api", "anthropic", "llm", "модел"),
    "r2": ("r2", "бэкап", "бекап", "бакет"),
}

ALL_GREEN = {w: True for w in WIRES}
ALL_RED = {w: False for w in WIRES}
TG_ONLY_RED = {"tg_api": False, "llm_api": True, "r2": True}


def _no_http(path):
    """Бэкенд не отвечает — минимум посторонних проб в цикле."""
    return None


def _big_disk(path):
    return (1, 1, 10 * 1024 ** 3)


def _snapshot_kwarg() -> str:
    """Имя параметра `probe_all`, которым приезжает снимок связи.

    Ищется по подстроке `reach`, а не пинится литералом: имя параметра —
    плумбинг, спекой он не назван, и красным обязан быть СОСТАВ проб, а не
    выбор слова. Отсутствие параметра — красное с внятной причиной.
    """
    hits = [n for n in inspect.signature(ow.probe_all).parameters if "reach" in n]
    if not hits:
        pytest.fail(
            "probe_all() не принимает снимок reachability (§5.1): проба «на "
            "связи» в цикле отсутствует, все пробы по-прежнему меряют только "
            "«жив». Параметры: %s"
            % list(inspect.signature(ow.probe_all).parameters))
    assert len(hits) == 1, "два параметра про связь — два источника правды: %s" % hits
    return hits[0]


def _snapshot(**wires):
    """Снимок связи. Намеренно избыточен — см. докстринг файла."""
    terms = {}
    for wire, ok in wires.items():
        terms[wire] = {
            "ok": bool(ok),
            "detail": ("проверка %s прошла" % wire) if ok
                      else ("%s не отвечает: TLS handshake failed" % wire),
            "error": None if ok else "TLS handshake failed",
        }
    snap = {"ts": time.time(), "terms": dict(terms)}
    snap.update(terms)          # та же правда плоско: форма спекой не задана
    return snap


def _wire_probes(**wires) -> dict:
    """Пробы, ДОБАВЛЕННЫЕ снимком связи, — разностью с циклом без снимка.

    Разность, а не фильтр по имени: агрегат, названный `network_ok` или
    `online`, фильтр по префиксу `reachability` не поймал бы вовсе — он бы
    просто не попал в выборку, и сторож промолчал бы о нём.
    """
    base = set(ow.probe_all(_no_http, _big_disk))
    got = ow.probe_all(_no_http, _big_disk,
                       **{_snapshot_kwarg(): _snapshot(**wires)})
    return {k: v for k, v in got.items() if k not in base}


# ── §5.1: состав проводов, литерально и в ОБЕ стороны ──────────────────────
@pytest.mark.parametrize("wires, case", [
    (ALL_GREEN, "все три живы"),
    (TG_ONLY_RED, "два из трёх"),
    (ALL_RED, "все три мертвы"),
])
def test_every_wire_gets_its_own_verdict_and_no_extra_ones(wires, case):
    """Ровно три вердикта, по одному на провод, — при ЛЮБОМ раскладе.

    Раскладов три намеренно. Проба, которая при красном проводе просто
    ВЫБРАСЫВАЕТ его ключ, на зелёном снимке выглядела бы правильной: состав
    обязан быть одинаков и на зелёном, и на красном, иначе «провод молчит»
    неотличимо от «провода нет».
    """
    added = _wire_probes(**wires)
    seen = {}
    for key in added:
        m = _KEY_RE.match(key)
        assert m, (
            "ключ %r не назван проводом (%s). Это агрегат: «сеть жива» — ровно "
            "та успокаивающая лампа, ради отказа от которой §5.1 и написан"
            % (key, case))
        wire = m.group("wire")
        assert wire not in seen, "провод %r назван дважды: %r и %r" % (
            wire, seen.get(wire), key)
        seen[wire] = key

    missing = set(WIRES) - set(seen)
    extra = set(seen) - set(WIRES)
    assert not missing, (
        "провод забыт (%s): %s. Забытый провод — это дыра ровно того размера, "
        "какой была авария 26–27.08" % (case, sorted(missing)))
    assert not extra, (
        "лишние вердикты (%s): %s — состав проводов разъехался с таблицей §5.1"
        % (case, sorted(extra)))
    assert len(added) == len(WIRES), (
        "добавлено %d проб на три провода (%s): %s"
        % (len(added), case, sorted(added)))


def test_there_is_no_aggregate_verdict_next_to_the_three():
    """Отдельно и в лоб: голого `reachability` быть не должно.

    Агрегат РЯДОМ с тремя термами хуже агрегата вместо них: зелёная лампа,
    стоящая возле красной, гасит именно красную
    ([[jarvis-loud-failure-next-to-a-soothing-lamp]]).
    """
    added = _wire_probes(**TG_ONLY_RED)
    for aggregate in (FAMILY, "reachability"):
        assert aggregate not in added, (
            "рядом с тремя проводами стоит агрегат %r — «два из трёх» снова "
            "читается как одно число" % aggregate)


# ── §5.1: «два из трёх» читается как два из трёх ───────────────────────────
@pytest.mark.parametrize("broken", WIRES)
def test_one_dead_wire_reddens_itself_and_only_itself(broken):
    """Каждый провод обязан уметь покраснеть В ОДИНОЧКУ.

    Параметризация по литеральному списку — вторая половина сверки состава:
    провод, попавший в ключи, но не влияющий на вердикт, ловится здесь.
    """
    wires = {w: (w != broken) for w in WIRES}
    added = _wire_probes(**wires)
    by_wire = {_KEY_RE.match(k).group("wire"): v for k, v in added.items()}

    assert by_wire[broken]["ok"] is False, (
        "провод %s не отвечает, а вердикт по нему зелёный" % broken)
    for w in WIRES:
        if w == broken:
            continue
        assert by_wire[w]["ok"] is True, (
            "провод %s жив, но покраснел заодно с %s — это агрегат, "
            "переодетый в три ключа" % (w, broken))


def test_two_dead_wires_are_two_red_verdicts_not_one():
    added = _wire_probes(tg_api=False, llm_api=False, r2=True)
    reds = [k for k, v in added.items() if not v["ok"]]
    assert len(reds) == 2, "два мёртвых провода дали %d красных: %s" % (
        len(reds), reds)


def test_each_verdict_has_the_shape_the_pipeline_consumes():
    """`ok` + `detail`: их читают `_transitions` и `build_alert`.

    Вердикт без `detail` доезжает до владельца пустым — «упало» без «что
    именно», то есть ровно тот случай, когда красное неотличимо от шума.
    """
    for key, verdict in _wire_probes(**TG_ONLY_RED).items():
        assert isinstance(verdict, dict), "%s: вердикт не словарь" % key
        assert "ok" in verdict, "%s: нет `ok`" % key
        assert isinstance(verdict.get("detail"), str) and verdict["detail"].strip(), (
            "%s: вердикт без внятного `detail`" % key)


def test_the_red_verdict_names_its_own_wire():
    added = _wire_probes(**TG_ONLY_RED)
    key, verdict = next((k, v) for k, v in added.items() if not v["ok"])
    assert "tg_api" in key or "tg_api" in verdict["detail"], (
        "красный вердикт не называет провод: %r / %r" % (key, verdict["detail"]))


# ── §5.1 на выходе канала: владелец слышит ТРИ новости, а не одну ──────────
def _alerts_for(probes):
    """Тексты владельцу после дебаунса (два цикла подряд с той же картиной)."""
    state, out = {}, []
    for _ in range(2):
        alerts, state = ow.evaluate(state, probes, debounce=2)
        out += alerts
    return out


def _names_wire(text: str, wire: str) -> bool:
    low = text.lower()
    return any(m in low for m in WIRE_MARKERS[wire])


def test_all_wires_green_is_silent():
    assert _alerts_for(_wire_probes(**ALL_GREEN)) == []


def test_one_dead_wire_gives_exactly_one_alert_naming_it():
    alerts = _alerts_for(_wire_probes(**TG_ONLY_RED))
    assert len(alerts) == 1, "один мёртвый провод дал %d сообщений: %s" % (
        len(alerts), alerts)
    assert _names_wire(alerts[0], "tg_api"), (
        "сообщение не называет провод — владелец не знает, что чинить: %r"
        % alerts[0])
    for alive in ("llm_api", "r2"):
        assert alive not in alerts[0], (
            "в сообщении о мёртвом tg_api помянут живой %s: %r"
            % (alive, alerts[0]))


def test_three_dead_wires_give_three_alerts_not_one_grouped():
    """Склейка §4.2 к проводам применяться НЕ ИМЕЕТ ПРАВА.

    Она склеивает ОДНОРОДНОЕ (шесть клиентов, умерших от одной причины —
    упал гардиан). Три провода однородны только на вид: Telegram, Anthropic и
    R2 ломаются порознь и чинятся порознь, а «упало 3 провода» — это снова
    одно число вместо трёх, то есть агрегат, добытый с другого конца.
    """
    alerts = _alerts_for(_wire_probes(**ALL_RED))
    assert len(alerts) == len(WIRES), (
        "три мёртвых провода дали %d сообщений: %s" % (len(alerts), alerts))
    for wire in WIRES:
        assert any(_names_wire(a, wire) for a in alerts), (
            "в сообщениях не назван провод %s: %s" % (wire, alerts))


def test_a_recovered_wire_reports_itself_alone():
    """Обратный ход §6.4: зелёное возвращается ПОИМЁННО.

    ✅ «связь восстановлена» при двух ещё мёртвых проводах — тот же агрегат,
    только с приятной стороны.
    """
    state = {}
    for _ in range(2):
        _a, state = ow.evaluate(state, _wire_probes(**ALL_RED), debounce=2)
    alerts, _state = ow.evaluate(
        state, _wire_probes(tg_api=True, llm_api=False, r2=False), debounce=2)
    assert len(alerts) == 1, "поднялся один провод, сообщений %d: %s" % (
        len(alerts), alerts)
    assert _names_wire(alerts[0], "tg_api"), alerts[0]
    assert "✅" in alerts[0], "подъём не помечен как подъём: %r" % alerts[0]
