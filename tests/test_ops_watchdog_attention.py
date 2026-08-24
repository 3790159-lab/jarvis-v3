# -*- coding: utf-8 -*-
"""Часть 2 арки: проба «карточки эскалации не читают» у `ops_watchdog`.

Спека `docs/superpowers/specs/2026-08-24-escalation-seen-probe.md` (§2, §5,
§5.1, §5.2, §6, §7, §9), контракт «проба карточки эскалации не читают» §3–§6.

СТОРОЖА ПИСАНЫ ОТ СПЕКИ И КОНТРАКТА. Реализацию писал другой автор в другом
дереве; его кода автор этих сторожей не видел и не искал. Смысл разделения
назван в [[jarvis-guards-not-by-the-plan-author]]: иначе тест и код наследуют
ОДНО неверное допущение, и оба молчат в одном и том же месте.

ЧЕТЫРЕ ВЕЩИ, КОТОРЫЕ ЗДЕСЬ ОХРАНЯЮТСЯ, и все четыре ломаются ТИХО:

1. СОСТАВ проб равен ВКЛЮЧЁННЫМ клиентам РОСТЕРА, а не списку инстансов.
   Инстанс сегодня один (yarina), а три из четырёх открытых карточек — у
   volska, включая самую старую. Проба «по инстансам» была бы ЗЕЛЁНОЙ ПО
   ПОСТРОЕНИЮ ровно там, где лежит вся проблема.
2. `bad_payload` КРАСНЫЙ. Проба, которая при любой поломке ручки говорит
   «карточки читают», хуже отсутствующей — это зелёное по построению.
3. АДРЕС собирается ОДНИМ вызовом резолвера на обе пробы. Доказывается
   ПОДМЕНОЙ инъектируемого модуля, а не совпадением чисел сегодня: два числа
   на одну вещь разъедутся в день смены адреса тайнета.
4. ГРАНИЦЫ уже существующего кода: ярлык нового семейства не «раннер»
   (§6.1), подавление §4.1 новое семейство НЕ захватывает (§6.2), склейка
   §4.2 не смешивает семейства (§6.3).

Имена берутся через `getattr(..., умолчание)` НАМЕРЕННО: пока реализации нет,
обращение к отсутствующей константе на уровне модуля сорвало бы СБОР всего
файла, и вместо тридцати честно красных сторожей была бы одна ошибка
коллекции — то есть ноль измеренных утверждений. Существование самих имён
пиннится отдельными сторожами ниже.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"

_spec = _ilu.spec_from_file_location("ops_watchdog_attention", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)

# Имена контракта §3 — литералами, а не выведенные из модуля.
ATT_PREFIX_EXPECTED = "escalation:"
ATT_PATH_EXPECTED = "/ops/attention"

ATT_PREFIX = getattr(ow, "ATTENTION_PROBE_PREFIX", ATT_PREFIX_EXPECTED)
ATT_PATH = getattr(ow, "ATTENTION_PATH", ATT_PATH_EXPECTED)

# Слаг, у которого инстанс ЕСТЬ, и слаг, у которого его нет. Берутся из
# константы модуля, а не литералом: правило контракта §4 звучит как
# «slug == PANEL_CLIENT_SLUG → спрашиваем, иначе no_instance», и сторож обязан
# ехать за этой константой, а не за сегодняшним значением «yarina».
WITH_INSTANCE = ow.PANEL_CLIENT_SLUG
NO_INSTANCE = "volska" if ow.PANEL_CLIENT_SLUG != "volska" else "yarina"
DISABLED = "demo"

HOST = "100.102.179.47"
PORT = ow.PANEL_CLIENT_PORT
DEBOUNCE = 2
GUARDIAN = "chatter_guardian"
RUN_PREFIX = ow.CLIENT_PROBE_PREFIX


def _ak(slug: str) -> str:
    """Ключ пробы эскалаций по слагу."""
    return ATT_PREFIX + slug


def _rk(slug: str) -> str:
    """Ключ пробы раннера по слагу."""
    return RUN_PREFIX + slug


def _payload(open_=3, stale_open=2, age=2_298_240.0, wait=2_298_300.0):
    """Тело ручки контракта §2 — РОВНО четыре ключа."""
    return {"open": open_, "stale_open": stale_open,
            "oldest_age_s": age, "oldest_wait_s": wait}


def _snap(host=HOST, port=PORT, status=200, payload=None, problem=None,
          slug=None, **extra):
    """Снимок для `probe_attention`, по образцу `probe_panel_client`.

    Контракт фиксирует форму снимка ЧАСТИЧНО: «одним позиционным словарём, как
    у `probe_worktree` / `probe_panel_client` / `probe_secrets_bundle`». Поля
    адреса и статуса оттуда взяты дословно; под каким именем в снимке лежит
    РАЗОБРАННОЕ ТЕЛО ответа, контракт не называет — поэтому тело кладётся сразу
    под несколько правдоподобных имён. Сторож пинит ИСХОД пробы, а не
    внутреннее имя ключа снимка: пинить неназванное значило бы краснеть на
    законном выборе автора кода.
    """
    snap = {"host": host, "port": port, "status": status, "problem": problem,
            "slug": WITH_INSTANCE if slug is None else slug}
    for name in ("payload", "body", "json", "data", "attention"):
        snap[name] = payload
    snap.update(extra)
    return snap


def _mentioning(texts, needle):
    return [t for t in texts if needle in t]


# ── §3 контракта: ИМЕНА ────────────────────────────────────────────────────

def test_the_probe_family_prefix_is_the_contract_one():
    """Префикс — ключ дедупа, склейки и ярлыка одновременно. Разъехавшись с
    контрактом, он выключил бы все три механизма молча."""
    assert getattr(ow, "ATTENTION_PROBE_PREFIX", None) == ATT_PREFIX_EXPECTED


def test_the_handle_path_is_named_once_and_correctly():
    assert getattr(ow, "ATTENTION_PATH", None) == ATT_PATH_EXPECTED


def test_probe_attention_takes_one_positional_snapshot():
    """Россыпь именованных аргументов уже стоила вердикта `no_bind_address`
    на ЗДОРОВОЙ панели: снимок уезжал в первый позиционный (`status`), `host`
    оставался пустым. Форма подписи здесь — не стиль, а тот самый дефект."""
    fn = getattr(ow, "probe_attention", None)
    assert fn is not None, "функции `probe_attention` нет вовсе"
    params = list(inspect.signature(fn).parameters.values())
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(positional) == 1, (
        "у `probe_attention` %d позиционных параметров вместо одного снимка: %s"
        % (len(positional), [p.name for p in params]))
    assert positional[0].name == "snapshot", positional[0].name


def test_probe_all_gained_attention_snapshot_as_its_last_parameter():
    """`None` = проб этого семейства в цикле НЕТ вовсе: watchdog не имеет
    права слать DOWN о том, чего не мерил."""
    params = list(inspect.signature(ow.probe_all).parameters.values())
    assert params[-1].name == "attention_snapshot", (
        "последний параметр `probe_all` — %r, а контракт §3 требует "
        "`attention_snapshot` последним, по образцу `restore_drill_snapshot`"
        % (params[-1].name,))
    assert params[-1].default is None, params[-1].default


def test_the_snapshot_collector_exists_under_its_contract_name():
    assert callable(getattr(ow, "_attention_snapshot", None)), (
        "`_attention_snapshot` не заведён — снимок для новой пробы не "
        "собирается ничем")


# ── §5: ШЕСТЬ исходов, и каждый чинится по-разному ─────────────────────────

def test_a_quiet_instance_reads_as_ok():
    """Ответил, `stale_open == 0` — карточки читают. Открытые карточки при
    этом БЫТЬ МОГУТ: «есть открытая» и «висит незакрытой двое суток» — разные
    новости, и первая не авария."""
    p = ow.probe_attention(_snap(payload=_payload(open_=4, stale_open=0,
                                                  age=3600.0, wait=1800.0)))
    assert p["ok"] is True, p


def test_a_stale_card_is_the_news_about_the_deed():
    p = ow.probe_attention(_snap(payload=_payload(open_=3, stale_open=2)))
    assert p["ok"] is False, p
    assert p["reason"] == "attention_stale", p


def test_an_unresolvable_bind_address_says_so():
    """«Мерить нечем» — это отдельная новость, и молчаливое зелёное здесь было
    бы худшим из возможных отказов."""
    p = ow.probe_attention(_snap(host=None, status=None, payload=None,
                                 problem="адрес 0.0.0.0 отвергнут"))
    assert p["ok"] is False, p
    assert p["reason"] == "no_bind_address", p


def test_a_silent_port_is_no_response():
    p = ow.probe_attention(_snap(status=None, payload=None))
    assert p["ok"] is False, p
    assert p["reason"] == "no_response", p


def test_a_missing_handle_reads_as_http_404_not_as_death():
    """🔴 ПОРЯДОК ДЕПЛОЯ. Сначала ручка (рестарт инстанса), ПОТОМ проба.
    Обратный порядок даёт красное на здоровом — ровно тот дефект, что чинили в
    панели 22.08. Значит 404 обязан читаться как «ручки ещё нет», а не как
    «инстанс мёртв» и не как «карточки не читают»."""
    p = ow.probe_attention(_snap(status=404, payload=None))
    assert p["ok"] is False, p
    assert p["reason"] == "http:404", p
    dead = ow.probe_attention(_snap(status=None, payload=None))
    assert p["reason"] != dead["reason"], (p, dead)


def test_a_live_process_answering_badly_is_its_own_reason():
    p = ow.probe_attention(_snap(status=500, payload=None))
    assert p["ok"] is False and p["reason"] == "http:500", p


@pytest.mark.parametrize("body, why", [
    (None, "тела нет вовсе"),
    ("нет ответа", "тело — строка, а не объект"),
    ([], "тело — список"),
    ({}, "тело пустое"),
    ({"open": 3}, "нет `stale_open` — не по чему выносить вердикт"),
    ({"stale_open": 1}, "нет `open`"),
    ({"open": 3, "stale_open": "два", "oldest_age_s": 1.0,
      "oldest_wait_s": 1.0}, "`stale_open` строкой"),
    ({"open": "три", "stale_open": 0, "oldest_age_s": 1.0,
      "oldest_wait_s": 1.0}, "`open` строкой"),
])
def test_a_body_of_the_wrong_shape_is_RED(body, why):
    """🔴 ЛОВУШКА 5: `bad_payload` ОБЯЗАН БЫТЬ КРАСНЫМ.

    Соблазн прост: не разобрали тело — считаем, что всё тихо. Это «зелёное по
    построению»: проба, которая при ЛЮБОЙ поломке ручки говорит «карточки
    читают», хуже отсутствующей — отсутствующая хотя бы не врёт.
    """
    p = ow.probe_attention(_snap(status=200, payload=body))
    assert p["ok"] is False, ("тело (%s) прочитано как здоровье: %r" % (why, p))
    assert p["reason"] == "bad_payload", (why, p)


def test_bad_payload_is_not_confused_with_the_deed():
    """«Ручка сломалась» и «карточки не читают» чинятся РАЗНЫМИ людьми."""
    broken = ow.probe_attention(_snap(payload={"open": 1}))
    stale = ow.probe_attention(_snap(payload=_payload(stale_open=1)))
    assert broken["reason"] != stale["reason"], (broken, stale)


def test_every_verdict_carries_a_human_detail():
    """`detail` едет в текст алерта. Пустой detail = алерт без единого
    признака, по которому разбирают."""
    for snap in (_snap(host=None, status=None, payload=None),
                 _snap(status=None, payload=None),
                 _snap(status=404, payload=None),
                 _snap(payload={"open": 1}),
                 _snap(payload=_payload(stale_open=1)),
                 _snap(payload=_payload(stale_open=0))):
        p = ow.probe_attention(snap)
        assert (p.get("detail") or "").strip(), (snap.get("status"), p)


def test_the_observation_and_the_deed_speak_different_words():
    """§5 контракта: первые пять исходов — новости про НАБЛЮДЕНИЕ («не знаю,
    читают ли»), шестой — про ДЕЛО («не читают»). Владелец читает ФРАЗУ, а не
    имя переменной, и одинаковый текст на две разные новости отправит его
    чинить не то."""
    deed = ow.probe_attention(_snap(payload=_payload(stale_open=2)))["detail"]
    for snap in (_snap(host=None, status=None, payload=None),
                 _snap(status=None, payload=None),
                 _snap(status=404, payload=None),
                 _snap(payload={"open": 1})):
        obs = ow.probe_attention(snap)["detail"]
        assert obs != deed, ("наблюдение и дело описаны одной фразой: %r" % obs)


# ── §7 спеки / §5 контракта: `reason` без чисел и без адреса ───────────────

def test_the_reason_carries_no_age_and_no_address():
    """`reason` — ключ дедупа, а возраст растёт КАЖДЫЙ цикл. Секунды в нём
    дали бы алерт раз в 30 секунд, то есть пятнадцать записей об одном
    событии — шум, который однажды спрячет настоящее (DEV-66)."""
    p = ow.probe_attention(_snap(payload=_payload(open_=3, stale_open=2,
                                                  age=2_298_240.0)))
    reason = p["reason"]
    assert not any(ch.isdigit() for ch in reason), (
        "в причине живут цифры: %r" % reason)
    assert HOST not in reason, ("в причине живёт адрес: %r" % reason)


def test_the_detail_keeps_the_numbers_and_the_address():
    """Парная граница: числа и адрес не выброшены, а перенесены в `detail`.

    Без них разбор снова начинается с догадки — КУДА ходила проба и НАСКОЛЬКО
    всё плохо."""
    p = ow.probe_attention(_snap(payload=_payload(open_=3, stale_open=2)))
    detail = p["detail"]
    assert HOST in detail, ("адрес не назван в detail: %r" % detail)
    assert any(ch.isdigit() for ch in detail), detail


def _stale_pair(age_a, wait_a, age_b, wait_b, counts_a=(3, 2), counts_b=(3, 2)):
    """Две пробы одной и той же беды, отличающиеся только ЧИСЛАМИ."""
    a = ow.probe_attention(_snap(payload=_payload(
        open_=counts_a[0], stale_open=counts_a[1], age=age_a, wait=wait_a)))
    b = ow.probe_attention(_snap(payload=_payload(
        open_=counts_b[0], stale_open=counts_b[1], age=age_b, wait=wait_b)))
    return a, b


def _two_cycles(p1, p2):
    """Два цикла подряд одной пробой. Возвращает алерты обоих."""
    key = _ak(WITH_INSTANCE)
    alerts1, state1 = ow.evaluate({key: {"fail": DEBOUNCE - 1, "alerted": False}},
                                  {key: p1}, debounce=DEBOUNCE)
    alerts2, _state2 = ow.evaluate(state1, {key: p2}, debounce=DEBOUNCE)
    return alerts1, alerts2


def test_two_cycles_with_different_ages_give_ONE_alert():
    """§9 п. 5 дословно: возраст растёт каждый цикл, алерт обязан быть ОДИН.

    Дедуп в ядре идёт по `reason`; стоит возрасту просочиться туда — и каждая
    смена секунды прочитается как НОВАЯ авария.

    ⚠️ ЭТОТ СТОРОЖ ОДИН НЕ ДОКАЗЫВАЕТ ПРАВИЛА, и это названо, а не спрятано.
    Дельта здесь МЕЛКАЯ — тридцать секунд. Возраст в тексте сворачивается с
    шагом заметно крупнее (порядка десятых долей суток), поэтому оба цикла
    дают ОДНУ И ТУ ЖЕ свёрнутую строку, и сторож остался бы зелёным, даже
    просочись возраст в причину: причины совпали бы посимвольно. Замерено
    фактом — мутация «возраст в причине» пережила ровно этот сторож.

    Он оставлен потому, что ловит СОСЕДНИЙ случай: дедуп по факту падения при
    дрожащих числах внутри одного шага свёртки. Границу шага проверяет
    сторож ниже, и порознь они не заменяют друг друга.
    """
    p1, p2 = _stale_pair(200_000.0, 190_000.0, 200_030.0, 190_030.0)
    alerts1, alerts2 = _two_cycles(p1, p2)
    assert len(alerts1) == 1, ("предпосылка: первый цикл даёт алерт; %r" % (alerts1,))
    assert alerts2 == [], (
        "второй цикл с ПОДРОСШИМ возрастом дал ещё один алерт: возраст "
        "просочился в `reason`; %r" % (alerts2,))


def test_an_age_that_CROSSES_THE_FOLDING_STEP_still_gives_ONE_alert():
    """🔴 ДЫРА, найденная мутационным гейтом: сторож выше слеп ПО ПОСТРОЕНИЮ.

    Авария настоящая и ровно та, ради которой правило написано: через сутки
    свёрнутая строка возраста станет ДРУГОЙ, причина изменится, и владелец
    получит 🚨 «Новая причина» о падении, которое никуда не девалось. Пятнадцать
    записей об одном событии — это шум, который однажды спрячет настоящее.

    ПРЕДПОСЫЛКА ЧИТАЕТСЯ С САМОЙ ПРОБЫ, а не из моей модели свёртки: `detail`
    несёт числа (контракт §5), и разные `detail` означают, что возрасты легли
    ПО РАЗНЫЕ стороны шага свёртки — какой бы этот шаг ни был. Если однажды
    свёртка станет грубее и обе строки совпадут, сторож ЗАЯВИТ О СВОЕЙ
    СЛЕПОТЕ красным, а не промолчит зелёным. Ровно этого не хватало соседу.
    """
    day = 86400.0
    p1, p2 = _stale_pair(2.3 * day, 2.2 * day, 3.3 * day, 3.2 * day)

    assert p1["detail"] != p2["detail"], (
        "предпосылка НЕ выполнена: возрасты 2.3 и 3.3 суток дали ОДИНАКОВЫЙ "
        "текст, то есть не перешли шаг свёртки. Сторож в таком виде слеп к "
        "утечке возраста в причину — подбери дельту крупнее; detail: %r"
        % (p1["detail"],))
    assert p1["reason"] == p2["reason"], (
        "текст поехал, а ПРИЧИНА поехала вместе с ним: возраст просочился в "
        "`reason`. Через сутки владелец получит 🚨 «Новая причина» о падении, "
        "которое никуда не девалось; %r против %r" % (p1["reason"], p2["reason"]))

    alerts1, alerts2 = _two_cycles(p1, p2)
    assert len(alerts1) == 1, ("предпосылка: первый цикл даёт алерт; %r" % (alerts1,))
    assert alerts2 == [], (
        "второй цикл дал ВТОРОЙ алерт при той же самой беде: дедуп по причине "
        "сломан ростом возраста; %r" % (alerts2,))


def test_a_changed_COUNT_of_stale_cards_still_gives_ONE_alert():
    """Возраст не единственное растущее число: карточек тоже становится больше.

    Тем же приёмом и по той же причине: `detail` обязан поехать (иначе сторож
    ничего не двигал), `reason` — остаться. «Не читают у двоих» и «не читают у
    пятерых» — та же новость, сказанная второй раз, а не новая авария.
    """
    day = 86400.0
    p1, p2 = _stale_pair(3.3 * day, 3.2 * day, 3.3 * day, 3.2 * day,
                         counts_a=(3, 2), counts_b=(9, 7))

    assert p1["detail"] != p2["detail"], (
        "предпосылка НЕ выполнена: смена счёта карточек не видна в тексте, "
        "значит сторож ничего не двигал; detail: %r" % (p1["detail"],))
    assert p1["reason"] == p2["reason"], (
        "счёт карточек просочился в `reason`: каждая новая карточка даст "
        "🚨 «Новая причина»; %r против %r" % (p1["reason"], p2["reason"]))

    alerts1, alerts2 = _two_cycles(p1, p2)
    assert len(alerts1) == 1, ("предпосылка: первый цикл даёт алерт; %r" % (alerts1,))
    assert alerts2 == [], (
        "рост числа протухших карточек дал ВТОРОЙ алерт; %r" % (alerts2,))


# ── §4.1 спеки: порог принимает ИНСТАНС, а не watchdog ─────────────────────

def test_the_verdict_follows_stale_open_and_not_the_probes_own_arithmetic():
    """🔴 §9 п. 4 со стороны наблюдателя.

    Решение «протухло или нет» принимает ИНСТАНС по `STALE_AFTER`: watchdog
    stdlib-only и `app.*` не импортирует, значит своего порога у него нет и
    быть не может. Проба обязана верить полю `stale_open`, а не сравнивать
    `oldest_age_s` с собственным числом.

    Пара доказывает это с двух сторон: огромный возраст при `stale_open == 0`
    обязан быть ЗЕЛЁНЫМ, крошечный при `stale_open == 1` — КРАСНЫМ. Проба со
    своим порогом провалит ровно один из двух.
    """
    huge = ow.probe_attention(_snap(payload=_payload(
        open_=1, stale_open=0, age=200 * 86400.0, wait=200 * 86400.0)))
    assert huge["ok"] is True, (
        "проба сама решила, что 200 суток — это протухло: у неё завёлся "
        "ВТОРОЙ порог рядом со `STALE_AFTER`; %r" % (huge,))

    tiny = ow.probe_attention(_snap(payload=_payload(
        open_=1, stale_open=1, age=10.0, wait=5.0)))
    assert tiny["ok"] is False and tiny["reason"] == "attention_stale", (
        "инстанс сказал «протухло», а проба не поверила: значит порог она "
        "считает сама; %r" % (tiny,))


def test_the_watchdog_holds_no_threshold_of_its_own():
    """Структурная половина: ни `STALE_AFTER`, ни 48 часов в исходнике.

    Считается AST, а не текст: комментарий, объясняющий, ПОЧЕМУ порога здесь
    быть не должно, — объяснение, а не порог.
    """
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "STALE_AFTER" not in names, (
        "watchdog завёл собственное представление о пороге протухания")
    numbers = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant)
               and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool)}
    assert 172800 not in numbers and 172800.0 not in numbers, (
        "порог 48 ч вписан в watchdog вторым числом")


def test_the_watchdog_is_still_stdlib_only():
    """`import app` / `import chatter` запрещены: watchdog обязан уметь
    сказать «бэкенд мёртв» ТОГДА, когда мертво всё, что делит с ним
    окружение. Клиентских БД он не открывает по той же причине."""
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names
                    if a.name.split(".")[0] in ("app", "chatter")]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in ("app", "chatter"):
                bad.append(node.module)
    assert not bad, ("watchdog перестал быть stdlib-only: %s" % (bad,))
    src = OPS_PATH.read_text(encoding="utf-8")
    assert "sqlite3" not in src, "watchdog открывает клиентскую БД сам"


# ── §5.1 спеки: СОСТАВ ПРОБ ИЗ РОСТЕРА, А НЕ ИЗ СПИСКА ИНСТАНСОВ ───────────
#
# АМЕНДМЕНТ КОНТРАКТА (24.08). Форму снимка контракт не называл, и первая
# редакция этих сторожей подавала в `probe_all` словарь, собранный РУКАМИ.
# Довод, которым форма зафиксирована, не вкусовой: `probe_all` НИГДЕ не ходит
# по сети — у панели HTTP живёт в `_panel_client_snapshot`, а композитор
# получает готовый `status`. Значит и здесь сеть — в сборщике:
#
#   _attention_snapshot(roster_snapshot, panel_snapshot, *,
#                       slug=PANEL_CLIENT_SLUG, fetch=None) -> dict | None
#       -> {"clients": {"<slug>": {<запись одного клиента>}}}
#       None — когда состав или адрес спросить НЕЧЕМ (ростер не прочитан,
#              снимка панели нет)
#   probe_all(..., attention_snapshot=<то, что вернул сборщик>)
#
# Сторожа состава теперь гоняются ЧЕРЕЗ настоящий сборщик, а не через словарь
# из моих рук. Это строже: словарь из рук проверял, что `probe_all` его
# скопировал; настоящий сборщик проверяет ПРАВИЛО состава — перебор ростера,
# правило `slug == PANEL_CLIENT_SLUG`, и то, что адрес с портом взяты из
# готового снимка панели, а не вычислены вторично.

ROSTER_TWO = {"clients": [{"slug": NO_INSTANCE, "enabled": True},
                          {"slug": WITH_INSTANCE, "enabled": True},
                          {"slug": DISABLED, "enabled": False}]}


def _chatter(roster=None):
    return {"processes": [], "beats": {}, "legacy_beat_age": 90 * 86400.0,
            "guardian_beat_age": 5.0, "guardian_lock_pid": None,
            "root": "C:/jarvis",
            "roster": ROSTER_TWO if roster is None else roster}


def _panel(host=HOST, port=PORT, status=200, problem=None):
    """Снимок панели — ровно той формы, какую отдаёт `_panel_client_snapshot`."""
    return {"host": host, "port": port, "status": status, "problem": problem}


@pytest.fixture()
def net(monkeypatch):
    """Сеть инстанса под контролем — перехват на `urllib`, а не на `fetch`.

    Инъекцию `fetch` амендмент называет, но ФОРМУ её ответа — нет, а угадывать
    неназванное значит краснеть на законном выборе автора кода. Поэтому
    сборщик гоняется через СВОЙ настоящий сетевой слой, перехваченный там, где
    watchdog обязан оставаться stdlib-only, — на `urllib.request.urlopen`.
    Заодно это доказывает поход целиком: адрес, порт, путь и разбор тела.

    Сам факт инъектируемости `fetch` пиннится отдельно, по подписи.
    """
    state = {"status": 200, "silent": False, "urls": [],
             "body": _payload(open_=2, stale_open=0, age=3600.0, wait=1800.0)}

    def fake_urlopen(url, *a, **k):
        target = getattr(url, "full_url", url)
        state["urls"].append(str(target))
        if state["silent"]:
            raise OSError("порт молчит (refused)")
        if state["status"] != 200:
            raise ow.urllib.error.HTTPError(
                str(target), state["status"], "nope", {}, None)
        body = state["body"]
        text = body if isinstance(body, str) else json.dumps(body)
        return _FakeResp(200, text)

    monkeypatch.setattr(ow.urllib.request, "urlopen", fake_urlopen)
    return state


def _collect(roster=None, panel=None):
    """Снимок эскалаций РЕАЛЬНЫМ сборщиком контракта."""
    roster = ROSTER_TWO if roster is None else roster
    return ow._attention_snapshot(roster, _panel() if panel is None else panel)


def _probes(attention_snapshot, roster=None):
    return ow.probe_all(lambda p: 200,
                        lambda p: (100 * 2 ** 30, 0, 50 * 2 ** 30),
                        chatter_snapshot=_chatter(roster),
                        attention_snapshot=attention_snapshot)


def _cycle(roster=None, panel=None):
    """Полный цикл: ростер -> сборщик -> композитор. Ростер ОДИН объект на
    обе половины — второго чтения реестра появиться не должно."""
    roster = ROSTER_TWO if roster is None else roster
    return _probes(_collect(roster, panel), roster)


def _att_keys(probes):
    return sorted(k for k in probes if k.startswith(ATT_PREFIX))


def test_the_collector_takes_the_roster_and_the_panel_snapshot():
    """Подпись сборщика — часть контракта: ростер приходит ГОТОВЫМ (второго
    чтения реестра нет), адрес приходит ГОТОВЫМ (второго вызова резолвера
    нет), а `fetch` инъектируется — иначе «ходит на инстанс» осталось бы
    утверждением, которое нечем подменить."""
    sig = inspect.signature(ow._attention_snapshot)
    params = list(sig.parameters.values())
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert [p.name for p in positional] == ["roster_snapshot", "panel_snapshot"], (
        "сборщик принимает %s вместо (roster_snapshot, panel_snapshot)"
        % ([p.name for p in positional],))
    kw = {p.name: p for p in params if p.kind == p.KEYWORD_ONLY}
    assert "fetch" in kw and kw["fetch"].default is None, (
        "`fetch` не инъектируется: поход на инстанс нечем подменить")
    assert "slug" in kw and kw["slug"].default == ow.PANEL_CLIENT_SLUG, (
        "слаг единственного инстанса взят не из `PANEL_CLIENT_SLUG` — это "
        "лишнее место для имени, которое уже названо константой")


def test_the_cycle_probes_every_ENABLED_client_of_the_roster(net):
    """🔴 ГЛАВНАЯ ЛОВУШКА ВСЕЙ РАБОТЫ, и она видна уже сегодня.

    Инстанс ОДИН, а из четырёх открытых карточек ТРИ у клиента без инстанса,
    включая самую старую — 26.6 суток. Проба, построенная «по инстансам», была
    бы ЗЕЛЁНОЙ ПО ПОСТРОЕНИЮ ровно там, где лежит вся проблема: это буквально
    механизм, который молчит, потому что смотрит не туда.

    Ростер из двух включённых + инстанс из одного = ДВА ключа.
    """
    probes = _cycle()
    assert _att_keys(probes) == sorted([_ak(NO_INSTANCE), _ak(WITH_INSTANCE)]), (
        "состав проб эскалаций взят не из ростера: %s" % (_att_keys(probes),))
    assert _ak(DISABLED) not in probes, (
        "выключенный клиент получил пробу: выключенный клиент пробы не имеет "
        "вовсе — как и у раннеров")


def test_the_client_without_an_instance_is_RED_no_instance(net):
    """§9 п. 2: НЕ зелёное и НЕ отсутствие ключа.

    Молчать о том, что половина фермы непроверяема, нельзя. Лампа загорится с
    первого цикла — это правильно, и владелец должен знать заранее и решить:
    поднимать инстанс или помечать клиента осознанно непокрытым.
    """
    probes = _cycle()
    p = probes[_ak(NO_INSTANCE)]
    assert p["ok"] is False, (
        "клиент без инстанса позеленел — «не смог спросить» выдано за "
        "«карточки читают»; %r" % (p,))
    assert p["reason"] == "no_instance", p
    assert (p.get("detail") or "").strip(), p


def test_nobody_walks_to_the_network_for_the_client_without_an_instance(net):
    """🔴 ЛОВУШКА 3 в поведении: карты портов НЕТ.

    Спросить по адресу единственного инстанса ЗА клиента, у которого инстанса
    нет, значит выдать чужой ответ за его: `volska` получила бы вердикт по
    карточкам `yarina`, и это было бы зелёное ровно там, где лежит проблема.
    Правило простое и проверяемое: `slug == PANEL_CLIENT_SLUG` — спрашиваем,
    иначе не ходим вовсе.
    """
    _cycle()
    assert len(net["urls"]) == 1, (
        "походов на инстанс %d при ОДНОМ инстансе на ферме: %s"
        % (len(net["urls"]), net["urls"]))


def test_the_client_with_an_instance_is_measured_normally(net):
    """Парная граница: правило `no_instance` не имеет права выключить пробу
    вовсе — иначе всё семейство станет вечно красным фоном."""
    net["body"] = _payload(open_=2, stale_open=0, age=3600.0, wait=1800.0)
    probes = _cycle()
    assert probes[_ak(WITH_INSTANCE)]["ok"] is True, probes[_ak(WITH_INSTANCE)]

    net["body"] = _payload(open_=2, stale_open=1)
    probes = _cycle()
    p = probes[_ak(WITH_INSTANCE)]
    assert p["ok"] is False and p["reason"] == "attention_stale", p


def test_no_instance_is_not_confused_with_a_dead_one(net):
    """«Инстанса нет» чинит владелец (поднять или признать непокрытым),
    «инстанс не отвечает» — гардиан. Один reason на два случая утопил бы
    вторую новость в дедупе первой."""
    net["silent"] = True
    probes = _cycle()
    absent = probes[_ak(NO_INSTANCE)]["reason"]
    dead = probes[_ak(WITH_INSTANCE)]["reason"]
    assert absent == "no_instance" and dead == "no_response", (absent, dead)


def test_a_missing_handle_reaches_the_verdict_through_the_whole_cycle(net):
    """Порядок деплоя целиком: ручки ещё нет -> 404 -> «не знаю, читают ли».

    Поштучный сторож на `probe_attention` пинит перевод кода в причину; этот
    пинит, что код вообще ДОЕЗЖАЕТ от `urlopen` до вердикта цикла. Сборщик,
    проглотивший `HTTPError`, превратил бы 404 в `no_response`, и владелец
    пошёл бы поднимать процесс, который жив.
    """
    net["status"] = 404
    p = _cycle()[_ak(WITH_INSTANCE)]
    assert p["reason"] == "http:404", p


def test_without_a_snapshot_the_family_does_not_appear_out_of_nowhere(net):
    """`None` = проб этого семейства в цикле НЕТ вовсе: watchdog не имеет
    права слать DOWN о том, чего не мерил.

    Пара утверждений, а не одно: без предпосылки «со снимком пробы ЕСТЬ»
    сторож зелен по построению — ключей могло не оказаться по любой другой
    причине.
    """
    assert _att_keys(_cycle()), "предпосылка: со снимком пробы семейства есть"
    probes = _probes(None)
    assert _att_keys(probes) == [], (
        "проба эскалаций выставлена без снимка: %s" % (_att_keys(probes),))


def test_an_unreadable_roster_gives_no_attention_probes_at_all(net):
    """Красный `chatter_roster` означает «состава не знаем». Выставить в этот
    момент пробы «по инстансам» значило бы подменить неизвестный состав
    известным — и промолчать ровно о тех, кого не увидели.

    Проверяется В ТРИ ХОДА: сборщик обязан ответить `None` (а не пустым
    составом, который прочитался бы как «клиентов нет»), цикл на этом `None`
    обязан остаться без ключей семейства, и на инстанс никто не ходил.
    """
    broken = {"error": "битый yaml"}
    assert _collect(roster=broken) is None, (
        "сборщик выдал состав там, где реестр не прочитан: пустой состав "
        "неотличим от «клиентов нет»")
    probes = _cycle(roster=broken)
    assert probes["chatter_roster"]["ok"] is False
    assert _att_keys(probes) == [], (
        "состава фермы не знаем, а пробы эскалаций выставлены: %s"
        % (_att_keys(probes),))
    assert net["urls"] == [], (
        "не зная состава, сборщик всё-таки сходил на инстанс: %s" % (net["urls"],))


def test_without_a_panel_snapshot_there_is_nothing_to_measure(net):
    """Снимка панели нет — адрес спросить нечем, и это НЕ повод для DOWN.

    `None` от `_panel_client_snapshot` означает «watchdog не на деплой-хосте»
    (нет `run_panel_client.py`). Выдать это за «инстанс мёртв» значило бы
    поднять тревогу на машине, которой мерить и не положено.
    """
    assert ow._attention_snapshot(ROSTER_TWO, None) is None, (
        "сборщик собрал состав без снимка панели — значит адрес он взял "
        "откуда-то ещё")
    assert net["urls"] == [], net["urls"]


def test_the_runner_probes_are_untouched_by_the_new_family(net):
    """ГРАНИЦА: новое семейство встаёт РЯДОМ, а не вместо.

    Пер-клиентная арка закрыта два часа назад, на ней стоят свои сторожа и
    мутационный гейт; молчаливая подмена её ключей была бы дефектом, который
    покажет себя только на живой аварии.

    Предпосылка обязательна: без проб эскалаций в этом же цикле утверждение
    «раннеры не тронуты» истинно по построению и не охраняет ничего.
    """
    probes = _cycle()
    assert _att_keys(probes), "предпосылка: пробы эскалаций в цикле есть"
    for slug in (NO_INSTANCE, WITH_INSTANCE):
        assert _rk(slug) in probes, (
            "проба раннера %s пропала: %s" % (slug, sorted(probes)))
    assert _rk(DISABLED) not in probes
    assert "chatter_roster" in probes and "chatter_beat_legacy" in probes


def test_the_new_keys_do_not_collide_with_the_runner_keys(net):
    """Два семейства пер-клиентных ключей на один слаг: `fail`, `alerted` и
    дедуп по причине живут ВНУТРИ записи ключа, и слипшиеся ключи означали бы,
    что мёртвый раннер глушит алерт о непрочитанной карточке."""
    net["body"] = _payload(open_=2, stale_open=1)
    probes = _cycle()
    assert _ak(WITH_INSTANCE) != _rk(WITH_INSTANCE)
    assert _ak(WITH_INSTANCE) in probes and _rk(WITH_INSTANCE) in probes, sorted(probes)
    assert probes[_ak(WITH_INSTANCE)] is not probes[_rk(WITH_INSTANCE)]
    assert probes[_ak(WITH_INSTANCE)]["reason"] == "attention_stale", (
        "предпосылка: ключ эскалаций несёт СВОЙ вердикт")
    assert probes[_rk(WITH_INSTANCE)]["reason"] != "attention_stale", (
        "ключ раннера получил вердикт эскалаций — семейства слиплись")



# ── §2 спеки / ЛОВУШКА 4: адрес — ОДИН вызов резолвера ─────────────────────

class _FakeResp:
    """Ответ urlopen: контекстный менеджер, код и тело. Формы доступа — все
    расхожие сразу, потому что каким именно способом новая соседняя функция
    читает ответ, контракт не называет."""

    def __init__(self, code, body):
        self.code = code
        self.status = code
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.code

    def read(self, *a):
        return self._body

    def info(self):
        return self.headers


@pytest.fixture()
def resolver(monkeypatch):
    """Подменённый резолвер бинда + счётчик его вызовов.

    Модуль кладётся в `sys.modules["run_panel_client"]` — ровно туда, откуда
    `_load_run_panel_client` берёт ОДИН ОБЩИЙ объект. Инъекция сохраняется не
    как тестовый костыль: ею сторож ДОКАЗЫВАЕТ, что проба поехала за
    резолвером, а не что сегодня совпали числа (§2.2 спеки дословно).
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run_panel_client as rpc

    calls = {"resolve": 0, "ip": 0, "urls": []}

    def fake_ip(*a, **k):
        calls["ip"] += 1
        return "100.77.77.77"

    def fake_resolve(*a, **k):
        calls["resolve"] += 1
        return ("100.77.77.77", None)

    monkeypatch.setattr(rpc, "tailnet_ip", fake_ip)
    monkeypatch.setattr(rpc, "resolve_client_host", fake_resolve)

    def fake_urlopen(url, *a, **k):
        target = getattr(url, "full_url", url)
        calls["urls"].append(str(target))
        return _FakeResp(200, json.dumps(_payload(open_=3, stale_open=2)))

    monkeypatch.setattr(ow.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_the_probe_walks_to_the_address_the_resolver_gave(resolver):
    """ПОДМЕНОЙ, а не совпадением чисел: если снимок берёт адрес где-то ещё,
    он не сдвинется — и сторож покраснеет.

    Прогулка случается В СБОРЩИКЕ, а не в `probe_all`: композитор нигде не
    ходит по сети (у панели HTTP тоже живёт в её сборщике, а `probe_all`
    получает готовый `status`). Сторож ходит тем же путём, каким пойдёт цикл.
    """
    panel = ow._panel_client_snapshot()
    assert panel["host"] == "100.77.77.77", panel

    ow._attention_snapshot(ROSTER_TWO, panel)
    walked = [u for u in resolver["urls"] if ATT_PATH_EXPECTED in u]
    assert walked, (
        "проба не сходила на %s вовсе; ходила по адресам: %s"
        % (ATT_PATH_EXPECTED, resolver["urls"]))
    assert all("100.77.77.77" in u for u in walked), (
        "проба пошла НЕ на тот адрес, который дал резолвер бинда: %s" % (walked,))
    assert all(str(PORT) in u for u in walked), (
        "порт взят не из снимка панели — это лишнее место для числа 8011: %s"
        % (walked,))


def test_the_address_is_collected_by_ONE_resolver_call_for_both_probes(resolver):
    """🔴 ЛОВУШКА 4: два вызова = два числа на одну вещь.

    Спека §2.1 требует буквально: «сбор адреса для обеих проб — ОДИН вызов,
    результат которого делится, а не два вызова резолвера рядом». Разойдутся
    они ровно в день смены адреса тайнета, и меньшее число погасит большее
    молча. Снимок панели передаётся сборщику ГОТОВЫМ — ровно затем, чтобы
    второму вызову неоткуда было взяться.
    """
    panel = ow._panel_client_snapshot()
    ow._attention_snapshot(ROSTER_TWO, panel)
    assert resolver["resolve"] == 1, (
        "резолвер бинда позван %d раз(а) за цикл вместо одного: адрес "
        "собирается ВТОРОЙ раз рядом" % resolver["resolve"])


def test_a_refusing_resolver_reaches_the_verdict_as_no_bind_address(resolver, monkeypatch):
    """Отказ резолвера обязан доехать до ВЕРДИКТА, а не превратиться ни в
    пустую строку-адрес, по которой проба сходила бы неизвестно куда, ни в
    отсутствие пробы.

    Различие названо в докстринге `_panel_client_snapshot` дословно: «спросить
    адрес нечем» (нет `run_panel_client.py`) и «адрес вычислен, но НЕГОДЕН» —
    разные вещи, и вторую нельзя выдавать за первую. Первая = снимка панели
    нет вовсе и пробы в цикле нет; вторая = снимок ЕСТЬ, `host` в нём None, и
    проба обязана стать КРАСНОЙ.
    """
    import run_panel_client as rpc
    monkeypatch.setattr(rpc, "tailnet_ip", lambda *a, **k: "")
    monkeypatch.setattr(
        rpc, "resolve_client_host",
        lambda *a, **k: (None, "адрес 0.0.0.0 открыл бы панель всей сети"))

    panel = ow._panel_client_snapshot()
    assert panel["host"] is None, panel
    probes = _probes(ow._attention_snapshot(ROSTER_TWO, panel), ROSTER_TWO)
    p = probes.get(_ak(WITH_INSTANCE))
    assert p is not None, (
        "«не смогли вычислить адрес» подменено на «пробы в этом цикле нет»: "
        "это тишина там, где договорились говорить вслух; ключи: %s"
        % (_att_keys(probes),))
    assert p["reason"] == "no_bind_address", p
    assert not [u for u in resolver["urls"] if ATT_PATH_EXPECTED in u], (
        "адреса нет, а проба всё-таки куда-то сходила: %s" % (resolver["urls"],))


def test_the_address_is_computed_in_exactly_one_place_in_the_source():
    """Статическая половина той же ловушки, по образцу сторожа десятой пробы.

    Утверждение — про число МЕСТ во всём модуле, а не про текст одной функции:
    вынести обращение к резолверу в отдельную функцию — законная факторизация,
    а вот два места вместо одного — тот самый дефект.
    """
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    producers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = {s.attr for s in ast.walk(node) if isinstance(s, ast.Attribute)}
        used |= {s.id for s in ast.walk(node) if isinstance(s, ast.Name)}
        if used & {"resolve_client_host", "tailnet_ip"}:
            producers.append(node.name)
    assert len(producers) == 1, (
        "адрес бинда вычисляется в %d местах: %s. Два числа на одну вещь "
        "разойдутся при переезде тайнета" % (len(producers), sorted(producers)))


def test_the_old_http_getter_was_not_rewritten():
    """Контракт §7: `_panel_http_get` возвращает ТОЛЬКО код статуса и
    пиннится. Тело нужно новой пробе — заводится СОСЕДНЯЯ функция, а не
    правка старой: на старой стоит проба живости панели, и её молчаливое
    изменение уронило бы соседнюю арку."""
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_panel_http_get"),
              None)
    assert fn is not None, "`_panel_http_get` исчез"
    returned = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Call):
                returned.add(getattr(node.value.func, "attr", None)
                             or getattr(node.value.func, "id", None))
            elif isinstance(node.value, ast.Attribute):
                returned.add(node.value.attr)
            elif isinstance(node.value, ast.Constant):
                returned.add(repr(node.value.value))
            else:
                returned.add(ast.dump(node.value)[:40])
    assert returned <= {"getcode", "code", "None"}, (
        "`_panel_http_get` стал возвращать не только код статуса: %s" % (returned,))


