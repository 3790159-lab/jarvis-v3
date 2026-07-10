# -*- coding: utf-8 -*-
"""Instagram media prep (Этап 3, кирпич 3c).

Instagram's Content Publishing API (see ``create_media_container`` in
:mod:`app.services.instagram_api`) requires a photo's ``image_url`` to point
at a public https host serving a JPEG with:

  - width between 320 and 1440 px
  - aspect ratio between 4:5 (0.8, portrait) and 1.91:1 (landscape)

``prepare_for_ig`` converts an arbitrary local image (PNG/WebP/JPEG, with or
without alpha) into a compliant JPEG: RGB, aspect fixed via center-crop or
padding (``IG_ASPECT_STRATEGY=crop|pad``, default crop), width clamped into
range, EXIF/ICC metadata stripped. ``host_for_ig`` chains that with the R2
uploader (:mod:`app.services.r2_storage`) to produce the public URL that
feeds straight into ``create_media_container``.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

__all__ = [
    "IGMediaError",
    "MIN_WIDTH",
    "MAX_WIDTH",
    "MIN_ASPECT",
    "MAX_ASPECT",
    "prepare_for_ig",
    "host_for_ig",
]

MIN_WIDTH = 320
MAX_WIDTH = 1440
MIN_ASPECT = 4 / 5  # 0.8 — tallest allowed (portrait, 4:5)
MAX_ASPECT = 1.91  # widest allowed (landscape, 1.91:1)

_BACKGROUND = (255, 255, 255)
_JPEG_QUALITY = 90
_STRATEGIES = ("crop", "pad")


class IGMediaError(RuntimeError):
    """Raised when a source image cannot be prepared for IG publishing."""


def _resolve_strategy(explicit: Optional[str]) -> str:
    value = (explicit if explicit is not None else os.getenv("IG_ASPECT_STRATEGY", "crop"))
    value = value.strip().lower()
    if value not in _STRATEGIES:
        raise IGMediaError(
            f"invalid IG_ASPECT_STRATEGY: {value!r} (expected one of {_STRATEGIES})"
        )
    return value


def _flatten_to_rgb(img: Image.Image) -> Image.Image:
    """Drop any alpha channel, compositing onto a solid background."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, _BACKGROUND)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return img.convert("RGB")


def _target_aspect(aspect: float) -> float:
    return min(max(aspect, MIN_ASPECT), MAX_ASPECT)


def _fix_aspect_crop(img: Image.Image) -> Image.Image:
    w, h = img.size
    aspect = w / h
    target = _target_aspect(aspect)
    if abs(aspect - target) < 1e-9:
        return img
    if aspect > target:  # too wide -> crop width
        new_w = max(1, round(h * target))
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = max(1, round(w / target))  # too tall -> crop height
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def _fix_aspect_pad(img: Image.Image) -> Image.Image:
    w, h = img.size
    aspect = w / h
    target = _target_aspect(aspect)
    if abs(aspect - target) < 1e-9:
        return img
    if aspect > target:  # too wide -> pad height
        new_h = max(1, round(w / target))
        canvas = Image.new("RGB", (w, new_h), _BACKGROUND)
        canvas.paste(img, (0, (new_h - h) // 2))
        return canvas
    new_w = max(1, round(h * target))  # too tall -> pad width
    canvas = Image.new("RGB", (new_w, h), _BACKGROUND)
    canvas.paste(img, ((new_w - w) // 2, 0))
    return canvas


def _resize_width(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w < MIN_WIDTH:
        new_w = MIN_WIDTH
    elif w > MAX_WIDTH:
        new_w = MAX_WIDTH
    else:
        return img
    new_h = max(1, round(h * new_w / w))
    return img.resize((new_w, new_h), Image.LANCZOS)


def prepare_for_ig(path: str | Path, *, strategy: Optional[str] = None) -> Path:
    """Convert/resize/reframe ``path`` into an Instagram-ready JPEG.

    Writes a new file (the source is left untouched) and returns its path.
    """
    src = Path(path)
    if not src.exists() or not src.is_file():
        raise IGMediaError(f"file not found: {src}")

    mode = _resolve_strategy(strategy)

    try:
        with Image.open(src) as raw:
            img = ImageOps.exif_transpose(raw)
            img = _flatten_to_rgb(img)
    except IGMediaError:
        raise
    except Exception as exc:  # noqa: BLE001 — honest wrap of any Pillow failure
        raise IGMediaError(f"cannot read image {src}: {exc}") from exc

    img = _fix_aspect_crop(img) if mode == "crop" else _fix_aspect_pad(img)
    img = _resize_width(img)

    fd, tmp_name = tempfile.mkstemp(prefix="ig_", suffix=".jpg")
    os.close(fd)
    out_path = Path(tmp_name)
    img.save(out_path, "JPEG", quality=_JPEG_QUALITY)
    logger.info("prepared %s for IG -> %s (%dx%d)", src.name, out_path.name, *img.size)
    return out_path


def host_for_ig(
    path: str | Path,
    *,
    strategy: Optional[str] = None,
    key: Optional[str] = None,
    client: Optional[Any] = None,
    config: Optional[Any] = None,
) -> str:
    """``prepare_for_ig`` + R2 upload -> public URL for ``create_media_container``."""
    from app.services.r2_storage import upload_file

    prepared = prepare_for_ig(path, strategy=strategy)
    return upload_file(prepared, key=key, client=client, config=config)
