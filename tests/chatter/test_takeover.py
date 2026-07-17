"""Различение своё/чужое исходящее. Проигранная гонка = Аня глушит сама
себя и молчит навсегда — худший отказ продукта, поэтому тестов здесь много."""
from __future__ import annotations

import asyncio

from chatter.transport.telethon_tg import SentRegistry


def test_registered_id_is_recognised_as_ours():
    r = SentRegistry()
    r.add(4821)
    assert r.is_ours(4821) is True
    assert r.is_ours(4822) is False


def test_registry_is_bounded_and_forgets_the_oldest():
    # Реестр живёт всю жизнь процесса (недели). Без границы это утечка.
    r = SentRegistry(max_size=3)
    for i in (1, 2, 3, 4):
        r.add(i)
    assert r.is_ours(1) is False      # вытеснен
    assert r.is_ours(4) is True
    assert len(r) == 3


# ---------------------------------------------------------------------------
# decide_outgoing — гонка между send() (worker-поток) и обработчиком
# исходящих (event loop). Смотри комментарий в chatter/telethon_run.py над
# decide_outgoing: гонка СТРУКТУРНА, не паранойя.
# ---------------------------------------------------------------------------
from chatter.telethon_run import decide_outgoing


def test_our_own_message_never_triggers_a_takeover():
    # АНТИРЕГРЕСС на самозаглушку. Если этот тест когда-нибудь покраснеет —
    # Аня замолчит навсегда и молча.
    r = SentRegistry()
    r.add(4821)
    assert asyncio.run(decide_outgoing(4821, registry=r, grace_seconds=0.01)) == "ours"


def test_unknown_id_becomes_a_takeover_only_after_the_grace_window():
    r = SentRegistry()
    assert asyncio.run(decide_outgoing(999, registry=r, grace_seconds=0.01)) == "human"


def test_id_registered_during_the_grace_window_is_ours_not_a_takeover():
    # ГОНКА: обработчик апдейта может обогнать возврат send_message. Голое
    # сравнение id объявило бы наше сообщение чужим и заглушило бы Аню.
    r = SentRegistry()

    async def scenario():
        task = asyncio.create_task(decide_outgoing(4821, registry=r, grace_seconds=0.2))
        await asyncio.sleep(0.05)
        r.add(4821)                 # send_message наконец вернул id
        return await task

    assert asyncio.run(scenario()) == "ours"
