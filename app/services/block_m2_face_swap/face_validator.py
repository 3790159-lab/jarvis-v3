# -*- coding: utf-8 -*-
"""Local face validator for Block M.2.5 face-swap batch pipeline.

Runs entirely on the bot host (not on RunPod) so we can reject source/target
photos with no detectable face before spending any pod money.

Backend selection at first call:

1. **InsightFace** (preferred) — ``buffalo_l`` model (insightface>=1.0.1),
   auto-downloads ~290MB to ``~/.insightface/models`` on first use. Robust,
   returns face boxes and landmarks. Runs on CPU (``ctx_id=-1``).
2. **OpenCV Haar cascade** (fallback) — ships with ``opencv-python``; weaker
   (higher false-positive rate) but no extra deps. Logged when used so we
   know quality is reduced.

The active backend is recorded in module-level :data:`VALIDATOR_BACKEND`
after the first detection call. Public accessor:
:func:`get_active_backend` — triggers the lazy load if needed and returns
the selected backend name (``"insightface"``, ``"opencv"``, or ``"none"``).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


BACKEND_INSIGHTFACE = "insightface"
BACKEND_OPENCV = "opencv"
BACKEND_NONE = "none"

# ── frame-quality scoring tunables (Веха C / Задача 1) ───────────────────────
# Stop emitting magic numbers inline — all knobs live here for live tuning.
TARGET_FACE_FRACTION = 0.10  # bbox occupying ≥10% of the frame = full area score
MAX_OFFSET_RATIO = 0.5       # nose-vs-eye-centre offset (÷ eye distance) at frontality 0
W_DET = 0.40                 # detector confidence (sharpness / not-blurry)
W_FRONT = 0.35               # frontality (critical for swap quality)
W_AREA = 0.25                # relative face size

# Module-level record of which backend is in use. Updated by
# ``FaceValidator._ensure_loaded()`` and observable via
# :func:`get_active_backend`. Don't read this directly from external code —
# call the getter so the lazy load is forced first.
VALIDATOR_BACKEND: str = BACKEND_NONE

__all__ = [
    "FaceValidator",
    "FaceScore",
    "VALIDATOR_BACKEND",
    "BACKEND_INSIGHTFACE",
    "BACKEND_OPENCV",
    "BACKEND_NONE",
    "get_active_backend",
]


@dataclass(frozen=True)
class FaceScore:
    """Quality score for the largest detected face in one frame.

    ``composite`` (0..1) is the single number callers compare across frames
    (Веха C / Задача 2 ``select_best_frame``). The remaining fields expose the
    raw signals for debugging / logging.
    """

    composite: float        # 0..1 final score
    det_score: float        # raw detector confidence 0..1 (0.0 if backend lacks it)
    area_fraction: float    # bbox area / image area, 0..1
    frontality: float       # 0..1, 1.0 = perfectly frontal (0.0 if unavailable)
    bbox: tuple[int, int, int, int]
    backend: str            # "insightface" | "opencv" — surfaces degradation


@dataclass(frozen=True)
class _ScoredDetection:
    """Internal: one detection enriched with the signals SCRFD already gives."""

    bbox: tuple[int, int, int, int]
    det_score: float | None  # None when the backend (opencv) provides no score
    kps: Any | None          # 5 landmarks or None (opencv)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def get_active_backend() -> str:
    """Return the currently active validator backend name.

    Forces lazy initialisation if no FaceValidator has been used yet,
    so the answer is always meaningful (not ``"none"`` just because the
    library hasn't been exercised). Idempotent and safe to call repeatedly.
    """
    if VALIDATOR_BACKEND == BACKEND_NONE:
        # Trigger the same lazy-load path FaceValidator uses; we do not
        # need to actually detect on any image — _ensure_loaded() sets the
        # module variable as a side effect.
        FaceValidator()._ensure_loaded()
    return VALIDATOR_BACKEND


class FaceValidator:
    """Detect faces in local images. Backend is chosen lazily on first use."""

    def __init__(self, *, prefer_insightface: bool = True) -> None:
        self._prefer_insightface = prefer_insightface
        self._lock = threading.Lock()
        self._insightface_app: Any = None
        self._opencv_cascade: Any = None
        self._loaded = False

    # ── public API ──────────────────────────────────────────────────────────

    def has_face(self, image_path: Path) -> bool:
        return self.count_faces(image_path) > 0

    def count_faces(self, image_path: Path) -> int:
        faces = self._detect(image_path)
        return len(faces)

    def get_largest_face_bbox(
        self, image_path: Path
    ) -> tuple[int, int, int, int] | None:
        faces = self._detect(image_path)
        if not faces:
            return None
        # bbox format: (x1, y1, x2, y2). Largest by area.
        return max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    def score_largest_face(self, image_path: Path) -> "FaceScore | None":
        """Score the largest detected face for swap suitability.

        Returns ``None`` when no face is detected (consistent with
        :meth:`get_largest_face_bbox`). Uses the dedicated ``_detect_scored``
        path so the existing detection methods stay untouched. $0 / local.
        """
        dets, (img_h, img_w), backend = self._detect_scored(Path(image_path))
        if not dets:
            return None
        largest = max(
            dets, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1])
        )
        return self._compute_score(largest, img_w, img_h, backend)

    # ── internals ───────────────────────────────────────────────────────────

    def _detect_scored(
        self, image_path: Path
    ) -> tuple[list["_ScoredDetection"], tuple[int, int], str]:
        """Detect faces keeping det_score + kps. Returns (dets, (h, w), backend)."""
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"image not found: {path}")
        self._ensure_loaded()

        import cv2  # type: ignore[import-not-found]

        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"cv2 could not read image: {path}")
        img_h, img_w = img.shape[:2]

        dets: list[_ScoredDetection] = []
        if self._insightface_app is not None:
            for face in self._insightface_app.get(img):
                bbox = getattr(face, "bbox", None)
                if bbox is None or len(bbox) < 4:
                    continue
                x1, y1, x2, y2 = (int(v) for v in bbox[:4])
                det_score = getattr(face, "det_score", None)
                dets.append(
                    _ScoredDetection(
                        bbox=(x1, y1, x2, y2),
                        det_score=None if det_score is None else float(det_score),
                        kps=getattr(face, "kps", None),
                    )
                )
            return dets, (img_h, img_w), BACKEND_INSIGHTFACE

        if self._opencv_cascade is not None:
            for bbox in self._detect_opencv(path):
                dets.append(_ScoredDetection(bbox=bbox, det_score=None, kps=None))
            return dets, (img_h, img_w), BACKEND_OPENCV

        return dets, (img_h, img_w), BACKEND_NONE

    def _compute_score(
        self,
        det: "_ScoredDetection",
        img_w: int,
        img_h: int,
        backend: str,
    ) -> "FaceScore":
        x1, y1, x2, y2 = det.bbox
        bbox_area = max(0, x2 - x1) * max(0, y2 - y1)
        img_area = max(1, img_w * img_h)
        area_fraction = bbox_area / img_area
        area_norm = min(1.0, area_fraction / TARGET_FACE_FRACTION)

        # Weighted signals, renormalised over only those actually available.
        signals: list[tuple[float, float]] = [(W_AREA, area_norm)]
        det_score = 0.0
        frontality = 0.0

        if backend == BACKEND_INSIGHTFACE:
            if det.det_score is not None:
                det_score = _clamp01(det.det_score)
                signals.append((W_DET, det_score))
            front = self._frontality(det.kps)
            if front is not None:
                frontality = front
                signals.append((W_FRONT, frontality))

        total_w = sum(w for w, _ in signals)
        composite = (
            sum(w * v for w, v in signals) / total_w if total_w > 0 else 0.0
        )
        return FaceScore(
            composite=_clamp01(composite),
            det_score=det_score,
            area_fraction=area_fraction,
            frontality=frontality,
            bbox=det.bbox,
            backend=backend,
        )

    @staticmethod
    def _frontality(kps: Any | None) -> float | None:
        """Frontality from 5 kps via nose-vs-eye-centre symmetry.

        Returns ``None`` (signal unavailable) for missing/degenerate kps so the
        caller can renormalise instead of dividing by a near-zero eye distance.
        """
        if kps is None:
            return None
        try:
            if len(kps) < 3:
                return None
            left_x = float(kps[0][0])
            right_x = float(kps[1][0])
            nose_x = float(kps[2][0])
        except (TypeError, IndexError, ValueError):
            return None
        eye_dist = abs(right_x - left_x)
        if eye_dist < 1.0:
            return None
        eye_center_x = (left_x + right_x) / 2.0
        ratio = abs(nose_x - eye_center_x) / eye_dist
        return max(0.0, 1.0 - ratio / MAX_OFFSET_RATIO)

    def _ensure_loaded(self) -> None:
        global VALIDATOR_BACKEND
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self._prefer_insightface:
                try:
                    import insightface  # type: ignore[import-not-found]

                    # insightface>=1.0.1 supports ``allowed_modules`` natively
                    # and auto-downloads the buffalo_l pack on first prepare().
                    # We load only the detection model (the validator never needs
                    # recognition/landmarks) to keep memory and latency down. The
                    # old 0.2.x pin lacked this kwarg AND model auto-download,
                    # which is why the validator silently fell back to Haar.
                    app = insightface.app.FaceAnalysis(
                        name="buffalo_l",
                        allowed_modules=["detection"],
                    )
                    app.prepare(ctx_id=-1, det_size=(640, 640))  # ctx_id=-1 → CPU
                    self._insightface_app = app
                    VALIDATOR_BACKEND = BACKEND_INSIGHTFACE
                    logger.info("FaceValidator: using InsightFace (buffalo_l)")
                    self._loaded = True
                    return
                except Exception as exc:  # noqa: BLE001 - fallback intentional
                    logger.warning(
                        "FaceValidator: InsightFace unavailable (%s); "
                        "falling back to OpenCV Haar cascade",
                        exc,
                    )

            try:
                import cv2  # type: ignore[import-not-found]

                cascade_path = (
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                )
                cascade = cv2.CascadeClassifier(cascade_path)
                if cascade.empty():
                    raise RuntimeError(
                        f"OpenCV cascade file unreadable at {cascade_path}"
                    )
                self._opencv_cascade = cascade
                VALIDATOR_BACKEND = BACKEND_OPENCV
                logger.info("FaceValidator: using OpenCV Haar cascade")
            except Exception as exc:  # noqa: BLE001 - last-resort backend
                logger.error(
                    "FaceValidator: no backend available — every photo will "
                    "report 0 faces (%s)",
                    exc,
                )
                VALIDATOR_BACKEND = BACKEND_NONE

            self._loaded = True

    def _detect(self, image_path: Path) -> list[tuple[int, int, int, int]]:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"image not found: {path}")
        self._ensure_loaded()

        if self._insightface_app is not None:
            return self._detect_insightface(path)
        if self._opencv_cascade is not None:
            return self._detect_opencv(path)
        return []

    def _detect_insightface(
        self, path: Path
    ) -> list[tuple[int, int, int, int]]:
        import cv2  # type: ignore[import-not-found]

        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"cv2 could not read image: {path}")
        faces = self._insightface_app.get(img)
        out: list[tuple[int, int, int, int]] = []
        for face in faces:
            bbox = getattr(face, "bbox", None)
            if bbox is None or len(bbox) < 4:
                continue
            x1, y1, x2, y2 = (int(v) for v in bbox[:4])
            out.append((x1, y1, x2, y2))
        return out

    def _detect_opencv(self, path: Path) -> list[tuple[int, int, int, int]]:
        import cv2  # type: ignore[import-not-found]

        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"cv2 could not read image: {path}")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # minNeighbors=3 (loosened from 5) — Haar cascade is conservative;
        # 5 was rejecting clear casual frontal photos in production. 3 still
        # rejects most spurious matches but accepts everyday selfies.
        rects = self._opencv_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=3, minSize=(40, 40)
        )
        return [(int(x), int(y), int(x + w), int(y + h)) for (x, y, w, h) in rects]
