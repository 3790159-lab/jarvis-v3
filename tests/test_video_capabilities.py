import pytest

from app.services.block_m2_video.engines.capabilities import (
    caps_for, WAVESPEED_CAPS, SEEDANCE_CAPS,
)


def test_wavespeed_caps_shape():
    c = WAVESPEED_CAPS
    assert c.engine_mode == "spicy"
    assert c.allowed_durations == (5, 10, 15)
    assert c.allowed_resolutions == ("720p", "1080p")
    assert c.native_fps == 30
    assert c.censored is False


def test_seedance_caps_shape():
    c = SEEDANCE_CAPS
    assert c.engine_mode == "seedance"
    assert c.allowed_durations == (5, 10)
    assert c.allowed_resolutions == ("480p", "720p", "1080p")
    assert c.native_fps == 24
    assert c.censored is True


def test_snap_duration_and_resolution():
    assert WAVESPEED_CAPS.snap_duration(7) == 5
    assert WAVESPEED_CAPS.snap_duration(13) == 15
    assert SEEDANCE_CAPS.snap_duration(15) == 10           # seedance has no 15
    assert SEEDANCE_CAPS.snap_resolution("1080p") == "1080p"
    assert WAVESPEED_CAPS.snap_resolution("480p") == "720p"  # WS has no 480; -> default


def test_cost_for_tables():
    assert WAVESPEED_CAPS.cost_for(15, "720p") == pytest.approx(1.50)
    assert WAVESPEED_CAPS.cost_for(5, "1080p") == pytest.approx(0.75)
    assert SEEDANCE_CAPS.cost_for(10, "1080p") == pytest.approx(0.55)
    assert SEEDANCE_CAPS.cost_for(5, "720p") == pytest.approx(0.11)


def test_caps_for_mode():
    assert caps_for("spicy") is WAVESPEED_CAPS
    assert caps_for("seedance") is SEEDANCE_CAPS
    with pytest.raises(KeyError):
        caps_for("nope")