# ── §6.1: ЯРЛЫК нового семейства ───────────────────────────────────────────

def test_the_label_of_the_new_family_is_about_escalations_not_runners():
    """🔴 §6.1: `label_for` СЕЙЧАС СОВРЁТ.

    Он отдаёт «CHATTER раннер клиента «%s»» ЛЮБОМУ ключу, прошедшему
    `is_client_check`. Новое семейство получило бы ярлык «раннер» — то есть
    ровно дефект, закрытый в `e132b707` два часа назад, только наоборот:
    владелец пошёл бы перезапускать раннер вместо того, чтобы прийти в диалог.
    """
    label = ow.label_for(_ak("volska"))
    assert label != _ak("volska"), (
        "ярлыка нет вовсе — владелец прочтёт сырой ключ, то есть имя "
        "переменной: %r" % label)
    assert "раннер" not in label.lower(), (
        "новое семейство представилось РАННЕРОМ: %r" % label)
    assert "volska" in label, (
        "слаг обязан остаться — по нему владелец понимает, к КОМУ идти: %r"
        % label)
    assert any(w in label.lower() for w in ("эскал", "карточ", "внимани")), (
        "ярлык не говорит, о ЧЁМ новость: владелец читает фразу, а не имя "
        "ключа; %r" % label)


