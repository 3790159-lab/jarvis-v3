# -*- coding: utf-8 -*-
"""Tests for the numbered custom-prompt parser (Block M.2.5 animation UX).

The parser turns a user's free-form numbered message into a
``Dict[int, Optional[str]]`` mapping a 1-based photo index to either a custom
prompt or ``None`` (meaning: use the default motion prompt for that photo).
"""
from __future__ import annotations

import pytest

from app.services.block_m2_face_swap.prompt_parser import (
    ParseResult,
    PromptParseError,
    parse_numbered_prompts,
)


def test_happy_path_dot_separator():
    result = parse_numbered_prompts("1. walking\n2. dancing", expected_count=2)
    assert isinstance(result, ParseResult)
    assert result.prompts == {1: "walking", 2: "dancing"}
    assert result.mismatch_info is None


def test_paren_separator():
    result = parse_numbered_prompts("1) walking\n2) dancing", expected_count=2)
    assert result.prompts == {1: "walking", 2: "dancing"}


def test_colon_separator():
    result = parse_numbered_prompts("1: walking\n2: dancing", expected_count=2)
    assert result.prompts == {1: "walking", 2: "dancing"}


def test_mixed_separators_in_one_message():
    result = parse_numbered_prompts(
        "1. walking\n2) dancing\n3: jumping", expected_count=3
    )
    assert result.prompts == {1: "walking", 2: "dancing", 3: "jumping"}


def test_empty_prompt_idx():
    result = parse_numbered_prompts(
        "1. walking\n2. \n3. jumping", expected_count=3
    )
    assert result.prompts == {1: "walking", 2: None, 3: "jumping"}


def test_explicit_skip_token():
    result = parse_numbered_prompts(
        "1. walking\n2. /skip\n3. jumping", expected_count=3
    )
    assert result.prompts == {1: "walking", 2: None, 3: "jumping"}


def test_gap_in_numbering():
    result = parse_numbered_prompts("1. walking\n3. jumping", expected_count=3)
    assert result.prompts == {1: "walking", 2: None, 3: "jumping"}
    assert result.mismatch_info is None


def test_out_of_range_raises():
    with pytest.raises(PromptParseError):
        parse_numbered_prompts("1. walking\n5. jumping", expected_count=3)


def test_duplicate_idx_raises():
    with pytest.raises(PromptParseError):
        parse_numbered_prompts("1. walking\n1. other", expected_count=2)


def test_no_numbered_lines_raises():
    with pytest.raises(PromptParseError):
        parse_numbered_prompts(
            "just plain text without numbers", expected_count=3
        )


def test_too_few_prompts_mismatch_info():
    result = parse_numbered_prompts("1. a\n2. b\n3. c", expected_count=5)
    assert result.mismatch_info is not None
    assert result.mismatch_info["kind"] == "too_few"
    assert result.mismatch_info["provided"] == 3
    assert result.mismatch_info["expected"] == 5


def test_too_many_prompts_mismatch_info():
    result = parse_numbered_prompts(
        "1. a\n2. b\n3. c\n4. d\n5. e\n6. f\n7. g", expected_count=5
    )
    assert result.mismatch_info is not None
    assert result.mismatch_info["kind"] == "too_many"
    assert result.mismatch_info["provided"] == 7
    assert result.mismatch_info["expected"] == 5


def test_whitespace_tolerance():
    text = "  1. walking \n\n 2.   dancing  \n"
    result = parse_numbered_prompts(text, expected_count=2)
    assert result.prompts == {1: "walking", 2: "dancing"}


def test_multi_word_prompts():
    text = "1. идёт по осеннему парку, листья падают"
    result = parse_numbered_prompts(text, expected_count=1)
    assert result.prompts == {1: "идёт по осеннему парку, листья падают"}
