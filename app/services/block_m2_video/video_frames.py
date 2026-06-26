"""Slice a reference video into a representative set of frames (Веха B, Task 1).

Pure, local, free: uses OpenCV (already a dependency) to sample evenly-spaced
frames from a short reference clip. The downstream motion analysis (Веха C)
only needs a representative arc of the motion, not every frame, so we sample
``K = round(duration_sec) + 2`` frames (first + last always included). The 15s
duration limit enforced upstream is the natural cap on K (~17 frames worst
case); we deliberately set no separate max-frames ceiling.

The cv2 import is the only external boundary and is isolated behind ``_cv2()``
so tests can inject a fake without a real video file.
"""

from __future__ import annotations

import shutil
from pathlib import Path


class VideoFramesError(Exception):
    """Raised when a reference video cannot be opened, read, or sliced."""


def _cv2():
    """Lazy cv2 import, isolated so tests can monkeypatch the boundary."""
    import cv2  # type: ignore[import-not-found]

    return cv2


def _frame_count_for_duration(duration_sec: float) -> int:
    """K = round(duration) + 2 (the +2 keeps first and last frame distinct)."""
    return round(duration_sec) + 2


def _even_indices(frame_count: int, k: int) -> list[int]:
    """``k`` frame indices spread evenly across ``frame_count`` (ends included).

    If the video has fewer real frames than ``k``, every frame is returned.
    """
    if frame_count <= 0:
        return []
    if frame_count <= k:
        return list(range(frame_count))
    if k <= 1:
        return [0]
    return [round(i * (frame_count - 1) / (k - 1)) for i in range(k)]


def slice_video_to_frames(
    video_path,
    out_dir,
    *,
    jpeg_quality: int = 92,
) -> list[Path]:
    """Sample evenly-spaced frames from ``video_path`` into ``out_dir``.

    Returns the list of written frame paths (``frame_000.jpg`` …). ``out_dir``
    is wiped first so frames from a previous reference video never linger.
    Raises :class:`VideoFramesError` if the video cannot be opened or read.
    """
    cv2 = _cv2()
    video_path = Path(video_path)
    out_dir = Path(out_dir)

    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise VideoFramesError(f"could not open video: {video_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0 or frame_count <= 0:
            raise VideoFramesError(
                f"could not read video (fps={fps}, frames={frame_count}): {video_path}"
            )

        duration_sec = frame_count / fps
        k = _frame_count_for_duration(duration_sec)
        indices = _even_indices(frame_count, k)

        # Fresh slate: drop any frames left from a previous reference video.
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        for i, idx in enumerate(indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            dest = out_dir / f"frame_{i:03d}.jpg"
            cv2.imwrite(str(dest), frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            paths.append(dest)

        if not paths:
            raise VideoFramesError(f"no frames extracted from video: {video_path}")
        return paths
    finally:
        cap.release()