def test_the_two_families_do_not_read_alike():
    """Оба ярлыка пер-клиентные и оба называют слаг. Различаться они обязаны
    СЛОВАМИ, иначе два разных сообщения об одном клиенте неотличимы.

    Первое утверждение — предпосылка: без него сторож зелен по построению,
    пока ярлыка нет вовсе (сырой ключ отличается от фразы про раннер, но это
    не «различаются словами», это «одного ярлыка не существует»).
    """
    a = ow.label_for(_ak("volska"))
    b = ow.label_for(_rk("volska"))
    assert a != _ak("volska"), (
        "предпосылка: у нового семейства есть ярлык, а не сырой ключ; %r" % a)
    assert a != b, ("оба семейства представляются одинаково: %r" % a)


def test_the_runner_label_did_not_move():
    """ГРАНИЦА: ярлык раннеров закрыт сторожем соседней арки и правке не
    подлежит — «новое семейство» не повод переписать старое."""
    assert ow.label_for(_rk("volska")) == "CHATTER раннер клиента «volska»"


def test_the_new_family_is_a_client_check():
    """§5.2 спеки: иначе склейка его не соберёт, а `label_for` отдаст сырой
    ключ. Требование жёсткое: мест разбора префикса НЕ становится больше."""
    assert ow.is_client_check(_ak("volska")) is True
    assert ow.is_client_check(_rk("volska")) is True
    assert ow.is_client_check("backend") is False
    assert ow.is_client_check("chatter_runner") is False
    assert ow.is_client_check("") is False


