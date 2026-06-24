# -*- coding: utf-8 -*-
"""TDD: surface per-frame terminal animate failures (E005 censorship etc.) into
user-facing text. Pure helpers — classify a raw engine reason into a category,
then render friend-friendly (grouped, no raw codes) vs admin-detailed (per-index,
raw kept) lines.
"""
from app.services.block_m2_video.animate_failures import (
    classify_failure,
    friendly_failure_lines,
    detailed_failure_lines,
)

_E005 = ("prediction failed: The input or output was flagged as sensitive. "
         "Please try again with different inputs. (E005) (uIJ6l3ruRD)")
_TIMEOUT = "poll timed out after 600s"
_429 = "Client error '429 Too Many Requests' for url 'https://api.replicate.com'"
_GENERIC = "download failed: connection reset"


# ── classify_failure ────────────────────────────────────────────────────────

def test_classify_censored_from_e005_code():
    assert classify_failure(_E005) == "censored"


def test_classify_censored_from_sensitive_text_without_code():
    assert classify_failure("The input or output was flagged as sensitive.") == "censored"


def test_classify_timeout():
    assert classify_failure(_TIMEOUT) == "timeout"


def test_classify_overloaded_429():
    assert classify_failure(_429) == "overloaded"


def test_classify_generic_engine_error():
    assert classify_failure(_GENERIC) == "engine_error"


def test_classify_empty_is_engine_error():
    assert classify_failure("") == "engine_error"


# ── friendly_failure_lines (friend) ──────────────────────────────────────────

def test_friendly_groups_by_category_with_counts():
    errs = {0: _E005, 2: _E005, 3: _TIMEOUT}
    joined = "\n".join(friendly_failure_lines(errs))
    assert "2" in joined and "цензур" in joined.lower()   # 2 censored, named
    assert "не успел" in joined.lower() or "таймаут" in joined.lower()


def test_friendly_never_leaks_raw_codes():
    joined = "\n".join(friendly_failure_lines({0: _E005}))
    assert "E005" not in joined
    assert "uIJ6" not in joined


def test_friendly_censored_suggests_alternative():
    joined = "\n".join(friendly_failure_lines({0: _E005})).lower()
    # actionable hint so the friend knows what to do, not just "failed"
    assert "wavespeed" in joined or "другое фото" in joined


def test_friendly_empty_when_no_errors():
    assert friendly_failure_lines({}) == []


# ── detailed_failure_lines (admin / me) ──────────────────────────────────────

def test_detailed_lists_each_index_1based_with_raw_reason():
    errs = {0: _E005, 3: _TIMEOUT}
    joined = "\n".join(detailed_failure_lines(errs))
    assert "#1" in joined and "#4" in joined     # 1-based positions
    assert "E005" in joined                       # raw kept for admin diagnosis
    assert "600s" in joined


def test_detailed_sorted_by_index():
    errs = {3: _TIMEOUT, 0: _E005, 1: _429}
    lines = detailed_failure_lines(errs)
    order = [ln for ln in lines if ln.strip().startswith("•")]
    assert order[0].find("#1") != -1
    assert order[1].find("#2") != -1
    assert order[2].find("#4") != -1


def test_detailed_empty_when_no_errors():
    assert detailed_failure_lines({}) == []
