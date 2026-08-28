# -*- coding: utf-8 -*-
"""Сторожа пары D, ЗАКАЗАННЫЕ МУТАЦИОННЫМ ГЕЙТОМ (§8, «слепых ноль»).

Гейт `scripts/mutate_web_d_panel_send.py` сломал четыре решения §2 и НЕ получил
красного ни от одного сторожа §5. Разбор каждого случая показал не слабую
проверку, а ОТСУТСТВУЮЩУЮ: решение принято кодом и описано в его комментарии,
но сторожа под него не написали — ни спека §5 его не назвала, ни заход
сторожей не добавил.

Это ровно та половина, ради которой гейт и гоняется: суита §5 зелёная и без
этих четырёх, и зелёной осталась бы, если бы завтра любое из четырёх решений
отменили правкой в одну строку.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ, А НЕ ДОПИСКОЙ В `test_web_d_panel_send.py`. Тех
сторожей писали ОТ СПЕКИ И ДО КОДА, и это их ценность: они не знают, как
реализация выглядит. Эти четыре написаны ПОСЛЕ кода и по следу гейта — знание
у них другое, и смешивать два происхождения в одном файле значит через месяц
не отличить одно от другого.

Стенд, двойники и фикстуры — соседние: заводить второй `store` на те же
таблицы значило бы завести два ответа на вопрос «как выглядит стенд».
"""
from __future__ import annotations

import pytest

from tests.chatter.test_web_d_panel_send import (  # noqa: F401 — фикстура
    MOON, MOON_CHANNEL, WEB, _core, _core_attr, _Ctx, _Deliverer, _deliver,
    _enqueue, _personas, _register, _row, store)


# ═══ дыра 1: повторная регистрация доставщика ═══════════════════════════════

def test_povtornaya_registraciya_dostavshika_GROMKAYA(store):
    """Два доставщика на один канал — это два ответа на «чем шлём».

    Гейт снял проверку столкновения (`register_deliverer`, ветка `existing is
    not None`) и не покраснел нигде: суита регистрирует каждый канал ровно
    один раз, поэтому столкновения в ней не случается НИКОГДА.

    Цена молчания названа в самом коде: «второй молча победил бы первого», то
    есть сообщение уехало бы не тем путём, которым его собирался отправить
    автор первого. В процессе, где живут два канала и один из них
    перерегистрируется при переподключении, это тихая подмена транспорта.

    Сторож требует ГРОМКОГО, а не «последний выигрывает». Форма отказа не
    навязывается: любое исключение годится, молчание — нет.
    """
    first = _Deliverer(msg_id="a-1")
    second = _Deliverer(msg_id="b-1")
    undo = _register(MOON_CHANNEL, first)
    try:
        with pytest.raises(Exception) as caught:  # noqa: B017 — форма не навязана
            _register(MOON_CHANNEL, second)
        assert MOON_CHANNEL in str(caught.value), (
            "отказ не называет канал (%r): разбирать «чей это доставщик» "
            "придётся по трассировке" % (str(caught.value),))
        deliverer_for = _core_attr("deliverer_for", "§2.1 называет реестр так")
        assert deliverer_for(MOON_CHANNEL) is first, (
            "после отказа в реестре лежит ВТОРОЙ доставщик: отказ, который не "
            "оставил реестр нетронутым, хуже отсутствия отказа — он и шумит, "
            "и подменяет")
    finally:
        undo()


def test_povtornaya_registraciya_TOGO_ZHE_dostavshika_NE_otkaz(store):
    """Встречная половина: повторный импорт модуля канала — норма.

    Без неё «громко» лечится тем, что регистрация становится обязательной
    ровно один раз за процесс, — и первый же двойной импорт
    (`import chatter.telethon_run` из панели, §2.5) роняет панель на ровном
    месте. Столкновение — это ДРУГОЙ доставщик, а не второй вызов.
    """
    one = _Deliverer(msg_id="a-1")
    undo = _register(MOON_CHANNEL, one)
    try:
        _register(MOON_CHANNEL, one)     # повтор ТЕМ ЖЕ объектом — обязан молчать
        deliverer_for = _core_attr("deliverer_for", "§2.1 называет реестр так")
        assert deliverer_for(MOON_CHANNEL) is one, (
            "повторная регистрация того же доставщика подменила его в реестре")
    finally:
        undo()