def test_the_slug_is_cut_by_the_same_single_function():
    assert ow.client_slug_of(_ak("volska")) == "volska"
    assert ow.client_slug_of(_rk("volska")) == "volska"
    assert ow.client_slug_of("backend") == ""


def test_the_prefix_is_still_cut_in_exactly_one_function():
    """СТРУКТУРНЫЙ пин: поведенческим это не ловится ПО ПОСТРОЕНИЮ — ручной
    срез и `client_slug_of` дают одинаковую строку. Предмет защиты —
    ЕДИНСТВЕННОСТЬ написания: три независимых написания префикса разъехались бы
    молча, и подавление перестало бы срабатывать, а склейка сложила бы
    клиентов вместе с бэкендом."""
    tree = ast.parse(OPS_PATH.read_text(encoding="utf-8"))
    cutters = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Subscript) or not isinstance(sub.slice, ast.Slice):
                continue
            low = sub.slice.lower
            if (isinstance(low, ast.Call)
                    and getattr(low.func, "id", None) == "len"
                    and low.args
                    and isinstance(low.args[0], ast.Name)
                    and low.args[0].id.endswith("PREFIX")):
                cutters.add(node.name)
    assert cutters == {"client_slug_of"}, (
        "префикс режется в %s, а обязан — только в `client_slug_of`" % (sorted(cutters),))


