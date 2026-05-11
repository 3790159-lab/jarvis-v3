# -*- coding: utf-8 -*-
"""Per-persona history storage and redo support for Phase A videos."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .engine_protocol import VideoRequest, VideoResult

logger = logging.getLogger(__name__)

HISTORY_ROOT = Path("state/personas/videos")
EXPENSES_PATH = Path("state/expenses/persona_video.jsonl")


def save_result(result: VideoResult, request: VideoRequest) -> None:
    """Persist generation artifacts and metadata for ``result``.

    Side effects:

    1. ``output_path.parent/`` is created.
    2. ``input.png`` is copied alongside the output if not already there.
    3. ``prompt.txt`` and ``metadata.json`` are written.
    4. A line is appended to ``state/expenses/persona_video.jsonl``.
    """
    out_dir = result.output_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    input_copy = out_dir / "input.png"
    if not input_copy.exists() and request.input_image_path.exists():
        input_copy.write_bytes(request.input_image_path.read_bytes())

    (out_dir / "prompt.txt").write_text(request.prompt, encoding="utf-8")

    meta = {
        "generation_id": result.generation_id,
        "persona_id": result.persona_id,
        "engine": result.engine,
        "model": result.model,
        "seed": result.seed,
        "cost_usd": result.cost_usd,
        "duration_sec": result.duration_sec,
        "timestamp": result.timestamp.isoformat(),
        "prompt": result.prompt,
        "seconds": result.seconds,
        "extra": result.extra,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    EXPENSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXPENSES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": result.timestamp.isoformat(),
                    "persona": result.persona_id,
                    "generation_id": result.generation_id,
                    "engine": result.engine,
                    "model": result.model,
                    "cost_usd": result.cost_usd,
                    "seconds": result.seconds,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    logger.info("Saved generation %s to %s", result.generation_id, out_dir)


def get_last_generation(persona_id: str) -> dict | None:
    """Return the most recent generation metadata for ``persona_id`` or ``None``.

    Falls through to older directories if the newest one lacks
    ``metadata.json`` — this handles concurrent writes (a fresh gen
    directory exists but ``save_result`` has not yet written metadata).
    """
    persona_dir = HISTORY_ROOT / persona_id
    if not persona_dir.exists():
        return None
    gens = sorted(
        persona_dir.glob("gen_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for gen in gens:
        meta_file = gen / "metadata.json"
        if meta_file.exists():
            return json.loads(meta_file.read_text(encoding="utf-8"))
    return None


def list_generations(persona_id: str, limit: int = 20) -> list[dict]:
    """Return up to ``limit`` generations for ``persona_id``, newest first."""
    persona_dir = HISTORY_ROOT / persona_id
    if not persona_dir.exists():
        return []
    gens = sorted(
        persona_dir.glob("gen_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    results: list[dict] = []
    for gen in gens:
        meta_file = gen / "metadata.json"
        if meta_file.exists():
            results.append(json.loads(meta_file.read_text(encoding="utf-8")))
    return results
