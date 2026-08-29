# -*- coding: utf-8 -*-
"""Сторожа пары C, ЗАКАЗАННЫЕ МУТАЦИОННЫМ ГЕЙТОМ (§11, «слепых ноль»).

Гейт `scripts/mutate_web_c_envelope.py` сломал одиннадцать решений §2–§5 и не
получил красного. Разбор поимённо развёл их натрое, и только одна куча —
настоящая слепота; она здесь.

🔴 САМАЯ ДОРОГАЯ ИЗ НАЙДЕННЫХ — ПЕРВЫЕ ДВА СТОРОЖА. Сторож 27 сверяет
`MIGRATED_TABLES` и `FLAG_PREFIXES` со СХЕМОЙ и с КОДОМ, и сверяет хорошо, в
обе стороны. Но `MIGRATED_TABLES` — это литерал В ФАЙЛЕ СТОРОЖА, а мигрирует
`chatter/storage/db.py:_CONTACT_ID_TABLES`, и между ними НЕТ НИ ОДНОЙ
проверки. Убрать строку из боевого списка можно молча: сторож продолжит
сверять свой литерал со схемой и останется зелёным, а таблица не мигрирует
НИКОГДА — миграция идёт один раз, по факту «двухсегментных не осталось».

Это в точности тот дефект, ради которого арка и написана, только на этаж выше:
перепись занизили молча — теперь не в спеке, а в коде. Четвёртый раз по тому
же механизму (§8 п.3).
"""
from __future__ import annotations

import sqlite3

import pytest

from chatter.core.contact_ref import ContactRefError, peer_of
from chatter.storage.db import (_CONTACT_ID_FLAG_PREFIXES, _CONTACT_ID_TABLES,
                                _ORIGIN_KEY_TABLES, ContactIdMigrationBlocked,
                                Store)
from tests.chatter.test_web_c_migration import (FLAG_PREFIXES,
                                                MIGRATED_TABLES, NEW_A,
                                                _seed_old, _sha)


# ═══ дыра 1: боевой список таблиц никто не сверяет с литералом сторожа ══════

def test_boevoy_spisok_tablits_soglasen_s_literalom_storozha():
    """`db._CONTACT_ID_TABLES` == литерал §4.1 из сторожа, в ОБЕ стороны.

    Сторож 27 держит связку «литерал <-> схема». Эта — вторую половину той же
    цепи, «литерал <-> КОД». Без неё цепь разорвана ровно посередине: схема и
    литерал согласны, а мигрирует третий список, о котором никто не спросил.

    Направления оба и они разные по цене:
      * таблица есть в коде, но нет в литерале — сторож 27 её не судит вовсе,
        то есть она мигрирует НЕПРОВЕРЕННОЙ;
      * таблица есть в литерале, но нет в коде — она НЕ МИГРИРУЕТ, а сторож 27
        зелен, потому что со схемой литерал по-прежнему согласен.
    """
    code = set(_CONTACT_ID_TABLES)
    named = set(MIGRATED_TABLES)
    assert code == named, (
        "боевой список миграции и литерал сторожа разошлись.\n"
        "  только в коде (мигрируют НЕПРОВЕРЕННЫМИ): %r\n"
        "  только в литерале (НЕ МИГРИРУЮТ, а сторож 27 зелен): %r\n"
        "Миграция идёт ОДИН раз, по факту «двухсегментных не осталось»: "
        "таблица, пропущенная сегодня, остаётся в старой форме навсегда."
        % (sorted(code - named), sorted(named - code)))
    assert len(_CONTACT_ID_TABLES) == len(set(_CONTACT_ID_TABLES)), (
        "в боевом списке есть повторы (%r): перепись пройдёт по таблице "
        "дважды, а сверка числа строк — по одной записи словаря"
        % (sorted(_CONTACT_ID_TABLES),))


def test_boevoy_spisok_prefiksov_soglasen_s_literalom_storozha():
    """То же для префиксов ключей `runtime_flags`, и цена здесь та же.

    `contact_id` живёт не только в колонках: три префикса вшивают его в КЛЮЧ
    (§1.1). Префикс, выпавший из боевого списка, — это живая карточка
    эскалации либо серия промахов профиля, которая осиротеет МОЛЧА: ключ
    останется на старую форму, а контакта под этой формой больше не будет.
    """
    code = set(_CONTACT_ID_FLAG_PREFIXES)
    named = set(FLAG_PREFIXES)
    assert code == named, (
        "боевой список префиксов и литерал сторожа разошлись.\n"
        "  только в коде: %r\n  только в литерале: %r" % (sorted(code - named),
                                                          sorted(named - code)))