# ── §6.2: ПОДАВЛЕНИЕ §4.1 НОВОЕ СЕМЕЙСТВО НЕ ЗАХВАТЫВАЕТ ───────────────────

def _armed():
    return {"fail": DEBOUNCE - 1, "alerted": False}


def _red(reason):
    return {"ok": False, "reason": reason, "detail": "деталь: " + reason}


def test_a_red_guardian_does_NOT_silence_the_new_family():
    """🔴 §6.2, и это самый опасный шов работы.

    Логика подавления: «падение клиента под мёртвым гардианом — та же новость,
    что и падение гардиана, сказанная второй раз». Для эскалаций это НЕВЕРНО:
    мёртвый гардиан не объясняет, почему человек не пришёл в диалог. Захвати
    новое семейство это условие — красный гардиан МОЛЧА глушил бы лампу
    «карточки не читают», то есть лампа выключалась бы ровно тогда, когда на
    ферме и без того плохо.

    Слаги РАЗНЫЕ намеренно: одинаковые сделали бы тексты неразличимыми, и
    сторож не смог бы сказать, чей именно алерт уехал.

    ⚠️ СЕГОДНЯ ЭТОТ СТОРОЖ ЗЕЛЁН СЛУЧАЙНО: `is_client_check` про префикс
    эскалаций ещё не знает, поэтому подавление до нового семейства не
    дотягивается физически. Смысл он приобретает РОВНО в тот момент, когда
    реализация проведёт новый ключ через `is_client_check` ради ярлыка и
    склейки (§5.2 спеки) — и вместе с этим, если не разделить предикаты,
    затащит его под подавление. Сторож стоит на ту минуту, а не на эту, и
    предпосылка ниже говорит это ВСЛУХ: пока ключ не клиентский, подавлению
    просто не за что зацепиться, и зелёный здесь ничего не значит.
    """
    assert ow.is_client_check(_ak("marina")), (
        "предпосылка: новое семейство проходит через `is_client_check` — "
        "без этого подавление до него не дотягивается, и сторож зелен по "
        "построению")
    prev = {GUARDIAN: {"fail": 0, "alerted": False},
            _rk("volska"): _armed(), _ak("marina"): _armed()}
    probes = {GUARDIAN: _red("dead_pid"),
              _rk("volska"): _red("no_process"),
              _ak("marina"): _red("attention_stale")}

    alerts, state = ow.evaluate(prev, probes, debounce=DEBOUNCE)

    assert _mentioning(alerts, "marina"), (
        "красный гардиан проглотил новость «карточки не читают»: подавление "
        "§4.1 захватило новое семейство; получено %r" % (alerts,))
    assert state[_ak("marina")].get("alerted") is True, (
        "алерт ушёл, а `alerted` не поставлен — следующий цикл повторит его")