# ═══ дыра 2: канальная половина ответа живёт У ДОСТАВЩИКА ══════════════════

def test_kanalnaya_polovina_otveta_zhivyot_u_dostavshika(store):
    """§2.6: «подключён ли канал, не закрылось ли окно» спрашивают у канала.

    Гейт снял ветку `if ctx is not None: ask = getattr(deliverer,
    "can_send_now")` — и суита не заметила: единственный её вызов идёт БЕЗ
    `ctx`, то есть ровно мимо этой ветки.

    Цена молчания: правила WhatsApp (окно 24 часа) и Instagram переехали бы в
    ядро, и ядро снова начало бы знать каналы поимённо — тот самый признак
    провала, ради которого §2.1 и написана. Либо, что тише, окно перестало бы
    спрашиваться вовсе: панель показала бы поле ввода в закрытом окне.
    """
    Refusal = _core_attr("Refusal", "§2.6 отвечает отказом, а не bool")
    can_send_now = _core_attr("can_send_now", "§2.6 называет её так")
    marker = "лунная почта принимает только по вторникам"

    deliverer = _Deliverer(msg_id="m-1")
    deliverer.can_send_now = lambda ctx, contact_id: Refusal(
        "channel_window_closed", marker)
    undo = _register(MOON_CHANNEL, deliverer)
    try:
        ctx = _Ctx(store, _personas(store))
        without_ctx = can_send_now(MOON)
        assert without_ctx is None, (
            "без контекста процесса ядро уже отказало (%r): канальную "
            "половину спросили там, где спросить её не у кого"
            % (without_ctx,))
        with_ctx = can_send_now(MOON, ctx)
        assert with_ctx is not None and marker in str(getattr(with_ctx, "human", "")), (
            "ядро не спросило доставщика: ответ %r вместо канального отказа "
            "«%s». Значит канальные правила придётся класть в ядро — то есть "
            "ядро снова знает каналы поимённо (§2.1)" % (with_ctx, marker))
    finally:
        undo()


# ═══ дыра 3: ответ доставщика, который НЕ SentRef ══════════════════════════

class _MuteDeliverer:
    """Доставщик, отвечающий «ушло» СЛОВОМ вместо `SentRef`.

    Явный класс, а не мок: автомок истинен всегда и на вопрос «а это SentRef?»
    ответил бы «да» ([[jarvis-magicmock-truthy-spins-the-loop]]).
    """

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple] = []

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.answer


