# -*- coding: utf-8 -*-
"""parse_dt timezone handling. $0, no network.

Regression: parse_dt on a NAIVE datetime called an undefined global get_zone(tz)
→ NameError (found by the menu-audit LOAD_GLOBAL scan; same class as the
/browse_check 'os' bug). It must use ZoneInfo(tz), like now_local().
"""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services import time_brain as tb


def test_parse_dt_naive_datetime_uses_tz_and_returns_utc():
    got = tb.parse_dt("2026-04-27T21:30:00", tz="Europe/Kyiv")
    expected = datetime(2026, 4, 27, 21, 30, tzinfo=ZoneInfo("Europe/Kyiv")).astimezone(UTC)
    assert got == expected                 # naive time interpreted in tz, then UTC
    assert got.tzinfo == UTC


def test_parse_dt_aware_datetime_preserved():
    # already-aware input keeps its offset (does not touch the tz branch)
    got = tb.parse_dt("2026-04-27T21:30:00+03:00", tz="Europe/Kyiv")
    assert got == datetime(2026, 4, 27, 18, 30, tzinfo=UTC)