def test_the_suppression_of_the_runner_family_stays_bit_for_bit():
    """Парная граница: §4.1 остаётся ПРЕЖНИМ. На нём стоят сторожа соседней
    арки и мутационный гейт, и «починить» §6.2 отменой подавления значило бы
    вернуть шторм, ради тишины в котором вся та работа и делалась."""
    prev = {GUARDIAN: {"fail": 0, "alerted": False},
            _rk("volska"): _armed(), _ak("marina"): _armed()}
    probes = {GUARDIAN: _red("dead_pid"),
              _rk("volska"): _red("no_process"),
              _ak("marina"): _red("attention_stale")}

    alerts, state = ow.evaluate(prev, probes, debounce=DEBOUNCE)

    assert _mentioning(alerts, "volska") == [], (
        "пер-клиентный раннер под красным гардианом ушёл владельцу — §4.1 "
        "сломано правкой соседнего семейства; %r" % (alerts,))
    st = state[_rk("volska")]
    assert st.get("alerted") is False, (
        "подавление поставило `alerted`: гардиан поднимется, клиент — нет, и "
        "владелец не узнает об этом НИКОГДА")
    assert st["fail"] == DEBOUNCE, st


def test_the_journal_still_gets_the_suppressed_runner_and_the_loud_escalation():
    """Журнал читает панель, а не человек в 2 ночи: КАЖДЫЙ переход целиком."""
    prev = {GUARDIAN: {"fail": 0, "alerted": False},
            _rk("volska"): _armed(), _ak("marina"): _armed()}
    probes = {GUARDIAN: _red("dead_pid"),
              _rk("volska"): _red("no_process"),
              _ak("marina"): _red("attention_stale")}

    journal, _new = ow.transitions(prev, probes, DEBOUNCE)
    by_check = {r["check"]: r for r in journal}
    assert _rk("volska") in by_check, "подавлённое падение раннера не в журнале"
    assert _ak("marina") in by_check, "падение эскалаций не в журнале"
    for rec in (by_check[_rk("volska")], by_check[_ak("marina")]):
        assert set(rec) == {"ts", "check", "kind", "reason", "detail"}, sorted(rec)


