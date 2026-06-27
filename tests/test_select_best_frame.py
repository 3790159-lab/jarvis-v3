# -*- coding: utf-8 -*-
"""Tests for ``select_best_frame`` (Веха C / Задача 2).

Picks the frame whose largest face scores highest (composite) via the local
FaceValidator. $0 / 0 vision — the validator is mocked here. ``None`` when no
frame has a usable face acts as the money-gate: Веха C stops before any paid
Grok call.
"""
from __future__ import annotations

import types
from pathlib import Path

from app.services.block_m2_video.video_frames import select_best_frame


class _FakeValidator:
    """Maps frame path -> composite score (or None = no face)."""

    def __init__(self, scores: dict[Path, float | None]) -> None:
        self._scores = scores

    def score_largest_face(self, path: Path):
        composite = self._scores.get(path)
        if composite is None:
            return None
        return types.SimpleNamespace(composite=composite)


def _frames(n: int) -> list[Path]:
    return [Path(f"frame_{i:03d}.jpg") for i in range(n)]


def test_picks_frame_with_max_composite():
    f0, f1, f2 = _frames(3)
    v = _FakeValidator({f0: 0.40, f1: 0.85, f2: 0.55})
    assert select_best_frame([f0, f1, f2], v) == f1


def test_all_none_returns_none():
    f0, f1, f2 = _frames(3)
    v = _FakeValidator({f0: None, f1: None, f2: None})
    assert select_best_frame([f0, f1, f2], v) is None


def test_empty_list_returns_none():
    v = _FakeValidator({})
    assert select_best_frame([], v) is None


def test_ties_pick_first_for_determinism():
    f0, f1, f2 = _frames(3)
    v = _FakeValidator({f0: 0.70, f1: 0.70, f2: 0.70})
    assert select_best_frame([f0, f1, f2], v) == f0


def test_single_face_among_none_is_chosen():
    f0, f1, f2 = _frames(3)
    v = _FakeValidator({f0: None, f1: 0.33, f2: None})
    assert select_best_frame([f0, f1, f2], v) == f1
