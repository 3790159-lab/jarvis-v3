# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — показатель шума: доля мусорных предложений.

Гейт теневой недели — 20%. Считать его можно только по ОЦЕНЁННЫМ карточкам:
неоценённая карточка не доказана ни полезной, ни мусорной, и записывать её в
полезные значит подгонять показатель под гейт.

Отдельно закрыт самый дорогой способ соврать себе: ноль мусора из НУЛЯ
оценённых — это не «шума нет», а «мы не измеряли». Показатель в этом случае
не определён (`None`), а не 0.0.

Помеченный мусор больше не всплывает: без подавления владелец разбирал бы одну
и ту же ложную карточку каждый прогон, а показатель считал бы её заново.
"""
from __future__ import annotations

import pytest

from app.services import autonomy_proposals as ap


def _proposal(subject: str, kind: str = "register_script_without_task"):
    return ap.Proposal(kind=kind, subject=subject,
                       evidence={"subject": subject}, action_level=4,
                       proposed_action={"action": "register_scheduled_task"})


@pytest.fixture()
def conn(tmp_path):
    connection = ap.connect(tmp_path / "autonomy.db")
    yield connection
    connection.close()


def _record(conn, subject, now=1_000.0):
    ap.record(conn, _proposal(subject), now=now)
    return ap.list_shadow(conn)[-1]["id"]


def test_noise_ratio_counts_junk_among_judged(conn):
    ids = [_record(conn, name) for name in ("a", "b", "c", "d")]
    ap.judge(conn, ids[0], "junk", now=2_000.0, note="таск снят намеренно")
    for good in ids[1:]:
        ap.judge(conn, good, "useful", now=2_000.0)

    stats = ap.noise_stats(conn)

    assert stats["judged"] == 4 and stats["junk"] == 1
    assert stats["ratio"] == 0.25
    assert stats["gate_pass"] is False, "25% при гейте 20% — гейт не пройден"


def test_zero_judged_is_undefined_not_zero_noise(conn):
    """Самая дорогая ложь себе: «мусора 0 из 0» читается как «шума нет»."""
    _record(conn, "a")

    stats = ap.noise_stats(conn)

    assert stats["judged"] == 0
    assert stats["ratio"] is None
    assert stats["gate_pass"] is None, "неизмеренное не может «пройти гейт»"
    assert stats["unjudged"] == 1


def test_unjudged_cards_do_not_count_as_useful(conn):
    """Иначе показатель улучшается сам собой от каждой новой карточки."""
    ids = [_record(conn, name) for name in ("a", "b", "c")]
    ap.judge(conn, ids[0], "junk", now=2_000.0)

    stats = ap.noise_stats(conn)

    assert stats["judged"] == 1 and stats["ratio"] == 1.0
    assert stats["unjudged"] == 2


def test_junk_verdict_suppresses_the_same_observation_next_run(conn):
    """Без подавления один и тот же мусор разбирался бы каждый прогон."""
    junk_id = _record(conn, "temp")
    ap.judge(conn, junk_id, "junk", now=2_000.0)

    outcome = ap.record(conn, _proposal("temp"), now=3_000.0)

    assert outcome == "suppressed"
    assert ap.list_shadow(conn) == []


DAY = 86_400.0


def test_junk_suppression_expires_and_the_observation_returns(conn):
    """🔑 Срок жизни, а не вечность. Хеш наблюдения «сервис мёртв» ОДИНАКОВ у
    ложной тревоги и у настоящей аварии: вечный `junk` ослепил бы нас к
    реальному падению навсегда. После срока то же наблюдение обязано всплыть."""
    junk_id = _record(conn, "cloudflare-tunnel")
    ap.judge(conn, junk_id, "junk", now=2_000.0)

    within = ap.record(conn, _proposal("cloudflare-tunnel"),
                       now=2_000.0 + 6 * DAY)
    after = ap.record(conn, _proposal("cloudflare-tunnel"),
                      now=2_000.0 + 8 * DAY)

    assert within == "suppressed"
    assert after == "inserted", "через 8 дней наблюдение должно вернуться"


def test_suppression_window_ends_exactly_at_the_ttl(conn):
    """Граница определена явно: ровно на сроке наблюдение уже видно."""
    junk_id = _record(conn, "a")
    ap.judge(conn, junk_id, "junk", now=2_000.0)

    assert ap.record(conn, _proposal("a"),
                     now=2_000.0 + ap.JUNK_TTL_SEC) == "inserted"


def test_default_ttl_is_seven_days(conn):
    assert ap.JUNK_TTL_SEC == 7 * DAY


def test_ttl_is_a_parameter_not_a_hardcode(conn):
    junk_id = _record(conn, "a")
    ap.judge(conn, junk_id, "junk", now=2_000.0)

    assert ap.record(conn, _proposal("a"), now=2_000.0 + 2 * DAY,
                     junk_ttl_sec=1 * DAY) == "inserted"


def test_a_repeated_junk_verdict_restarts_the_window(conn):
    """Признал мусором снова — срок считается от ПОСЛЕДНЕГО вердикта, иначе
    повторно отвергнутый шум полез бы обратно через неделю от первого раза."""
    first = _record(conn, "a")
    ap.judge(conn, first, "junk", now=2_000.0)
    ap.record(conn, _proposal("a"), now=2_000.0 + 8 * DAY)
    second = ap.list_shadow(conn)[-1]["id"]
    ap.judge(conn, second, "junk", now=2_000.0 + 8 * DAY)

    assert ap.record(conn, _proposal("a"), now=2_000.0 + 10 * DAY) == "suppressed"


def test_useful_verdict_lets_the_hole_reappear(conn):
    """Закрытая по делу дыра, открывшаяся снова, обязана снова быть видна —
    иначе она станет невидимой навсегда."""
    good_id = _record(conn, "backup")
    ap.judge(conn, good_id, "useful", now=2_000.0)

    outcome = ap.record(conn, _proposal("backup"), now=3_000.0)

    assert outcome == "inserted"


def test_unknown_verdict_is_refused_loudly(conn):
    """DEV-18: непонятный вердикт — явная ошибка, а не тихо записанный мусор."""
    card = _record(conn, "a")

    with pytest.raises(ap.ProposalError):
        ap.judge(conn, card, "наверное полезно", now=2_000.0)


def test_judging_an_unknown_card_is_refused_loudly(conn):
    with pytest.raises(ap.ProposalError):
        ap.judge(conn, "нет-такого-id", "junk", now=2_000.0)
