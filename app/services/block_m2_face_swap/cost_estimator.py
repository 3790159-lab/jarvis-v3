# -*- coding: utf-8 -*-
"""Cost & time estimator for Block M.2.5 face-swap batches.

All unit costs are env-overridable so we can recalibrate after the first
real run without touching code:

- ``SWAPBATCH_SWAP_USD_PER_PHOTO`` (default ``0.02``)
- ``SWAPBATCH_ANIMATE_USD_PER_VIDEO`` (default ``0.27``)
- ``SWAPBATCH_COLD_START_USD`` (default ``0.05``)
- ``SWAPBATCH_SWAP_SEC_PER_PHOTO`` (default ``15.0``)
- ``SWAPBATCH_ANIMATE_SEC_PER_VIDEO`` (default ``720.0``)
- ``SWAPBATCH_COLD_START_SEC`` (default ``120.0``)

Reading happens at every :func:`estimate` call so tests can monkey-patch
the env between calls.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _envf(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class CostEstimate:
    """Result of :func:`estimate`. All durations in minutes, all costs in USD."""

    valid_count: int
    skipped_count: int
    swap_usd: float
    animate_usd: float
    total_usd: float
    swap_minutes: float
    animate_minutes: float
    total_minutes: float


def estimate(valid_count: int, skipped_count: int = 0) -> CostEstimate:
    """Compute swap + animate cost and wall-clock time for a batch."""
    if valid_count < 0:
        raise ValueError(f"valid_count must be >= 0, got {valid_count}")
    if skipped_count < 0:
        raise ValueError(f"skipped_count must be >= 0, got {skipped_count}")

    swap_per_photo = _envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
    animate_per_video = _envf("SWAPBATCH_ANIMATE_USD_PER_VIDEO", 0.27)
    cold_start_usd = _envf("SWAPBATCH_COLD_START_USD", 0.05)
    swap_sec_per_photo = _envf("SWAPBATCH_SWAP_SEC_PER_PHOTO", 15.0)
    animate_sec_per_video = _envf("SWAPBATCH_ANIMATE_SEC_PER_VIDEO", 720.0)
    cold_start_sec = _envf("SWAPBATCH_COLD_START_SEC", 120.0)

    if valid_count == 0:
        return CostEstimate(
            valid_count=0,
            skipped_count=skipped_count,
            swap_usd=0.0,
            animate_usd=0.0,
            total_usd=0.0,
            swap_minutes=0.0,
            animate_minutes=0.0,
            total_minutes=0.0,
        )

    swap_usd = valid_count * swap_per_photo + cold_start_usd
    animate_usd = valid_count * animate_per_video + cold_start_usd
    swap_minutes = (cold_start_sec + valid_count * swap_sec_per_photo) / 60.0
    animate_minutes = (
        cold_start_sec + valid_count * animate_sec_per_video
    ) / 60.0

    return CostEstimate(
        valid_count=valid_count,
        skipped_count=skipped_count,
        swap_usd=round(swap_usd, 2),
        animate_usd=round(animate_usd, 2),
        total_usd=round(swap_usd + animate_usd, 2),
        swap_minutes=round(swap_minutes, 1),
        animate_minutes=round(animate_minutes, 1),
        total_minutes=round(swap_minutes + animate_minutes, 1),
    )


def format_cost_report_ru(
    est: CostEstimate, *, source_face_count: int, total_targets: int
) -> str:
    """Render the user-facing cost report in Russian."""
    lines = [
        "📊 Оценка батча",
        f"Source: {source_face_count} лицо ✅"
        if source_face_count == 1
        else f"Source: {source_face_count} лиц",
        f"Targets: {total_targets} фото",
        f"  ✅ {est.valid_count} с лицами",
    ]
    if est.skipped_count:
        lines.append(f"  ⚠️ {est.skipped_count} без лиц (будут пропущены)")
    lines.append("")
    if est.valid_count == 0:
        lines.append("Нет валидных фото — батч не может быть запущен.")
        lines.append("/swapbatch_cancel — сбросить")
        return "\n".join(lines)

    lines.extend(
        [
            f"Swap: ${est.swap_usd:.2f} ({est.valid_count} × фото + старт)",
            f"Animate: ${est.animate_usd:.2f} ({est.valid_count} × видео + старт)",
            f"Всего: ~${est.total_usd:.2f}",
            f"Время: ~{est.total_minutes:.0f} мин (swap ~{est.swap_minutes:.0f} мин + animate ~{est.animate_minutes:.0f} мин)",
            "",
            "/swapbatch_go — запустить swap",
            "/swapbatch_cancel — отменить",
        ]
    )
    return "\n".join(lines)