def test_boevoy_spisok_perestraivaemyh_tablits_ne_ussoh():
    """Таблицы §5 (`origin_msg_id` -> TEXT) — тем же правилом.

    Список короткий и оттого особенно тихий: строка, выпавшая отсюда, не
    краснеет нигде, а колонка остаётся INTEGER-аффинной — то есть `"007"` и
    `"7"` снова становятся ОДНИМ ключом идемпотентности, и второй счёт
    подавляется как дубль.
    """
    assert set(_ORIGIN_KEY_TABLES) == {"quotes", "invoices"}, (
        "список перестраиваемых таблиц стал %r: §5 называет ровно две."
        % (sorted(_ORIGIN_KEY_TABLES),))


# ═══ дыра 2: чужая голова в ТРЁХСЕГМЕНТНОЙ форме ═══════════════════════════

@pytest.mark.parametrize("bad, why", [
    ("12345:my:slug", "числовая голова: опознание «по числу» через заднюю дверь"),
    ("Telegram:111:volska", "регистр: 'Telegram' и 'telegram' дают ДВА "
                            "контакта на одного человека, и оба выглядят живыми"),
    ("instagram:111:volska", "канал, которого в реестре ещё нет"),
    ("tg:111:volska", "сокращение вместо имени канала"),
])
def test_chuzhaya_golova_v_tryohsegmentnoy_forme_OTVERGAETSYA(bad, why):
    """Встречная половина ЛИТЕРАЛЬНОГО реестра — внутри РАЗБОРА (§3.1).

    Сторож Д3 судит формы с неверным ЧИСЛОМ сегментов, сторож 4 — сборку.
    Разбор трёхсегментной строки с чужой головой не судил никто: гейт снял
    `if parts[0] not in KNOWN_CHANNELS` и прошёл мимо всех.

    Цена названа в самом коде: без этой проверки `"12345:my:slug"` проходит
    как трёхсегментная форма с каналом `12345`, и опознание «по числовой
    голове», которое арка и убирала, возвращается через заднюю дверь — только
    теперь молча и с правдоподобным ответом.
    """
    with pytest.raises(ContactRefError):
        peer_of(bad)


# ═══ дыра 3: индексы проверялись на СВЕЖЕЙ базе, а не после ПЕРЕСТРОЙКИ ═════

@pytest.mark.parametrize("table, index", [("quotes", "idx_quotes_contact"),
                                          ("invoices", "idx_invoices_contact")])
def test_indeksy_peresozdany_imenno_PERESTROYKOY(tmp_path, table, index):
    """Сторож 28 брал СВЕЖУЮ базу — а в свежей перестройки не происходит.

    В новой базе `origin_msg_id` объявлен TEXT сразу, `_origin_key_legacy_tables`
    возвращает пусто, и `_rebuild_origin_key` не зовётся НИ РАЗУ. Индексы там
    создаёт `_SCHEMA`, а вовсе не перестройка: сторож проверял схему и отвечал
    не на тот вопрос ([[jarvis-checks-that-answer-the-wrong-question]]).

    Здесь база СТАРАЯ: колонка INTEGER, значит перестройка обязана произойти
    по-настоящему — и вместе с таблицей уйдут индексы. Забытый шаг №4 §5.2
    ничего не ломает громко, он просто делает ленту панели медленнее с каждым
    месяцем, и заметят это не скоро.
    """
    db = tmp_path / "rebuild.db"
    with Store(db):
        pass
    _make_origin_legacy(db, table)

    with Store(db) as s:
        decl = [r["type"] for r in s._conn.execute("PRAGMA table_info(%s)" % table)
                if r["name"] == "origin_msg_id"]
        assert decl == ["TEXT"], (
            "предпосылка: перестройка обязана была случиться, объявленный тип "
            "%r" % (decl,))
        names = {r[1] for r in s._conn.execute("PRAGMA index_list(%s)" % table)}
        assert index in names, (
            "после НАСТОЯЩЕЙ перестройки %s индекса %s нет (есть %r): индексы "
            "уходят вместе с таблицей, и пересоздать их — отдельный шаг "
            "(§5.2 п.4)" % (table, index, sorted(names)))


