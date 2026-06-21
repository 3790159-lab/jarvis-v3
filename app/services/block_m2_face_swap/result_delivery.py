# -*- coding: utf-8 -*-
"""Pure helpers for delivering batch swap results to Telegram.

``chunk_photos`` groups paths into Telegram media-group-sized batches (10).
``build_result_zips`` packs results into one or more zips, each kept under
``max_bytes`` so it stays below Telegram's ~50 MB sendDocument cap. Stored JPEGs
barely compress, so we size parts by raw input bytes (a safe over-estimate).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# Telegram bot sendDocument hard cap is 50 MB; leave headroom for zip overhead.
DEFAULT_ZIP_MAX_BYTES = 45 * 1024 * 1024
MEDIA_GROUP_SIZE = 10


def chunk_photos(paths: list[Path], size: int = MEDIA_GROUP_SIZE) -> list[list[Path]]:
    return [paths[i:i + size] for i in range(0, len(paths), size)]


def build_result_zips(
    paths: list[Path],
    out_dir: Path,
    max_bytes: int = DEFAULT_ZIP_MAX_BYTES,
) -> list[Path]:
    """Pack ``paths`` into size-bounded zip parts under ``out_dir``.

    Returns the list of created zip Paths (named ``results_1ofN.zip`` …). A
    single oversized file still goes into its own part (never silently dropped).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in paths if p.exists()]
    # First pass: assign files to parts by cumulative raw size.
    parts: list[list[Path]] = [[]]
    running = 0
    for p in existing:
        size = p.stat().st_size
        if parts[-1] and running + size > max_bytes:
            parts.append([])
            running = 0
        parts[-1].append(p)
        running += size
    if not parts[0]:
        return []
    total = len(parts)
    zips: list[Path] = []
    for i, part in enumerate(parts, start=1):
        zpath = out_dir / f"results_{i}of{total}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
            for p in part:
                zf.write(p, arcname=p.name)
        zips.append(zpath)
    return zips