def test_a_green_guardian_changes_nothing_for_the_new_family():
    """ГРАНИЦА: §6.2 не имеет права превратиться в «эскалации всегда громкие,
    что бы ни случилось» — дебаунс и дедуп остаются общими."""
    key = _ak("marina")
    probes = {key: _red("attention_stale")}
    alerts1, st1 = ow.evaluate({}, probes, debounce=DEBOUNCE)
    assert alerts1 == [], ("первый красный цикл обязан молчать: %r" % (alerts1,))
    alerts2, _st2 = ow.evaluate(st1, probes, debounce=DEBOUNCE)
    assert len(alerts2) == 1, ("второй цикл обязан дать РОВНО один алерт: %r"
                               % (alerts2,))


# ── §6.3: СКЛЕЙКА §4.2 НЕ СМЕШИВАЕТ СЕМЕЙСТВА ─────────────────────────────

def _tr(check, kind, reason):
    return {"ts": 1_755_000_000.0, "check": check, "kind": kind,
            "reason": reason, "detail": "деталь: " + reason}


RUNNERS = (("volska", "no_process"), ("yarina", "stale_heartbeat"),
           ("olga", "no_heartbeat"))
ESCALATIONS = (("marina", "attention_stale"), ("nadia", "no_instance"),
               ("oksana", "no_response"))


def test_the_two_families_are_glued_separately():
    """🔴 §6.3. Два упавших раннера и две протухшие эскалации дали бы группу
    из четырёх «down» — ОДНО сообщение о ДВУХ РАЗНЫХ новостях, из которого
    непонятно, идти чинить процессы или идти в диалог.

    Группировка обязана идти по (СЕМЕЙСТВО, вид), а не по виду.
    """
    trs = ([_tr(_rk(s), "down", r) for s, r in RUNNERS]
           + [_tr(_ak(s), "down", r) for s, r in ESCALATIONS])
    texts = ow.group_alerts(trs)

    assert len(texts) == 2, (
        "три падения раннеров и три протухшие эскалации обязаны стать ДВУМЯ "
        "сообщениями — по одному на семейство; получено %r" % (texts,))
    runner_text = [t for t in texts if all(s in t for s, _ in RUNNERS)]
    esc_text = [t for t in texts if all(s in t for s, _ in ESCALATIONS)]
    assert len(runner_text) == 1 and len(esc_text) == 1, texts
    for slug, _r in ESCALATIONS:
        assert slug not in runner_text[0], (
            "клиент %r из семейства эскалаций затесался в склейку РАННЕРОВ: "
            "владелец пойдёт перезапускать процесс вместо разговора" % slug)
    for slug, _r in RUNNERS:
        assert slug not in esc_text[0], (
            "раннер %r затесался в склейку эскалаций" % slug)


def test_two_and_two_do_not_reach_the_glue_threshold():
    """Порог СТРОГИЙ и считается ВНУТРИ семейства.

    Счёт по виду поперёк семейств дал бы 4 > 2 и склеил бы всё в одну строку —
    ту самую, из которой не понять, что случилось.

    ⚠️ Сегодня зелен случайно (эскалации ещё не клиентские ключи и идут мимо
    склейки поштучно). Стоит он на день, когда семейство в склейку попадёт, и
    предпосылка ниже отличает этот день от сегодняшнего.
    """
    assert ow.is_client_check(_ak("marina")), (
        "предпосылка: семейство эскалаций вообще участвует в склейке")
    trs = ([_tr(_rk(s), "down", r) for s, r in RUNNERS[:2]]
           + [_tr(_ak(s), "down", r) for s, r in ESCALATIONS[:2]])
    texts = ow.group_alerts(trs)
    assert len(texts) == 4, (
        "по два перехода в каждом семействе склейке не подлежат: ожидалось "
        "четыре отдельных сообщения, получено %r" % (texts,))


def test_a_lonely_escalation_keeps_its_own_text_next_to_a_glued_runner_group():
    """⚠️ Сегодня зелен случайно, по той же причине, что и сторож выше: его
    предмет — граница между склеенным семейством и одиночкой из другого."""
    assert ow.is_client_check(_ak("marina")), (
        "предпосылка: семейство эскалаций вообще участвует в склейке")
    trs = ([_tr(_rk(s), "down", r) for s, r in RUNNERS]
           + [_tr(_ak("marina"), "down", "attention_stale")])
    texts = ow.group_alerts(trs)
    assert len(texts) == 2, texts
    alone = [t for t in texts if "marina" in t]
    assert len(alone) == 1, texts
    for slug, _r in RUNNERS:
        assert slug not in alone[0], (
            "одинокая эскалация приклеилась к группе раннеров: %r" % (alone,))


