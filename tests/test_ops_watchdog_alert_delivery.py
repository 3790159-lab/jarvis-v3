# -*- coding: utf-8 -*-
"""ДОСТАВКА алертов пер-клиентного сторожа: подавление §4.1 и склейка §4.2.

Спека `docs/superpowers/specs/2026-08-22-per-client-watchdog-probe.md`
(одобренная редакция — коммит `aa63adb2`), контракт «половина ДОСТАВКА».
Половина «наблюдение» уже лежит в ветке: пробы `chatter_runner:<slug>` есть,
и каждая из них — отдельный ключ со своим `fail`, `alerted` и дедупом.

КАКОЙ ДЕФЕКТ ЗАКРЫВАЕТСЯ. Ровно то, что делает пер-клиентные пробы полезными
(свой ключ на клиента), делает их источником шторма: шесть клиентов, умерших
от ОДНОЙ причины — упал гардиан, — дают шесть сообщений подряд. Цена шторма
измерена в этой репе фактом: 25 живых вопросов за 7 минут на прогоне сторожей
хука и 22 подтверждения, нажатые не глядя после 14 вопросов за 3 минуты в 2
ночи. Человек перестаёт читать вовсе, и следующий алерт — настоящий — тонет.
Поэтому §4.1 глушит пер-клиентные `down` на время красноты гардиана, а §4.2
склеивает N переходов одного вида в ОДНО сообщение со списком слагов и причин.

ПОЧЕМУ ЭТО ВАЖНО ИМЕННО ЗДЕСЬ. Оба механизма режут канал доставки, а канал
доставки — единственное, чем сторож вообще отличается от лога. Три способа
испортить его насмерть названы заранее и прибиты ниже поимённо:

  1. подавление, ставящее `alerted`: гардиан поднялся, клиент нет — и клиент
     молчит НАВСЕГДА, потому что «уже сказали»;
  2. склейка, помнящая прошлый цикл: потолок превращается в кулдаун, а
     кулдаун — в «авария одного глушит другого», от чего вся спека и написана;
  3. склейка, съевшая имена: «упало 4 клиента» оставляет владельца без списка,
     кого чинить, — то есть крадёт ровно тот факт, ради которого пер-клиентные
     ключи и заводились.

Плюс два свойства, которые ломаются молча и потому проверяются отдельно:
журнал обязан получить КАЖДЫЙ переход целиком (его читает панель, а не человек
в 2 ночи), а состояние обязано остаться ПЕР-СЛАГОВЫМ — склеивается доставка,
не решения.

Сторожа писались ОТ КОНТРАКТА И СПЕКИ, реализации автор не видел: код пишет
второй автор в отдельном дереве. Смысл пары в том, чтобы тест и код не
унаследовали одно и то же неверное допущение. На момент написания функций
`is_client_check`, `group_alerts`, `build_client_group_alert` и константы
`ALERT_GROUP_MIN` в модуле НЕТ — файл обязан быть красным.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_delivery",
    Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

# Ключ пробы гардиана — тот самый, который регистрирует `probe_all`. Литерал,
# а не импорт: отдельной константы под него в модуле нет. Переименование ключа
# ловит первый же сторож ниже, иначе подавление §4.1 смотрело бы на пробу,
# которой не существует, и было бы выключено МОЛЧА.
GUARDIAN = "chatter_guardian"
DEBOUNCE = 2


def _ck(slug: str) -> str:
    """Ключ пер-клиентной пробы по слагу."""
    return ow.CLIENT_PROBE_PREFIX + slug


def _red(reason: str, detail: str | None = None) -> dict:
    return {"ok": False, "reason": reason,
            "detail": detail if detail is not None else "деталь: " + reason}


def _green(detail: str = "живий") -> dict:
    return {"ok": True, "detail": detail}


def _armed() -> dict:
    """Состояние «один красный цикл уже был»: следующий красный берёт порог."""
    return {"fail": DEBOUNCE - 1, "alerted": False}


def _announced(reason: str) -> dict:
    """Состояние «о падении владельцу уже сказали»."""
    return {"fail": DEBOUNCE, "alerted": True, "alerted_reason": reason}


def _tr(check: str, kind: str, reason: str, detail: str | None = None,
        ts: float = 1_755_000_000.0) -> dict:
    """Переход РОВНО той формы, которую отдаёт `_transitions` (§2.2 спеки)."""
    return {"ts": ts, "check": check, "kind": kind, "reason": reason,
            "detail": detail if detail is not None else "деталь: " + reason}


def _mentioning(texts, needle):
    return [t for t in texts if needle in t]


# Три клиента с ТРЕМЯ РАЗНЫМИ причинами: причины чинятся по-разному, и склейка,
# которая назовёт одну на всех, украдёт факт.
TRIO = (("volska", "no_process"),
        ("yarina", "stale_heartbeat"),
        ("olga", "no_heartbeat"))


# ── контракт: имена, которые обязаны существовать ─────────────────────────
def test_guardian_probe_key_is_the_one_the_module_knows():
    assert GUARDIAN in ow.LABELS, (
        "ключ пробы гардиана переименован: подавление §4.1 вычисляется из "
        "самих probes по этому ключу и при рассинхроне выключится МОЛЧА — "
        "шторм вернётся, а красного не будет нигде")


def test_alert_group_min_is_two():
    assert ow.ALERT_GROUP_MIN == 2, (
        "порог склейки съехал: контракт фиксирует ALERT_GROUP_MIN = 2, и на "
        "нём стоят обе границы ниже (2 перехода — два сообщения, 3 — одно)")


@pytest.mark.parametrize("check, expected", [
    (ow.CLIENT_PROBE_PREFIX + "volska", True),
    (ow.CLIENT_PROBE_PREFIX + "yarina", True),
    # Легаси-ключ БЕЗ двоеточия — не клиентский: он про «раннер вообще», и
    # подавить его значило бы заглушить единственную пробу старого окружения.
    ("chatter_runner", False),
    ("chatter_guardian", False),
    ("chatter_beat_legacy", False),
    ("chatter_roster", False),
    ("backend", False),
    ("", False),
])
def test_is_client_check_matches_only_prefixed_keys(check, expected):
    assert ow.is_client_check(check) is expected, (
        "ключ %r отнесён не к той половине: слишком широкое правило глушит "
        "чужие пробы под красным гардианом, слишком узкое оставляет шторм" % (check,))


# ── §4.1. Подавление под красным гардианом ────────────────────────────────
def test_client_down_is_silent_under_red_guardian_and_loud_under_green():
    probes_red = {GUARDIAN: _red("dead_pid"), _ck("volska"): _red("no_process")}
    alerts_red, _state = ow.evaluate(
        {GUARDIAN: {"fail": 0, "alerted": False}, _ck("volska"): _armed()},
        probes_red, debounce=DEBOUNCE)
    assert alerts_red == [], (
        "при красной пробе chatter_guardian в ЭТОМ ЖЕ цикле пер-клиентный down "
        "владельцу уходить не имеет права: шесть клиентов под мёртвым гардианом "
        "дадут шесть сообщений об одной аварии; получено %r" % (alerts_red,))

    probes_green = {GUARDIAN: _green(), _ck("volska"): _red("no_process")}
    alerts_green, _state2 = ow.evaluate(
        {_ck("volska"): _armed()}, probes_green, debounce=DEBOUNCE)
    assert _mentioning(alerts_green, "volska"), (
        "подавление сработало при ЖИВОМ гардиане: смерть одного клиента при "
        "здоровой ферме — ровно то событие, ради которого сторож и заведён")


def test_suppressed_client_down_goes_to_the_journal_but_not_to_the_owner():
    probes = {GUARDIAN: _red("dead_pid"),
              _ck("volska"): _red("no_process", "процес раннера не знайдено")}
    journal, to_owner, _state = ow._transitions(
        {GUARDIAN: {"fail": 0, "alerted": False}, _ck("volska"): _armed()},
        probes, DEBOUNCE)

    mine = [r for r in journal if r["check"] == _ck("volska")]
    assert len(mine) == 1, (
        "подавлённое падение клиента обязано попасть в ЖУРНАЛ ровно один раз: "
        "журнал читает панель, и падение, о котором смолчали, — ровно то "
        "событие, ради которого журнал и заводится; записей %d" % len(mine))
    rec = mine[0]
    assert set(rec) == {"ts", "check", "kind", "reason", "detail"}, (
        "запись журнала потеряла или добавила поле: панель разбирает РОВНО "
        "пять полей §2.2; получено %r" % sorted(rec))
    assert rec["reason"] == "no_process", (
        "причина падения в журнал не доехала — панель не сможет отличить "
        "no_process от stale_heartbeat, а чинятся они по-разному")
    assert rec["detail"] == "процес раннера не знайдено", (
        "деталь падения обрезана: подавление глушит КАНАЛ ВЛАДЕЛЬЦА, "
        "а не содержание записи")

    assert [r for r in to_owner if r["check"] == _ck("volska")] == [], (
        "подавлённый переход остался в списке для владельца — подавление §4.1 "
        "не сработало вовсе либо сработало только на тексте")


def test_suppression_does_not_mark_the_client_as_alerted():
    alerts, state = ow.evaluate(
        {GUARDIAN: {"fail": 0, "alerted": False}, _ck("volska"): _armed()},
        {GUARDIAN: _red("dead_pid"), _ck("volska"): _red("no_process")},
        debounce=DEBOUNCE)
    st = state[_ck("volska")]
    assert st.get("alerted") is False, (
        "ГЛАВНЫЙ дефект этой работы: подавление поставило alerted. Гардиан "
        "поднимется, клиент — нет, и владелец не узнает об этом НИКОГДА, "
        "потому что «уже сказали». Подавление считает и молчит, но не помнит")
    assert st["fail"] == DEBOUNCE, (
        "счётчик fail под подавлением расти обязан: иначе после возврата "
        "гардиана дебаунс придётся набирать заново, и алерт опоздает на цикл")
    assert alerts == [], "под красным гардианом пер-клиентный down владельцу не уходит"


def test_client_that_stayed_down_alerts_on_the_cycle_after_guardian_returns():
    prev = {GUARDIAN: {"fail": 0, "alerted": False}, _ck("volska"): _armed()}
    alerts1, state1 = ow.evaluate(
        prev, {GUARDIAN: _red("dead_pid"), _ck("volska"): _red("no_process")},
        debounce=DEBOUNCE)
    assert _mentioning(alerts1, "volska") == [], (
        "цикл 1 (гардиан красный): про клиента владельцу молчим")

    alerts2, _state2 = ow.evaluate(
        state1, {GUARDIAN: _green(), _ck("volska"): _red("no_process")},
        debounce=DEBOUNCE)
    hits = _mentioning(alerts2, "volska")
    assert len(hits) == 1, (
        "гардиан вернулся, а клиент не поднялся — алерт про клиента обязан "
        "уйти НА СЛЕДУЮЩЕМ ЖЕ цикле. Ровно одно сообщение, ни нуля (подавление "
        "оставило alerted и клиент онемел), ни двух; получено %r" % (alerts2,))


def test_red_guardian_suppresses_client_downs_only():
    prev = {
        GUARDIAN: _armed(),                        # гардиан сам берёт порог
        "disk": _armed(),                          # чужая проба берёт порог
        _ck("volska"): _armed(),                   # клиент падает
        _ck("yarina"): _announced("no_process"),   # клиент поднимается
    }
    probes = {
        GUARDIAN: _red("dead_pid"),
        "disk": _red("low_space"),
        _ck("volska"): _red("no_process"),
        _ck("yarina"): _green(),
    }
    alerts, _state = ow.evaluate(prev, probes, debounce=DEBOUNCE)

    assert _mentioning(alerts, ow.LABELS[GUARDIAN]), (
        "подавление съело алерт САМОГО гардиана — владельцу не сказали про "
        "корневую аварию, ради тишины о её следствиях")
    assert _mentioning(alerts, ow.LABELS["disk"]), (
        "подавление задело НЕ-клиентскую пробу: красный гардиан не имеет "
        "отношения к месту на диске, и глушить его нечем")
    assert _mentioning(alerts, "yarina"), (
        "подавление задело recovered: подавляется ТОЛЬКО down. ✅ о подъёме "
        "клиента — единственное, что закрывает инцидент на глазах у владельца")
    assert _mentioning(alerts, "volska") == [], (
        "пер-клиентный down под красным гардианом ушёл владельцу — §4.1 не "
        "работает; полный список: %r" % (alerts,))


# ── §4.2. Склейка: group_alerts / build_client_group_alert ────────────────
def test_group_alerts_glues_more_than_min_client_downs_into_one_text():
    texts = ow.group_alerts([_tr(_ck(s), "down", r) for s, r in TRIO])
    assert len(texts) == 1, (
        "три пер-клиентных down в ОДНОМ цикле обязаны стать одним сообщением "
        "(ALERT_GROUP_MIN = 2, сравнение строгое); получено %r" % (texts,))


def test_group_alerts_keeps_exactly_two_client_downs_apart():
    texts = ow.group_alerts([_tr(_ck(s), "down", r) for s, r in TRIO[:2]])
    assert len(texts) == 2, (
        "граница СТРОГАЯ: ровно ALERT_GROUP_MIN = 2 переходов склейке не "
        "подлежат. `>=` вместо `>` начнёт сворачивать пары в список, где "
        "каждое имя ещё читается по отдельности; получено %r" % (texts,))


def test_glued_text_names_every_slug_and_its_reason():
    texts = ow.group_alerts([_tr(_ck(s), "down", r) for s, r in TRIO])
    assert len(texts) == 1, "предусловие: три перехода склеиваются в один текст"
    glued = texts[0]
    for slug, reason in TRIO:
        assert slug in glued, (
            "склейка съела имя %r: «упало 3 клиента» без имён оставляет "
            "владельца без списка, кого чинить, — то есть отменяет пер-слаговые "
            "ключи, ради которых вся спека и писалась; текст: %r" % (slug, glued))
        assert reason in glued, (
            "склейка съела причину %r клиента %r: no_process (раннер не "
            "стартовал) и stale_heartbeat (раннер завис) чинятся по-разному, "
            "и без причины сообщение не действие, а тревога" % (reason, slug))


def test_down_and_recovered_are_counted_and_glued_separately():
    downs = [_tr(_ck(s), "down", r) for s, r in TRIO]
    ups = [_tr(_ck(s), "recovered", _ck(s)) for s in ("marina", "nadia")]
    texts = ow.group_alerts(downs + ups)

    assert len(texts) == 3, (
        "три down склеиваются в одно сообщение, а ДВА recovered порога не "
        "берут и остаются отдельными: итого три текста. Общий счёт по видам "
        "(5 > 2) склеил бы падения с подъёмами в одну строку, где непонятно, "
        "что случилось; получено %r" % (texts,))
    glued = [t for t in texts if all(s in t for s, _ in TRIO)]
    assert len(glued) == 1, (
        "склеенного сообщения про все три падения нет; тексты: %r" % (texts,))
    for slug in ("marina", "nadia"):
        assert slug not in glued[0], (
            "подъём клиента %r затесался в склейку ПАДЕНИЙ — владелец прочтёт "
            "«упали», а клиент на самом деле поднялся" % slug)
    rest = [t for t in texts if t != glued[0]]
    for slug in ("marina", "nadia"):
        assert _mentioning(rest, slug), (
            "recovered клиента %r потерялся при склейке down" % slug)


def test_non_client_transitions_keep_their_own_text_and_come_first():
    others = [_tr("disk", "down", "low_space", "3.1GB free (min 10.0GB)"),
              _tr("backend", "recovered", "no_response", "HTTP 200")]
    texts = ow.group_alerts(others + [_tr(_ck(s), "down", r) for s, r in TRIO])

    expected = [ow.build_alert(t["check"], t["kind"], t["detail"]) for t in others]
    assert texts[:len(expected)] == expected, (
        "не-клиентские алерты обязаны остаться ДОСЛОВНО прежними (через "
        "build_alert) и стоять первыми: склейка — надстройка над доставкой "
        "клиентов, а не переписывание всех текстов; получено %r" % (texts,))
    assert len(texts) == len(expected) + 1, (
        "к двум чужим текстам должен добавиться ровно один склеенный "
        "клиентский; получено %r" % (texts,))


def test_group_alerts_does_not_remember_or_mutate_anything():
    trs = [_tr(_ck(s), "down", r) for s, r in TRIO]
    snapshot = copy.deepcopy(trs)

    first = ow.group_alerts(trs)
    second = ow.group_alerts(trs)
    assert first == second, (
        "склейка помнит прошлый вызов и на втором отдаёт другое — потолок "
        "превратился в КУЛДАУН, а кулдаун в «авария одного глушит другого», "
        "от чего вся спека и написана; %r против %r" % (first, second))
    assert trs == snapshot, (
        "склейка правит входные переходы: те же объекты уходят в журнал, и "
        "панель прочтёт их искажёнными")


def test_build_client_group_alert_names_kind_slugs_and_reasons():
    trs = [_tr(_ck(s), "down", r) for s, r in TRIO]
    down_text = ow.build_client_group_alert("down", trs)
    up_text = ow.build_client_group_alert("recovered", trs)

    assert isinstance(down_text, str) and down_text.strip(), (
        "склеенное сообщение обязано быть непустой строкой, готовой к отправке")
    for slug, reason in TRIO:
        assert slug in down_text, (
            "слаг %r не назван в склеенном сообщении" % slug)
        assert reason in down_text, (
            "причина %r клиента %r не названа в склеенном сообщении" % (reason, slug))
    assert down_text != up_text, (
        "вид перехода в склеенном сообщении не назван: одинаковый текст про "
        "падение и про подъём — это сообщение, из которого нельзя понять, "
        "стало хуже или лучше")


# ── §4.2 через evaluate: журнал, состояние, следующий цикл ────────────────
def _trio_cycle():
    prev = {_ck(s): _armed() for s, _ in TRIO}
    prev[GUARDIAN] = {"fail": 0, "alerted": False}
    probes = {GUARDIAN: _green()}
    probes.update({_ck(s): _red(r) for s, r in TRIO})
    return prev, probes


def test_evaluate_glues_client_downs_while_journal_keeps_each_one():
    prev, probes = _trio_cycle()

    alerts, _state = ow.evaluate(prev, probes, debounce=DEBOUNCE)
    assert len(alerts) == 1, (
        "владельцу обязано уйти ОДНО сообщение вместо трёх: три подряд — это "
        "не наблюдаемость, а шум, после которого перестают читать; "
        "получено %r" % (alerts,))

    journal, _new = ow.transitions(prev, probes, DEBOUNCE)
    downs = [r for r in journal if r["check"].startswith(ow.CLIENT_PROBE_PREFIX)]
    assert len(downs) == 3, (
        "склейка тронула ЖУРНАЛ: в него каждый переход пишется ОТДЕЛЬНО и "
        "полностью — его читает панель, а не человек в 2 ночи; записей %d"
        % len(downs))
    assert {r["check"] for r in downs} == {_ck(s) for s, _ in TRIO}, (
        "в журнале не все три клиента поимённо")
    for rec in downs:
        assert set(rec) == {"ts", "check", "kind", "reason", "detail"}, (
            "запись журнала потеряла или добавила поле: %r" % sorted(rec))
        assert rec["reason"] in dict(TRIO).values(), (
            "причина в журнальной записи подменена общей на всех: %r" % rec)


def test_glued_delivery_still_writes_per_slug_state():
    prev, probes = _trio_cycle()
    alerts, state = ow.evaluate(prev, probes, debounce=DEBOUNCE)

    assert len(alerts) == 1, "предусловие: три падения склеены в одно сообщение"
    for slug, reason in TRIO:
        st = state[_ck(slug)]
        assert st.get("alerted") is True, (
            "склейка съела alerted у %r: склеивается ДОСТАВКА, состояние "
            "остаётся пер-слаговым, иначе следующий цикл повторит алерт" % slug)
        assert st.get("alerted_reason") == reason, (
            "дедуп по причине перестал быть пер-слаговым у %r: ожидалось %r, "
            "в состоянии %r. Одна причина на всех — и смена причины у одного "
            "клиента либо промолчит, либо разошлёт всех" % (slug, reason, st))
        assert st.get("fail") == DEBOUNCE, (
            "счётчик fail у %r не пер-слаговый: авария соседа «дозаряжает» "
            "чужой дебаунс" % slug)


def test_grouping_is_not_a_cooldown_next_cycle_client_gets_its_own_alert():
    prev, probes1 = _trio_cycle()
    probes1[_ck("marina")] = _red("no_process")   # первый красный цикл, порог не взят

    alerts1, state1 = ow.evaluate(prev, probes1, debounce=DEBOUNCE)
    assert len(alerts1) == 1, (
        "цикл 1: три падения обязаны склеиться в одно сообщение; "
        "получено %r" % (alerts1,))
    assert "marina" not in alerts1[0], (
        "marina в цикле 1 ещё не взяла дебаунс — её имени в сообщении быть "
        "не должно: склейка не имеет права обходить дебаунс")

    alerts2, _state2 = ow.evaluate(state1, dict(probes1), debounce=DEBOUNCE)
    assert len(alerts2) == 1, (
        "цикл 2: три старых падения с ТОЙ ЖЕ причиной дедупятся ядром, а "
        "marina обязана получить СВОЙ алерт. Склейка, помнящая прошлый цикл, "
        "превращается в кулдаун — «авария одного глушит другого»; "
        "получено %r" % (alerts2,))
    assert "marina" in alerts2[0], (
        "клиент, упавший СЛЕДУЮЩИМ циклом, остался без алерта — доставка "
        "запомнила прошлый цикл; получено %r" % (alerts2,))


def test_reason_change_of_one_client_alerts_that_client_alone():
    prev, probes1 = _trio_cycle()
    alerts1, state1 = ow.evaluate(prev, probes1, debounce=DEBOUNCE)
    assert len(alerts1) == 1, (
        "предусловие: цикл 1 — три падения одним склеенным сообщением; "
        "получено %r" % (alerts1,))

    probes2 = {GUARDIAN: _green()}
    # volska: раннер уже не «не стартовал», а завис — ДРУГАЯ авария.
    probes2[_ck("volska")] = _red("stale_heartbeat")
    probes2[_ck("yarina")] = _red("stale_heartbeat")
    probes2[_ck("olga")] = _red("no_heartbeat")

    alerts2, _state2 = ow.evaluate(state1, probes2, debounce=DEBOUNCE)
    assert len(alerts2) == 1, (
        "смена причины у ОДНОГО клиента обязана дать РОВНО один алерт: дедуп "
        "по причине остаётся пер-слаговым и склейкой не отменяется — иначе "
        "либо смолчим о новой аварии, либо разошлём всех троих; "
        "получено %r" % (alerts2,))
    assert "volska" in alerts2[0], (
        "алерт о смене причины назвал не того клиента: %r" % (alerts2,))
    assert "stale_heartbeat" in alerts2[0], (
        "новая причина в сообщении не названа — владельцу нечего чинить")


# ── §4.1 + §4.2 в одном цикле ─────────────────────────────────────────────
def test_suppressed_client_downs_never_reach_the_group():
    prev, probes = _trio_cycle()
    prev["disk"] = _armed()
    probes[GUARDIAN] = _red("dead_pid")
    probes["disk"] = _red("low_space")

    alerts, _state = ow.evaluate(prev, probes, debounce=DEBOUNCE)
    assert alerts == [ow.build_alert("disk", "down", "деталь: low_space")], (
        "подавлённые переходы обязаны выпасть ДО склейки: владельцу они не "
        "уходят вовсе — ни поштучно, ни одной склеенной строкой. Склейка "
        "поверх подавления вернула бы шторм в виде одного сообщения о том, "
        "о чём договорились молчать; получено %r" % (alerts,))