@pytest.mark.parametrize("answer, why", [
    ("ушло", "строка: похоже на успех и читается как успех"),
    (None, "None: доставщик промолчал, а молчание — не ответ"),
    (True, "bool: истинность подделывает `SentRef` лучше всего"),
])
def test_otvet_dostavshika_ne_SentRef_eto_POVTOR_a_ne_uspeh(store, answer, why):
    """Ответ не по контракту — это «не знаю, ушло ли», и он ОБЯЗАН повториться.

    Гейт снял `isinstance(sent, SentRef)` и не покраснел: все двойники суиты
    отдают настоящий `SentRef`, поэтому ветка не исполняется ни разу.

    Решение названо в коде и оно НЕ очевидно: считать успехом — записать в
    историю неотправленное; считать отказом — похоронить, возможно,
    доставленное. Выбран третий вердикт, и именно он остаётся непроверенным.

    🔴 ПРОВЕРЯЕТСЯ ПРИЧИНА, А НЕ ВЕРДИКТ — и это поправка по второму прогону
    гейта. Первая редакция требовала `retry`, и мутация «снять проверку
    контракта» её ПЕРЕЖИЛА: без явной проверки `sent.msg_id` роняет
    `AttributeError`, тот ловится общим `except Exception`, и вердикт выходит
    ТОТ ЖЕ `retry`. Вердикт совпал, смысл — нет: владелец получает в
    `last_error` «AttributeError: 'str' object has no attribute 'msg_id'»
    вместо «доставщик канала ответил не SentRef». Первое читается как поломка
    НАШЕГО кода и уводит разбор в наш стек; второе называет виновника — чужой
    доставщик, нарушивший контракт. Сторож, смотрящий на вердикт, обе истории
    считает одной ([[jarvis-checks-that-answer-the-wrong-question]]).
    """
    deliverer = _MuteDeliverer(answer)
    undo = _register(MOON_CHANNEL, deliverer)
    try:
        _enqueue(store, MOON, "ответ не по контракту", token="tok-contract-%r" % (answer,))
        row = [r for r in store.pending_outgoing() if r["contact_id"] == MOON][0]
        verdict = _deliver(store, row, now=1000.0, personas=_personas(store))
    finally:
        undo()

    assert verdict == "retry", (
        "%s -> вердикт %r. `sent` здесь означал бы «записали в историю "
        "неотправленное», `refused` — «похоронили, возможно, доставленное»."
        % (why, verdict))
    got = _row(store, MOON)[0]
    assert got["status"] != "sent", (
        "%s -> строка закрыта как отправленная (%r)" % (why, got["status"]))
    assert got["last_error"], (
        "%s -> задание придержано БЕЗ слов в `last_error`: застрявшая строка "
        "без объяснения — ровно та тишина, ради которой очередь заводилась"
        % why)
    assert "SentRef" in str(got["last_error"]), (
        "%s -> причина придержки не называет НАРУШЕННЫЙ КОНТРАКТ, а звучит "
        "как %r. Это тот же вердикт с другим смыслом: разбор уйдёт в наш стек "
        "вместо чужого доставщика, а сам доставщик так и не узнает, что "
        "отвечает не по контракту." % (why, got["last_error"]))
    assert MOON_CHANNEL in str(got["last_error"]), (
        "%s -> причина не называет КАНАЛ (%r): в процессе с тремя каналами "
        "владелец не поймёт, чей доставщик сломан" % (why, got["last_error"]))
    assert not [m for m in store.history(MOON)
                if m["text"] == "ответ не по контракту"], (
        "%s -> неотправленное легло в ленту: владелец прочитает свой диалог с "
        "сообщением, которого собеседник не получал" % why)


# ═══ дыра 4: задания в диалог, которого в базе НЕТ ═════════════════════════

def test_zadanie_v_dialog_kotorogo_net_v_baze_OTKAZYVAET(store):
    """`get_or_create_contact` здесь нельзя: он объявил бы диалогом опечатку.

    Гейт снял `if not store.has_contact(contact_id)` и не покраснел: каждый
    стенд суиты заводит свои контакты заранее, поэтому «контакта нет» не
    случается ни в одном из них.

    Решение названо в коде: панель берёт `contact_id` из ленты, значит его
    отсутствие — это не «первый ход», а НЕСОВПАДЕНИЕ БАЗ (панель и раннер
    смотрят в разные файлы — [[jarvis-panel-db-source-is-the-live-runner]]).
    Молчаливое заведение строки превратило бы этот диагноз в «диалог есть, но
    пустой», и разбираться пришлось бы с последствием, а не с причиной.
    """
    ghost = "%s:ext-ghost:demo" % MOON_CHANNEL
    assert not store.has_contact(ghost), "предпосылка: контакта нет"

    deliverer = _Deliverer(msg_id="ghost-1")
    undo = _register(MOON_CHANNEL, deliverer)
    try:
        _enqueue(store, ghost, "кому это?", token="tok-ghost")
        row = [r for r in store.pending_outgoing() if r["contact_id"] == ghost][0]
        verdict = _deliver(store, row, now=1000.0, personas=_personas(store))
    finally:
        undo()

    assert verdict == "refused", (
        "задание в несуществующий диалог дало вердикт %r: `retry` крутил бы "
        "его вечно, `sent` объявил бы отправленным неотправленное" % (verdict,))
    assert deliverer.calls == [], (
        "доставщик всё-таки позван (%r): сообщение ушло в канал по адресу, "
        "которого нет в базе этого клиента" % (deliverer.calls,))
    assert not store.has_contact(ghost), (
        "отказ ЗАВЁЛ контакт: опечатка в id стала диалогом, и в ленте "
        "владельца появился собеседник, которого нет")
    got = _row(store, ghost)[0]
    assert got["status"] != "pending" and got["last_error"], (
        "отказ не оставил ни статуса, ни слов: %r" % (got,))
