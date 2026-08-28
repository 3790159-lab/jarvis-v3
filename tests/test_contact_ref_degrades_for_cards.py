# -*- coding: utf-8 -*-
"""Сторожа варианта 2 арки «личность контакта»: ДЕГРАДАЦИЯ вместо падения.

Писаны ОТ РЕШЕНИЯ ВЛАДЕЛЬЦА и ДО кода.

ЧТО ЧИНИМ. Замер §1 посчитал места СБОРКИ `contact_id`, а не места ВХОДА.
Собирается он действительно в одной форме (`<peer>:<slug>`), но приходит и в
другой: `chatter/run.py:1086` задаёт `--contact` со значением `console-user`,
и три формы `demo_switch` тоже односегментны. На таком входе fail-closed
`peer_of` бросает — и КАРТОЧКА ВЛАДЕЛЬЦА не уходит вовсе.

ПОЧЕМУ ЭТО НЕСОГЛАСОВАННОСТЬ, А НЕ ВЫБОР. Тот же довод дословно уже применён
к соседнему пути: докстринг `slug_of` говорит, что `_persona_settings`
«ОБЯЗАН иметь фолбэк на primary — карточка должна уйти даже с битым слугом»,
и что дать ему бросающую функцию значило бы «поменять „не на том языке“ на
„никак“». Две карточки одной арки не могут судиться по разным правилам.

ГДЕ ПРОХОДИТ ГРАНИЦА — и она НЕ «везде помягче»:

  * значение АДРЕСУЕТ (по нему шлют) -> fail-closed, как было. Это
    `telegram_peer_of` на пути `telethon_run.py`. Тут молчаливый мусор
    означает сообщение НЕ ТОМУ человеку, и падение дешевле.
  * значение ПОДПИСЫВАЕТ (имя и ссылка в карточке) -> деградация. Тут
    молчаливый мусор означает кривую подпись, а падение означает, что
    владелец не узнал о лиде вовсе.

ЧЕМ ДЕГРАДАЦИЯ НЕ ЯВЛЯЕТСЯ. Она НЕ «вернуть голову, что бы ни пришло».
Ровно этого модуль и создан не допускать: на трёхсегментном формате следующей
арки голова — это `"instagram"`, то есть правдоподобный мусор вместо
собеседника. Поэтому неизвестная форма отдаётся ЦЕЛИКОМ: строку `"a:b:c"`
никто не примет за peer, а `"instagram"` — примет. Деградация обязана быть
ЗАМЕТНОЙ, а не правдоподобной.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from chatter.core.contact_ref import ContactRefError, peer_of, telegram_peer_of

# `peer_label_of` НЕ импортируется на уровне модуля НАМЕРЕННО. Соседний
# `test_no_raw_contact_id_split.py` этот урок уже записал: импорт ещё не
# существующего имени роняет файл НА СБОРЕ целиком, и результат AST-сторожей
# ниже был бы съеден чужим ImportError. Тогда красное «функции нет» было бы
# неотличимо от красного «подписывающее место её не зовёт», а это разные
# поломки с разной починкой.
def _label_of():
    from chatter.core.contact_ref import peer_label_of
    return peer_label_of

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── §2.3 остаётся в силе: сегодняшняя форма БАЙТ-В-БАЙТ как была ───────────

def test_todays_form_is_unchanged_byte_for_byte():
    """Деградация не имеет права шевельнуть сегодняшний путь.

    Если этот сторож покраснеет, вариант 2 сломал то, что чинить не просили.
    """
    assert _label_of()("telegram:12345:volska") == "12345"


# ── односегментный вход: карточка УХОДИТ ───────────────────────────────────

@pytest.mark.parametrize("contact_id", [
    "console-user",          # chatter/run.py:1086, дефолт --contact
    "demo",                  # формы demo_switch
    "demo2",
    "yarina",
])
def test_single_segment_degrades_instead_of_raising(contact_id):
    """Односегментный `contact_id` обязан вернуть подпись, а не бросить."""
    assert _label_of()(contact_id) == contact_id


# ── ГЛАВНОЕ: деградация НЕ УГАДЫВАЕТ ───────────────────────────────────────

def test_unknown_form_returns_the_whole_id_not_a_plausible_head():
    """Трёхсегментная форма НЕ имеет права отдать голову.

    Это тот самый дефект, ради которого модуль написан: `"instagram"` вместо
    собеседника доедет до клиента молча. Деградация обязана вернуть строку,
    которую никто не спутает с peer.
    """
    contact_id = "instagram:777:volska"
    got = _label_of()(contact_id)
    assert got != "instagram", (
        "деградация вернула ПРАВДОПОДОБНУЮ голову — ровно тот молчаливый "
        "неверный ответ, против которого написан модуль")
    assert got == contact_id


@pytest.mark.parametrize("junk", [None, "", ":", "a:", ":b", 12345, "a:b:c:d"])
def test_label_never_raises_whatever_arrives(junk):
    """Подпись не имеет права уронить доставку карточки НИ НА ЧЁМ.

    Функция с оговоркой «кроме вот такого входа» не годится: карточка либо
    уходит всегда, либо мы не решили задачу.
    """
    got = _label_of()(junk)
    assert isinstance(got, str) and got != "", (
        "подпись обязана быть непустой строкой, получили %r" % (got,))


# ── ВСТРЕЧНАЯ ПОЛОВИНА: адресация осталась fail-closed ─────────────────────

@pytest.mark.parametrize("bad", ["console-user", "instagram:777:volska"])
def test_addressing_path_stays_fail_closed(bad):
    """`telegram_peer_of` и `peer_of` смягчать НЕЛЬЗЯ.

    Самая дешёвая правка под требование «карточка должна уйти» — размягчить
    сам `_parts`. Тогда деградация протекла бы на путь, по которому ШЛЮТ, и
    сообщение уехало бы не тому человеку. Без этого сторожа такая правка
    выглядела бы успехом.
    """
    with pytest.raises(ContactRefError):
        telegram_peer_of(bad)
    with pytest.raises(ContactRefError):
        peer_of(bad)


# ── AST: подписывающие места зовут ИМЕННО деградирующую функцию ────────────

# Литеральный список, а не обход дерева: выведенный список согласен с кодом по
# определению и промолчит там, где код забыл. Каждый пункт — место, где
# результат уходит в `display_name`/`contact_link`, то есть ПОДПИСЫВАЕТ.
LABEL_SITES = (
    "chatter/run.py",
    "chatter/notify/control_bot.py",
    "app/services/tamapi_metrics.py",
)

# Путь, который АДРЕСУЕТ. Деградирующей функции здесь быть не должно вовсе.
ADDRESSING_SITES = (
    "chatter/telethon_run.py",
)


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


@pytest.mark.parametrize("rel", LABEL_SITES)
def test_label_sites_call_the_degrading_function(rel):
    """Подписывающее место, зовущее `peer_of`, роняет карточку владельцу."""
    path = REPO_ROOT / rel
    assert path.exists(), "файл %s исчез из дерева" % rel
    called = _called_names(path)
    assert "peer_label_of" in called, (
        "%s не зовёт `peer_label_of` — карточка по-прежнему падает на "
        "односегментном contact_id" % rel)
    assert "peer_of" not in called, (
        "%s всё ещё зовёт бросающий `peer_of` для подписи" % rel)


@pytest.mark.parametrize("rel", ADDRESSING_SITES)
def test_addressing_sites_do_not_use_the_degrading_function(rel):
    """Встречная половина к предыдущему: деградация не течёт в адресацию."""
    path = REPO_ROOT / rel
    assert path.exists(), "файл %s исчез из дерева" % rel
    called = _called_names(path)
    assert "peer_label_of" not in called, (
        "%s ПОДПИСЫВАЮЩЕЙ функцией адресует — сообщение может уехать не "
        "тому человеку" % rel)
