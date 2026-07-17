"""Различение своё/чужое исходящее. Проигранная гонка = Аня глушит сама
себя и молчит навсегда — худший отказ продукта, поэтому тестов здесь много."""
from __future__ import annotations

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