def _make_origin_legacy(path, table: str) -> None:
    """Вернуть таблице СТАРУЮ колонку `origin_msg_id INTEGER`.

    DDL берётся из самой базы и правится подстановкой, а не пишется литералом:
    литеральный DDL устарел бы от первой же новой колонки и красил бы сторожа
    по своей вине."""
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        assert row, "в базе нет таблицы %s" % table
        legacy = row[0]
        for spaced in ("origin_msg_id     TEXT", "origin_msg_id        TEXT",
                       "origin_msg_id TEXT"):
            legacy = legacy.replace(spaced, spaced.replace("TEXT", "INTEGER"))
        assert "INTEGER" in legacy.split("origin_msg_id")[1][:30], (
            "подстановка не сработала — стенд построил бы НЕ старую базу и "
            "зеленел бы по построению")
        conn.execute("DROP TABLE %s" % table)
        conn.execute(legacy)
        conn.commit()
    finally:
        conn.close()


# ═══ дыра 4: два ПРЕДОХРАНИТЕЛЯ, которые срабатывают только при аварии ═════

def test_poterya_strok_pri_perepisi_eto_OTKAZ(tmp_path, monkeypatch):
    """Сверка числа строк ДО и ПОСЛЕ — предохранитель, а не украшение.

    Гейт снял `if lost:` и не покраснел нигде: в исправной переписи строки не
    теряются НИКОГДА, поэтому ветку не исполняет ни один стенд. Предохранитель,
    который не проверен, — это `except: pass`, только длиннее.

    Авария вносится подменой `_row_counts`: сторож судит не выдуманную потерю,
    а ПРОВОДКУ — «сравнение сделано, и несовпадение фатально». Строка,
    потерянная при замене, — это переписка лида, которой больше нет, и замена,
    её потерявшая, иначе выглядит как успех.
    """
    db = tmp_path / "loss.db"
    _seed_old(db)
    before = _sha(db)

    real = Store._row_counts
    calls = {"n": 0}

    def _lying(self):
        calls["n"] += 1
        got = real(self)
        if calls["n"] > 1:               # «после» — на одну строку меньше
            got = dict(got)
            got["messages"] = got.get("messages", 1) - 1
        return got

    monkeypatch.setattr(Store, "_row_counts", _lying)
    with pytest.raises(ContactIdMigrationBlocked) as caught:
        with Store(db):
            pass
    assert "messages" in str(caught.value), (
        "отказ не называет таблицу, в которой потерялись строки: %r"
        % (str(caught.value),))
    assert _sha(db) == before, (
        "после отказа база изменилась: перепись, потерявшая строку, обязана "
        "оставить базу в состоянии ДО, а не «почти мигрированной»")


def test_perepis_OBYAZANA_sprosit_o_rezultate(tmp_path, monkeypatch):
    """Проверка ПОСЛЕ — то же самое: «миграция, о результате которой не
    спросили, — это надежда, а не миграция».

    Гейт снял `_assert_identity_migrated()` и не покраснел: исправная перепись
    и без него оставляет ноль двухсегментных, поэтому проверка ПОСЛЕ не
    отличается от её отсутствия ничем — пока перепись исправна.

    Авария вносится подменой самой переписи на пустышку. Сторож судит проводку:
    результат СПРОШЕН, и несовпадение фатально. Без него молча разъехавшаяся
    перепись выглядит как успешная, а обнаружится на карточке или на счёте.
    """
    db = tmp_path / "silent.db"
    _seed_old(db)

    monkeypatch.setattr(Store, "_apply_identity_migration",
                        lambda self, *a, **k: None)
    with pytest.raises(ContactIdMigrationBlocked) as caught:
        with Store(db):
            pass
    assert "старая форма осталась" in str(caught.value), (
        "отказ не говорит, ЧТО именно не сошлось: %r" % (str(caught.value),))


def test_predposylka_ispravnaya_perepis_prohodit_BEZ_podmen(tmp_path):
    """Встречная половина к обоим предохранителям выше.

    Без неё оба зелены по построению у реализации, которая отказывает ВСЕГДА:
    `pytest.raises` довольна любым отказом, в том числе тем, что случился бы и
    без подмены."""
    db = tmp_path / "ok.db"
    _seed_old(db)
    with Store(db) as s:
        left = s._legacy_contact_ids()
        assert not left, "исправная перепись оставила старую форму: %r" % (left,)
        got = s._conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE contact_id=?", (NEW_A,)).fetchone()[0]
        assert got == 1, "контакт не переехал на трёхсегментную форму"
