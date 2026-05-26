# -*- coding: utf-8 -*-
"""Numbered custom-prompt parser for Block M.2.5 animation UX.

Turns a user's free-form numbered message (one prompt per photo) into a
``Dict[int, Optional[str]]`` mapping a 1-based photo index to either a custom
prompt string or ``None`` (meaning: use the default motion prompt for that
photo).

Pure function — no I/O, no Telegram, no orchestrator state — so it is trivially
unit-testable. The orchestrator and bot wiring layer on top of it.

Accepted line shapes (leading/trailing whitespace tolerated)::

    1. text       2) text       3: text

An empty value (``2.`` with nothing after) or the explicit ``/skip`` token maps
to ``None``. Gaps in numbering are filled with ``None`` (default) as long as
every index stays within ``1..expected_count``. A short or long *contiguous*
run (1..N where N != expected_count) is returned with ``mismatch_info`` set so
the bot can offer ``/apply_partial`` or ``/apply_first_N``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

__all__ = ["ParseResult", "PromptParseError", "parse_numbered_prompts"]


# ``1. text`` / ``1) text`` / ``1: text`` — number anchored at line start.
_LINE_RE = re.compile(r"^\s*(\d+)\s*[.):]\s*(.*)$")

# Value tokens that explicitly mean "use the default prompt for this photo".
_SKIP_TOKEN = "/skip"


class PromptParseError(ValueError):
    """Raised when a numbered-prompt message cannot be parsed."""


@dataclass
class ParseResult:
    """Outcome of parsing a numbered-prompt message.

    Attributes:
        prompts: Maps 1-based photo index → custom prompt (or ``None`` to use
            the default). For the proceed-to-confirm cases this covers exactly
            ``1..expected_count``; for a count mismatch it covers what the user
            actually provided (``1..provided``).
        mismatch_info: ``None`` when the count lines up. Otherwise a dict with
            ``kind`` (``"too_few"`` | ``"too_many"``), ``provided`` and
            ``expected`` so the bot can offer partial/truncated application.
    """

    prompts: Dict[int, Optional[str]]
    mismatch_info: Optional[dict] = None


def parse_numbered_prompts(text: str, expected_count: int) -> ParseResult:
    """Parse a numbered-prompt message against ``expected_count`` photos.

    Raises:
        PromptParseError: no numbered lines, a duplicate index, or an
            out-of-range index that is not part of a clean contiguous run.
    """
    parsed: Dict[int, Optional[str]] = {}
    for raw_line in text.splitlines():
        match = _LINE_RE.match(raw_line)
        if match is None:
            # Plain / blank / non-numbered line — ignore it.
            continue
        idx = int(match.group(1))
        if idx in parsed:
            raise PromptParseError(f"duplicate index {idx}")
        value = match.group(2).strip()
        if not value or value.lower() == _SKIP_TOKEN:
            parsed[idx] = None
        else:
            parsed[idx] = value

    if not parsed:
        raise PromptParseError("no numbered lines found")

    max_idx = max(parsed)
    is_contiguous = set(parsed) == set(range(1, max_idx + 1))

    if not is_contiguous:
        # Sparse numbering: every index must stay within range; gaps default.
        if max_idx > expected_count:
            raise PromptParseError(
                f"index {max_idx} out of range (only {expected_count} photos)"
            )
        prompts = {i: parsed.get(i) for i in range(1, expected_count + 1)}
        return ParseResult(prompts=prompts, mismatch_info=None)

    # Clean contiguous run 1..max_idx.
    if max_idx == expected_count:
        return ParseResult(prompts=dict(parsed), mismatch_info=None)

    kind = "too_few" if max_idx < expected_count else "too_many"
    return ParseResult(
        prompts=dict(parsed),
        mismatch_info={
            "kind": kind,
            "provided": max_idx,
            "expected": expected_count,
        },
    )
