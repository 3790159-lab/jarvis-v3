# -*- coding: utf-8 -*-
"""Local face validator for Block M.2.5 face-swap batch pipeline.

Runs entirely on the bot host (not on RunPod) so we can reject source/target
photos with no detectable face before spending any pod money.

Backend selection at first call:

1. **InsightFace** (preferred) — ``buffalo_l`` model, downloads ~500MB to
   ``~/.insightface/models`` on first use. Robust, returns face boxes and
   landmarks.
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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


BACKEND_INSIGHTFACE = "insightface"
BACKEND_OPENCV = "opencv"
BACKEND_NONE = "none"

# Module-level record of which backend is in use. Updated by
# ``FaceValidator._ensure_loaded()`` and observable via
# :func:`get_active_backend`. Don't read this directly from external code —
# call the getter so the lazy load is forced first.
VALIDATOR_BACKEND: str = BACKEND_NONE

__all__ = [
    "FaceValidator",
    "VALIDATOR_BACKEND",
    "BACKEND_INSIGHTFACE",
    "BACKEND_OPENCV",
    "BACKEND_NONE",
    "get_active_backend",
]


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

    # ── internals ───────────────────────────────────────────────────────────

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

                    # Construct without ``allowed_modules`` for compatibility
                    # with older insightface releases (0.2.x, only Py3.14
                    # wheel available) that lack that kwarg. Modern releases
                    # ignore the extra loaded modules cheaply.
                    try:
                        app = insightface.app.FaceAnalysis(
                            name="buffalo_l",
                            allowed_modules=["detection"],
                        )
                    except TypeError:
                        app = insightface.app.FaceAnalysis(name="buffalo_l")
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
