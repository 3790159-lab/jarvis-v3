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


# Batches estimated to take this long (minutes) get a "долго" warning in the
# report — animation is sequential, so ~10+ photos run for hours.
_LONG_BATCH_MINUTES = 120.0


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


def estimate(
    valid_count: int,
    skipped_count: int = 0,
    *,
    swap_usd_per_photo: float | None = None,
    cold_start_usd: float | None = None,
) -> CostEstimate:
    """Compute swap + animate cost and wall-clock time for a batch.

    ``swap_usd_per_photo`` / ``cold_start_usd`` let the caller inject the active
    engine's real rates (lucataco ≈ $0.005/photo, no cold start) instead of the
    SWAPBATCH_* env defaults.
    """
    if valid_count < 0:
        raise ValueError(f"valid_count must be >= 0, got {valid_count}")
    if skipped_count < 0:
        raise ValueError(f"skipped_count must be >= 0, got {skipped_count}")

    swap_per_photo = (
        swap_usd_per_photo
        if swap_usd_per_photo is not None
        else _envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
    )
    animate_per_video = _envf("SWAPBATCH_ANIMATE_USD_PER_VIDEO", 0.27)
    cold_start_usd = (
        cold_start_usd
        if cold_start_usd is not None
        else _envf("SWAPBATCH_COLD_START_USD", 0.05)
    )
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
    est: CostEstimate,
    *,
    source_face_count: int,
    total_targets: int,
    no_face_advisory: int = 0,
) -> str:
    """Render the user-facing cost report in Russian.

    ``no_face_advisory`` (keyword-only, default 0): when > 0, append an
    advisory line noting that some photos had no detected face locally but
    will be sent to lucataco anyway — it is the final judge.
    """
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
        ]
    )
    # Animation is sequential — large batches take hours. Flag it so the user
    # isn't surprised by a multi-hour run.
    if est.total_minutes >= _LONG_BATCH_MINUTES:
        hours = est.total_minutes / 60.0
        lines.append(
            f"⏳ Это долго (~{hours:.1f} ч) — анимация идёт последовательно."
        )
    if no_face_advisory > 0:
        lines.append(
            f"ℹ️ ~{no_face_advisory} фото возможно без лица — отправлю всё равно, "
            "lucataco решит (учтены в стоимости)."
        )
    lines.extend(
        [
            "",
            "/swapbatch_go — запустить swap",
            "/swapbatch_cancel — отменить",
        ]
    )
    return "\n".join(lines)
