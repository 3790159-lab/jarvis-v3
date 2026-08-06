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
