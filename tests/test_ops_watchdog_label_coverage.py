# -*- coding: utf-8 -*-
"""Ни один ключ, который цикл СПОСОБЕН выставить, не остаётся без ярлыка.

ПОВОД — сторож, отвечавший не на тот вопрос. В
`tests/test_ops_watchdog_alert_delivery.py` живёт
`test_every_static_probe_key_of_a_full_cycle_has_a_label`, и он заявляет, что
проверяет «каждую пробу, которую цикл реально выставляет». На деле он зовёт
`probe_all()` ТОЛЬКО с `chatter_snapshot`. Все семейства, приезжающие СВОИМИ
снимками — `token_at_rest`, `restore_drill`, `secrets_bundle`, `panel_client`,
`escalation:*`, `outgoing:*` — в его «полный цикл» не попадают ВООБЩЕ.

ЦЕНА ДОКАЗАНА ФАКТОМ: проба `token_at_rest` живёт в цикле с 25.08 БЕЗ ЯРЛЫКА.
`label_for` отдаёт сырой ключ, и владелец получил бы «🚨 DOWN: token_at_rest.»
— имя переменной вместо фразы. Сторож всё это время был ЗЕЛЁНЫМ, потому что
смотрел не туда ([[jarvis-checks-that-answer-the-wrong-question]]). Это ровно
тот класс, против которого написана спека
`2026-08-27-alarm-channel-outside-the-broken-transport.md`: успокаивающая
лампа рядом с непроверенным местом.

ТРЕБОВАНИЕ СФОРМУЛИРОВАНО ОТ ПРИНЦИПА, а не от сегодняшнего списка семейств:

  1. состав семейств пинится ЛИТЕРАЛОМ (`BUILDERS`) и сверяется с сигнатурой
     `probe_all()` В ОБЕ СТОРОНЫ. Появилось новое семейство — сторож КРАСНЕЕТ
     и требует завести ему снимок здесь. Иначе слепота просто переехала бы на
     уровень выше: список, выведенный из кода, согласен с кодом по определению
     ([[jarvis-literal-lists-not-introspection]]);
  2. каждое семейство обязано РЕАЛЬНО дать ключи в полном цикле — снимок,
     построенный неверно и молча не давший ни одной пробы, сузил бы охват и
     выглядел бы как «всё проверено»;
  3. каждый статический ключ обязан иметь ярлык.

Пер-клиентные ключи (`chatter_runner:<slug>`, `escalation:<slug>`,
`outgoing:<slug>`) — законное исключение: слаг известен только в рантайме, в
`LABELS` их нет и быть не может. Их ярлык собирает `label_for` из семейства, и
проверяются они отдельным сторожем ниже.

НА МОМЕНТ НАПИСАНИЯ файл КРАСНЫЙ: `token_at_rest` без ярлыка — это найденный,
а не выдуманный дефект.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_label_coverage", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ow
_SPEC.loader.exec_module(ow)


SLUG = "volska"

# Снимок, содержимое которого пробе безразлично: ключ регистрируется самим
# фактом снимка, а вердикт внутри пусть будет каким угодно — сторож про ЯРЛЫК,
# а не про цвет.
ANY = {"снимок": "непустой"}

_ROSTER = {"clients": [{"slug": SLUG, "enabled": True}], "error": None}


def _chatter_with_roster():
    return {"processes": [], "roster": _ROSTER, "beats": {SLUG: 5.0},
            "legacy_beat_age": 10.0, "guardian_lock_pid": None,
            "guardian_beat_age": 5.0, "root": ROOT}


def _chatter_without_roster():
    """Старое окружение: ростера нет, цикл идёт другой веткой.

    Ветка не мёртвая — на ней живёт безымянный `chatter_runner`, и ключ у неё
    СВОЙ. Полный цикл, не прошедший обе ветки, — это половина цикла.
    """
    return {"processes": [], "guardian_lock_pid": None,
            "guardian_beat_age": 5.0, "root": ROOT}


# ── ЛИТЕРАЛЬНЫЙ состав семейств ────────────────────────────────────────────
# Ключ — параметр `probe_all()`, значение — как построить снимок, дающий
# ключи. Правка `probe_all()` без правки этого словаря = красное.
BUILDERS = {
    "chatter_snapshot": _chatter_with_roster,
    "worktree_snapshot": lambda: dict(ANY),
    "secrets_snapshot": lambda: dict(ANY),
    "panel_client_snapshot": lambda: dict(ANY),
    "restore_drill_snapshot": lambda: dict(ANY),
    "token_at_rest_snapshot": lambda: dict(ANY),
    "attention_snapshot": lambda: {"clients": {SLUG: dict(ANY)}},
    "outgoing_snapshot": lambda: {"clients": {SLUG: dict(ANY)}},
    # 27.08, §5.1: связь наружу. Заведено ПО ТРЕБОВАНИЮ этого же сторожа —
    # он покраснел на появлении нового параметра `probe_all`, ровно как задуман.
    # Провода перечислены литералом, а не взяты из `ow.REACH_WIRES`: список,
    # выведенный из кода, согласится с кодом по определению и промолчит там,
    # где ярлык забыли завести.
    "reachability_snapshot": lambda: {
        "terms": {w: dict(ANY) for w in ("tg_api", "llm_api", "r2")}},
}


def _snapshot_params() -> set[str]:
    return {n for n in inspect.signature(ow.probe_all).parameters
            if n.endswith("_snapshot")}


def _http_ok(path):
    """Бэкенд жив: только тогда цикл выставляет ключи ручек `/ops/*`."""
    return 200


def _big_disk(path):
    return (1, 1, 10 * 1024 ** 3)


def _cycle_keys(skip: str | None = None) -> set[str]:
    """Ключи ПОЛНОГО цикла: все семейства + обе ветки chatter-снимка."""
    kwargs = {name: build() for name, build in BUILDERS.items() if name != skip}
    keys = set(ow.probe_all(_http_ok, _big_disk, **kwargs))
    if skip != "chatter_snapshot":
        kwargs["chatter_snapshot"] = _chatter_without_roster()
        keys |= set(ow.probe_all(_http_ok, _big_disk, **kwargs))
    return keys


# ── 1. состав семейств: в обе стороны ──────────────────────────────────────
def test_the_list_of_probe_families_is_pinned_in_both_directions():
    """Новое семейство обязано ЛОМАТЬ этот файл.

    Это единственное место, где «полный цикл» перестаёт быть на честном слове.
    Сторож, выводящий список семейств из самой сигнатуры, согласился бы с
    любым её видом — и снова оказался бы зелёным на непроверенном.
    """
    actual = _snapshot_params()
    known = set(BUILDERS)
    new = actual - known
    gone = known - actual
    assert not new, (
        "в probe_all() появилось семейство проб, о котором сторож ярлыков не "
        "знает: %s. Заведи ему снимок в BUILDERS и убедись, что у его ключей "
        "есть ярлык в LABELS, — иначе владелец получит «🚨 DOWN: <имя "
        "переменной>»" % sorted(new))
    assert not gone, (
        "семейство исчезло из probe_all(), а снимок для него остался: %s — "
        "«полный цикл» здесь врёт составом" % sorted(gone))


def test_every_family_actually_contributes_keys_to_the_full_cycle():
    """Снимок, не давший НИ ОДНОГО ключа, — молча суженный охват.

    Ровно так и слепнет «полный цикл»: не тем, что кто-то убрал проверку, а
    тем, что семейство перестало доезжать, и никто этого не заметил. Проверка
    вычитанием: без семейства ключей обязано стать МЕНЬШЕ.
    """
    full = _cycle_keys()
    for family in BUILDERS:
        without = _cycle_keys(skip=family)
        assert full - without, (
            "семейство %s не добавляет в цикл ни одного ключа — его снимок "
            "построен неверно, и всё, что о нём утверждают сторожа ниже, "
            "утверждается о пустом множестве" % family)


# ── 2. ярлык у каждого статического ключа ──────────────────────────────────
def test_every_static_key_the_cycle_can_emit_has_a_human_label():
    """Сырой ключ в сообщении — имя переменной вместо новости.

    `label_for` отдаёт неизвестный ключ КАК ЕСТЬ намеренно: подставить «что-то
    сломалось» значило бы скрыть, что появилась проба без ярлыка. Скрывать её
    и должен этот тест — вслух.
    """
    naked = sorted(k for k in _cycle_keys()
                   if not ow.is_client_check(k) and ow.label_for(k) == k)
    assert not naked, (
        "пробы без ярлыка: %s. Владелец получит «🚨 DOWN: %s.» — имя "
        "переменной вместо фразы, и пойдёт искать, что это" % (naked, naked[0]))


def test_labels_are_not_empty_or_equal_to_each_other():
    """Ярлык-заглушка и ярлык-дубль — те же грабли с другого конца.

    Две проверки с одинаковым текстом читаются как «упало дважды одно и то же»
    — этот случай в модуле уже описан как ЛОВУШКА 2 для раннера и гардиана.
    """
    labels = {}
    for key in _cycle_keys():
        if ow.is_client_check(key):
            continue
        label = ow.label_for(key)
        assert isinstance(label, str) and label.strip(), "%s: пустой ярлык" % key
        assert label not in labels, (
            "ярлык %r достался двум пробам: %s и %s" % (label, labels[label], key))
        labels[label] = key


# ── 3. пер-клиентные ключи: ярлык собирается в рантайме ────────────────────
def test_per_client_keys_get_their_label_from_the_family():
    """Исключение проверяется, а не выводится из-под проверки.

    Пер-клиентных ключей в `LABELS` нет и быть не может: слаг известен только
    в рантайме. Но «нет в LABELS» не значит «можно без ярлыка» — иначе
    исключение стало бы дырой ровно того же вида.
    """
    per_client = sorted(k for k in _cycle_keys() if ow.is_client_check(k))
    assert per_client, (
        "в полном цикле нет ни одного пер-клиентного ключа — снимки ростера, "
        "эскалаций и отправки построены неверно, и проверка ниже пуста")
    for key in per_client:
        label = ow.label_for(key)
        assert label != key, "пер-клиентный ключ без ярлыка: %s" % key
        assert SLUG in label, (
            "ярлык %r не называет клиента — при двух клиентах владелец не "
            "поймёт, чей именно" % label)


def test_every_client_family_is_represented_in_the_full_cycle():
    """Три пер-клиентных семейства — три разных новости.

    Раннер чинит гардиан, эскалацию чинит человек, который придёт в диалог,
    отправку из панели — третье. Семейство, выпавшее из полного цикла, унесло
    бы с собой и проверку своего ярлыка.
    """
    keys = _cycle_keys()
    families = {ow.client_family_of(k) for k in keys if ow.is_client_check(k)}
    assert families == set(ow.CLIENT_FAMILIES), (
        "в полном цикле представлены не все пер-клиентные семейства: %s "
        "против объявленных %s"
        % (sorted(families), sorted(ow.CLIENT_FAMILIES)))