def test_the_glued_escalation_text_names_every_slug_and_reason():
    """«Не читают у троих» без имён возвращает владельца в панель — то есть
    отменяет пер-слаговые ключи, ради которых всё и заводилось."""
    trs = [_tr(_ak(s), "down", r) for s, r in ESCALATIONS]
    texts = ow.group_alerts(trs)
    assert len(texts) == 1, texts
    glued = texts[0]
    for slug, reason in ESCALATIONS:
        assert slug in glued, ("склейка съела имя %r: %r" % (slug, glued))
        assert reason in glued, (
            "склейка съела причину %r клиента %r: `no_instance` (некого "
            "спросить) и `attention_stale` (человек не пришёл) чинятся "
            "РАЗНЫМИ действиями" % (reason, slug))


def test_the_glued_escalation_text_does_not_call_them_runners():
    """Склейка собирает СВОЙ заголовок: «раннеры chatter, 3 клиента» поверх
    списка протухших карточек — то же враньё, что и в ярлыке, только в другом
    месте кода."""
    trs = [_tr(_ak(s), "down", r) for s, r in ESCALATIONS]
    texts = ow.group_alerts(trs)
    assert len(texts) == 1, (
        "предпосылка: три перехода одного семейства обязаны склеиться в один "
        "текст. Без неё сторож зелен по построению — три отдельных сообщения "
        "слова «раннер» не содержат просто потому, что склейки не было; %r"
        % (texts,))
    glued = texts[0]
    assert "раннер" not in glued.lower(), (
        "склеенное сообщение про эскалации называет их раннерами: %r" % glued)


def test_non_client_alerts_still_come_first_and_verbatim():
    """Порядок вывода фиксирован и сегодня: сначала не-клиентские, потом
    клиентские по видам. Новое семейство встаёт в этот порядок предсказуемо, а
    чужие тексты не переписываются."""
    others = [_tr("disk", "down", "low_space")]
    trs = others + [_tr(_ak(s), "down", r) for s, r in ESCALATIONS]
    texts = ow.group_alerts(trs)
    assert texts[0] == ow.build_alert("disk", "down", "деталь: low_space"), texts


def test_downs_and_recovereds_of_the_new_family_stay_apart():
    """🚨 и ✅ в одной строке — сообщение, из которого нельзя понять, стало
    хуже или лучше."""
    trs = ([_tr(_ak(s), "down", r) for s, r in ESCALATIONS]
           + [_tr(_ak(s), "recovered", "attention_stale")
              for s in ("polina", "sofia")])
    texts = ow.group_alerts(trs)
    assert len(texts) == 3, (
        "три падения склеиваются, а два подъёма порога не берут: итого три "
        "текста; получено %r" % (texts,))


# ── состояние: чистка не имеет права выключить лампу ───────────────────────

def test_pruning_keeps_the_state_of_a_live_escalation_key(net):
    """`prune_client_state` убирает записи слагов, которых больше нет в
    ростере. Задев живой ключ нового семейства, она вымыла бы `alerted` — и
    владелец получил бы алерт заново КАЖДЫЙ цикл.

    Предпосылка обязательна: пока пробы эскалаций в цикле не выставлены,
    «живой ключ уцелел» истинно по построению — уцелеет что угодно, чего
    чистка не знает.
    """
    net["body"] = _payload(open_=2, stale_open=1)
    probes = _cycle()
    assert _att_keys(probes), "предпосылка: пробы эскалаций в цикле есть"
    state = {_ak(WITH_INSTANCE): {"fail": 3, "alerted": True},
             _ak(NO_INSTANCE): {"fail": 3, "alerted": True},
             "backend": {"fail": 0, "alerted": False}}
    pruned = ow.prune_client_state(state, probes)
    assert _ak(WITH_INSTANCE) in pruned and _ak(NO_INSTANCE) in pruned, pruned
    assert "backend" in pruned, "вычищено лишнее"


def test_pruning_drops_the_escalation_key_of_a_departed_slug(net):
    """Мёртвая запись однажды прочтётся как чья-то."""
    net["body"] = _payload(open_=2, stale_open=1)
    probes = _cycle()
    assert _ak("olga") not in probes, "предпосылка: olga из ростера ушла"
    state = {_ak("olga"): {"fail": 3, "alerted": True}}
    pruned = ow.prune_client_state(state, probes)
    assert _ak("olga") not in pruned, (
        "запись ушедшего из ростера клиента осталась: %r" % (pruned,))


def test_nothing_of_the_new_family_is_pruned_while_the_roster_is_unknown(net):
    """Красный `chatter_roster` = «состава не знаем», и чистка в этот момент
    вымыла бы `alerted` у всех — то есть проглотила бы первый алерт после
    восстановления чтения. Правило уже стоит на раннерах; новое семейство
    обязано жить по нему же, а не мимо."""
    probes = _cycle(roster={"error": "битый yaml"})
    state = {_ak(WITH_INSTANCE): {"fail": 2, "alerted": True}}
    assert ow.prune_client_state(state, probes) == state


def test_the_state_of_an_UNMEASURED_family_is_left_alone():
    """🔴 ДЫРА, названная мутационным гейтом: ветка «семейство не мерилось».

    Снимок эскалаций может отсутствовать ЦЕЛИКОМ — спросить нечем (снимка
    панели нет, ростер не прочитан), — при том что сам ростер зелёный. Чистка
    в этот момент вымыла бы `alerted`, и клиент, о котором владельцу УЖЕ
    сказали, промолчал бы НАВСЕГДА: `alerted` снимается только
    восстановлением, которого никто не измерит. Это ровно тот класс, что
    назван в §4.1 спеки про «подавление не ставит alerted», только с другого
    конца — тут его снимает чистка.

    Отличить «слаг ушёл из ростера» от «семейство не мерилось» можно лишь по
    тому, есть ли в цикле ХОТЬ ОДНА проба семейства. Пока её нет, трогать
    нельзя НИ ОДНУ запись — в том числе запись слага, которого в ростере уже
    нет: мы про него ничего не мерили и знать не можем.
    """
    probes = _probes(None)
    assert probes["chatter_roster"]["ok"] is True, (
        "предпосылка: ростер ЗЕЛЁНЫЙ — иначе сработало бы прежнее правило "
        "«состава не знаем, не трогаем ничего», и сторож проверял бы его")
    assert _att_keys(probes) == [], (
        "предпосылка: семейство эскалаций в этом цикле не мерилось вовсе")

    state = {
        _ak(WITH_INSTANCE): {"fail": 4, "alerted": True,
                             "alerted_reason": "attention_stale"},
        _ak("olga"): {"fail": 2, "alerted": True,
                      "alerted_reason": "no_instance"},
    }
    pruned = ow.prune_client_state(state, probes)

    assert pruned.get(_ak(WITH_INSTANCE)) == state[_ak(WITH_INSTANCE)], (
        "чистка тронула запись семейства, которое в этом цикле не мерилось: "
        "`alerted` снят, и о непрочитанных карточках владельцу скажут заново "
        "следующим циклом — или не скажут никогда; %r" % (pruned,))
    assert pruned.get(_ak("olga")) == state[_ak("olga")], (
        "запись слага вычищена по итогам цикла, в котором семейство НЕ "
        "мерилось: «ушёл из ростера» и «спросить было нечем» — разные вещи, "
        "и вторую нельзя выдавать за первую; %r" % (pruned,))


def test_an_unmeasured_family_does_not_freeze_the_runner_cleanup():
    """ПАРНАЯ ГРАНИЦА, и без неё дыру можно «закрыть» слишком широко.

    Починка вида «нет проб эскалаций — не чистим вообще ничего» остановила бы
    чистку РАННЕРОВ, которые в этом же цикле измерены прекрасно. Мёртвая
    запись однажды прочтётся как чья-то, поэтому правило обязано быть
    ПОСЕМЕЙНЫМ: не мерили эскалации — не трогаем эскалации, раннеров мерили —
    чистим раннеров.
    """
    probes = _probes(None)
    assert _rk(WITH_INSTANCE) in probes, (
        "предпосылка: раннеры в этом цикле измерены")
    assert _att_keys(probes) == [], "предпосылка: эскалации не измерены"

    state = {_rk("olga"): {"fail": 3, "alerted": True},
             _ak("olga"): {"fail": 3, "alerted": True}}
    pruned = ow.prune_client_state(state, probes)

    assert _rk("olga") not in pruned, (
        "чистка раннеров замерла из-за того, что не мерилось ЧУЖОЕ семейство")
    assert _ak("olga") in pruned, (
        "запись неизмеренного семейства вычищена заодно с измеренным")
