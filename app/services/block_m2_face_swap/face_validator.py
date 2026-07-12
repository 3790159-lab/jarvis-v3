# -*- coding: utf-8 -*-
"""Local face validator for Block M.2.5 face-swap batch pipeline.

Runs entirely on the bot host (not on RunPod) so we can reject source/target
photos with no detectable face before spending any pod money.

Backend selection at first call, in order:

1. **InsightFace** (preferred) — ``buffalo_l`` model (insightface>=1.0.1),
   auto-downloads ~290MB to ``~/.insightface/models`` on first use. Robust,
   returns face boxes and landmarks. Runs on CPU (``ctx_id=-1``).
2. **OpenCV Haar cascade** (fallback) — ships with ``opencv-python``; weaker
   (higher false-positive rate) but no extra deps.
3. **MediaPipe** (last resort) — ``blaze_face_short_range`` model, downloaded
   (~230KB) to ``~/.mediapipe_models`` on first use. Reads images via Pillow
   instead of cv2, so it still works when cv2 itself is the thing that's
   broken. Does not depend on cv2 at all.

Each fallback transition is logged at WARNING so a degraded backend is
never silent. If **all three** backends fail to load, the validator does
NOT pretend photos have 0 faces (that silently rejected every real photo
for weeks in production without anyone noticing — see the 2026-07 incident).
Instead every detection call raises :class:`FaceValidatorUnavailableError`
so callers can surface an honest "validation unavailable" error instead of
a fake "no face detected" / fake pass.

The active backend is recorded in module-level :data:`VALIDATOR_BACKEND`
after the first detection call. Public accessor:
:func:`get_active_backend` — triggers the lazy load if needed and returns
the selected backend name (``"insightface"``, ``"opencv"``, ``"mediapipe"``,
or ``"none"``).
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


BACKEND_INSIGHTFACE = "insightface"
BACKEND_OPENCV = "opencv"
BACKEND_MEDIAPIPE = "mediapipe"
BACKEND_NONE = "none"

# MediaPipe Tasks API needs a local .tflite model file (no auto-download
# built into the library, unlike insightface). Google-hosted public asset,
# free, documented at ai.google.dev/edge/mediapipe/solutions/vision/face_detector.
_MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
_MEDIAPIPE_MODEL_PATH = Path.home() / ".mediapipe_models" / "blaze_face_short_range.tflite"
_MEDIAPIPE_MIN_CONFIDENCE = 0.5
_MEDIAPIPE_DOWNLOAD_TIMEOUT_SEC = 15


class FaceValidatorUnavailableError(RuntimeError):
    """Raised when no face-detection backend could be loaded.

    Honest failure, on purpose: callers must NOT treat this the same as "0
    faces detected" (a real, meaningful result) — validation is simply not
    available right now.
    """


def _ensure_mediapipe_model(dest: Path = _MEDIAPIPE_MODEL_PATH) -> Path:
    """Download the MediaPipe face-detector model to ``dest`` if missing."""
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with urllib.request.urlopen(
        _MEDIAPIPE_MODEL_URL, timeout=_MEDIAPIPE_DOWNLOAD_TIMEOUT_SEC
    ) as resp:
        tmp.write_bytes(resp.read())
    tmp.replace(dest)
    return dest


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
    "FaceValidatorUnavailableError",
    "FaceScore",
    "ValidationResult",
    "VALIDATOR_BACKEND",
    "BACKEND_INSIGHTFACE",
    "BACKEND_OPENCV",
    "BACKEND_MEDIAPIPE",
    "BACKEND_NONE",
    "get_active_backend",
]


class ValidationResult(NamedTuple):
    """Result of :meth:`FaceValidator.validate` — ``(ok, confidence, reason)``.

    Fail-closed contract: this is only ever returned for a real, observed
    outcome (face found / not found). When no detection backend is
    available, ``validate()`` raises :class:`FaceValidatorUnavailableError`
    instead of returning a result here — see the module docstring for why
    a silent negative is never acceptable.
    """

    ok: bool
    confidence: float
    reason: str


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
    backend: str            # "insightface" | "opencv" | "mediapipe" — surfaces degradation


@dataclass(frozen=True)
class _ScoredDetection:
    """Internal: one detection enriched with the signals SCRFD already gives."""

    bbox: tuple[int, int, int, int]
    det_score: float | None  # None when the backend (opencv) provides no score
    kps: Any | None          # 5 landmarks or None (opencv/mediapipe)


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
        self._mediapipe_detector: Any = None
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

    def validate(self, image_path: Path) -> "ValidationResult":
        """Validate one photo for face-swap suitability.

        Returns ``(ok, confidence, reason)``. ``ok`` is ``True`` iff a face
        was detected; ``confidence`` is the same 0..1 composite score as
        :meth:`score_largest_face` (0.0 when no face). Fail-closed: raises
        :class:`FaceValidatorUnavailableError` when no backend is available
        instead of returning a result — callers must not treat "engine
        unavailable" as "no face" (see module docstring).
        """
        score = self.score_largest_face(image_path)
        if score is None:
            return ValidationResult(ok=False, confidence=0.0, reason="no_face_detected")
        return ValidationResult(ok=True, confidence=score.composite, reason="ok")

    # ── internals ───────────────────────────────────────────────────────────

    def _detect_scored(
        self, image_path: Path
    ) -> tuple[list["_ScoredDetection"], tuple[int, int], str]:
        """Detect faces keeping det_score + kps. Returns (dets, (h, w), backend)."""
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"image not found: {path}")
        self._ensure_loaded()

        if self._mediapipe_detector is not None:
            raw, (img_h, img_w) = self._run_mediapipe(path)
            dets = [
                _ScoredDetection(bbox=bbox, det_score=score, kps=None)
                for bbox, score in raw
            ]
            return dets, (img_h, img_w), BACKEND_MEDIAPIPE

        if self._insightface_app is not None or self._opencv_cascade is not None:
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

            for bbox in self._detect_opencv(path):
                dets.append(_ScoredDetection(bbox=bbox, det_score=None, kps=None))
            return dets, (img_h, img_w), BACKEND_OPENCV

        raise FaceValidatorUnavailableError(
            "валидация недоступна: ни один backend распознавания лиц "
            "(InsightFace/OpenCV/MediaPipe) не загружен"
        )

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
                self._loaded = True
                return
            except Exception as exc:  # noqa: BLE001 - fallback intentional
                logger.warning(
                    "FaceValidator: OpenCV unavailable (%s); "
                    "falling back to MediaPipe",
                    exc,
                )

            try:
                import mediapipe as mp  # type: ignore[import-not-found]
                from mediapipe.tasks.python import vision as mp_vision  # type: ignore[import-not-found]
                from mediapipe.tasks.python.core.base_options import (  # type: ignore[import-not-found]
                    BaseOptions as MPBaseOptions,
                )

                model_path = _ensure_mediapipe_model()
                options = mp_vision.FaceDetectorOptions(
                    base_options=MPBaseOptions(model_asset_path=str(model_path)),
                    min_detection_confidence=_MEDIAPIPE_MIN_CONFIDENCE,
                )
                self._mediapipe_detector = mp_vision.FaceDetector.create_from_options(
                    options
                )
                VALIDATOR_BACKEND = BACKEND_MEDIAPIPE
                logger.info(
                    "FaceValidator: using MediaPipe (blaze_face_short_range)"
                )
            except Exception as exc:  # noqa: BLE001 - last-resort backend
                logger.error(
                    "FaceValidator: no backend available — validation is "
                    "DISABLED, not simulated (%s)",
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
        if self._mediapipe_detector is not None:
            return self._detect_mediapipe(path)
        raise FaceValidatorUnavailableError(
            "валидация недоступна: ни один backend распознавания лиц "
            "(InsightFace/OpenCV/MediaPipe) не загружен"
        )

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

    def _run_mediapipe(
        self, path: Path
    ) -> tuple[list[tuple[tuple[int, int, int, int], float | None]], tuple[int, int]]:
        """Run the MediaPipe detector. Reads via Pillow — no cv2 dependency,
        so this still works when cv2 itself is the broken piece."""
        import numpy as np  # type: ignore[import-not-found]
        import mediapipe as mp  # type: ignore[import-not-found]
        from PIL import Image as PILImage

        pil_img = PILImage.open(path).convert("RGB")
        arr = np.ascontiguousarray(np.asarray(pil_img))
        img_h, img_w = arr.shape[:2]
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
        result = self._mediapipe_detector.detect(mp_img)

        out: list[tuple[tuple[int, int, int, int], float | None]] = []
        for det in result.detections:
            bb = det.bounding_box
            x1, y1 = int(bb.origin_x), int(bb.origin_y)
            x2, y2 = x1 + int(bb.width), y1 + int(bb.height)
            score = float(det.categories[0].score) if det.categories else None
            out.append(((x1, y1, x2, y2), score))
        return out, (img_h, img_w)

    def _detect_mediapipe(self, path: Path) -> list[tuple[int, int, int, int]]:
        dets, _ = self._run_mediapipe(path)
        return [bbox for bbox, _ in dets]
