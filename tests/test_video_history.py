# -*- coding: utf-8 -*-
"""Tests for ``engines/history.py``."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.block_m2_video.engines import history as history_mod
from app.services.block_m2_video.engines.engine_protocol import (
    VideoRequest,
    VideoResult,
)


def _make_request(tmp_path: Path, *, persona_id: str = "persona_a") -> VideoRequest:
    img = tmp_path / "src_input.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return VideoRequest(
        persona_id=persona_id,
        persona_name="Test",
        input_image_path=img,
        prompt="hello prompt",
        seconds=5,
        seed=42,
    )


def _make_result(
    tmp_path: Path,
    request: VideoRequest,
    *,
    generation_id: str = "gen_abcdef00",
) -> VideoResult:
    out_dir = (
        tmp_path / "state" / "personas" / "videos"
        / request.persona_id / generation_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "output.mp4"
    output.write_bytes(b"MP4DATA")
    return VideoResult(
        generation_id=generation_id,
        persona_id=request.persona_id,
        output_path=output,
        engine="replicate",
        model="wan-video/wan-2.5-i2v-fast",
        seed=request.seed or 0,
        cost_usd=0.10,
        duration_sec=12.3,
        timestamp=datetime(2026, 5, 11, tzinfo=timezone.utc),
        prompt=request.prompt,
        seconds=request.seconds,
        extra={"video_url": "https://example/x.mp4"},
    )


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# ── save_result ──────────────────────────────────────────────────────────────


def test_save_result_writes_all_artifacts(tmp_path):
    request = _make_request(tmp_path)
    result = _make_result(tmp_path, request)

    history_mod.save_result(result, request)

    out_dir = result.output_path.parent
    assert (out_dir / "input.png").exists()
    assert (out_dir / "input.png").read_bytes() == b"\x89PNG\r\n\x1a\nFAKE"
    assert (out_dir / "prompt.txt").read_text(encoding="utf-8") == "hello prompt"

    meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["generation_id"] == "gen_abcdef00"
    assert meta["persona_id"] == "persona_a"
    assert meta["engine"] == "replicate"
    assert meta["seed"] == 42
    assert meta["cost_usd"] == 0.10
    assert meta["seconds"] == 5
    assert meta["extra"]["video_url"] == "https://example/x.mp4"


def test_save_result_appends_expense_line(tmp_path):
    request = _make_request(tmp_path)
    result = _make_result(tmp_path, request)

    history_mod.save_result(result, request)

    expenses = Path("state/expenses/persona_video.jsonl")
    assert expenses.exists()
    lines = expenses.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["persona"] == "persona_a"
    assert entry["generation_id"] == "gen_abcdef00"
    assert entry["cost_usd"] == 0.10


# ── get_last_generation ─────────────────────────────────────────────────────


def test_get_last_generation_returns_newest_by_mtime(tmp_path):
    request = _make_request(tmp_path, persona_id="persona_b")
    older = _make_result(tmp_path, request, generation_id="gen_old00000")
    history_mod.save_result(older, request)
    time.sleep(0.05)  # ensure distinct mtime
    newer_request = _make_request(tmp_path, persona_id="persona_b")
    newer_request.prompt = "newer"
    newer = _make_result(tmp_path, newer_request, generation_id="gen_new00000")
    history_mod.save_result(newer, newer_request)

    last = history_mod.get_last_generation("persona_b")

    assert last is not None
    assert last["generation_id"] == "gen_new00000"
    assert last["prompt"] == "newer"


def test_get_last_generation_returns_none_when_empty(tmp_path):
    assert history_mod.get_last_generation("persona_nonexistent") is None


# ── list_generations ─────────────────────────────────────────────────────────


def test_list_generations_respects_limit(tmp_path):
    for i in range(5):
        request = _make_request(tmp_path, persona_id="persona_c")
        result = _make_result(
            tmp_path, request, generation_id=f"gen_x{i:07d}"
        )
        history_mod.save_result(result, request)
        time.sleep(0.02)

    listed = history_mod.list_generations("persona_c", limit=3)

    assert len(listed) == 3
    # newest first
    assert listed[0]["generation_id"] == "gen_x0000004"
    assert listed[2]["generation_id"] == "gen_x0000002"


def test_metadata_round_trips_through_json(tmp_path):
    request = _make_request(tmp_path)
    result = _make_result(tmp_path, request)
    history_mod.save_result(result, request)

    meta = json.loads(
        (result.output_path.parent / "metadata.json").read_text(encoding="utf-8")
    )
    # Should be JSON-serializable again
    re_dumped = json.dumps(meta)
    re_loaded = json.loads(re_dumped)
    assert re_loaded == meta
