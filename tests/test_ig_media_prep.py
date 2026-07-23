# -*- coding: utf-8 -*-
"""Tests for Instagram media prep (Этап 3, кирпич 3c).

Money-safe: no network call is ever made. host_for_ig() always injects a
MagicMock boto3 client + R2Config, mirroring test_r2_storage.py's pattern —
the real R2 upload path is never exercised here.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services.ig_media_prep import (
    MAX_ASPECT,
    MAX_WIDTH,
    MIN_ASPECT,
    MIN_WIDTH,
    IGMediaError,
    host_for_ig,
    is_media_url_alive,
    prepare_for_ig,
)
from app.services.r2_storage import R2Config

_ASPECT_TOLERANCE = 0.03  # integer pixel rounding can nudge aspect a hair


def _png(tmp_path: Path, size, name="src.png", mode="RGB", color=(200, 50, 50)):
    p = tmp_path / name
    Image.new(mode, size, color).save(p, "PNG")
    return p


def _rgba_png(tmp_path: Path, size, name="src.png"):
    p = tmp_path / name
    img = Image.new("RGBA", size, (10, 20, 30, 0))
    # opaque left half, transparent right half so flattening is observable
    for x in range(size[0] // 2):
        for y in range(size[1]):
            img.putpixel((x, y), (10, 20, 30, 255))
    img.save(p, "PNG")
    return p


def _webp(tmp_path: Path, size, name="src.webp"):
    p = tmp_path / name
    Image.new("RGB", size, (30, 120, 200)).save(p, "WEBP")
    return p


def _r2_config() -> R2Config:
    return R2Config(
        account_id="acct",
        access_key_id="ak",
        secret_access_key="sk",
        bucket="jarvis-media",
        endpoint="https://acct.r2.cloudflarestorage.com",
        public_base_url="https://pub-abc.r2.dev",
    )


def _r2_client() -> MagicMock:
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    return client


# ── format conversion ────────────────────────────────────────────────────────


def test_png_converted_to_jpeg(tmp_path):
    src = _png(tmp_path, (400, 400))

    out = prepare_for_ig(src)

    assert out.suffix == ".jpg"
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"


def test_webp_converted_to_jpeg(tmp_path):
    src = _webp(tmp_path, (400, 400))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        assert img.format == "JPEG"


def test_rgba_alpha_is_flattened(tmp_path):
    src = _rgba_png(tmp_path, (400, 400))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        assert img.mode == "RGB"  # no alpha channel survives


def test_source_file_is_untouched(tmp_path):
    src = _png(tmp_path, (400, 400))
    original_bytes = src.read_bytes()

    out = prepare_for_ig(src)

    assert out != src
    assert src.read_bytes() == original_bytes


# ── size bounds ──────────────────────────────────────────────────────────────


def test_tiny_1x1_image_upscaled_to_min_width(tmp_path):
    src = _png(tmp_path, (1, 1))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        w, h = img.size
        assert w >= MIN_WIDTH
        assert MIN_ASPECT - _ASPECT_TOLERANCE <= w / h <= MAX_ASPECT + _ASPECT_TOLERANCE


def test_oversized_image_downscaled_to_max_width(tmp_path):
    src = _png(tmp_path, (2000, 2000))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        w, _h = img.size
        assert w <= MAX_WIDTH


def test_in_range_width_left_alone(tmp_path):
    src = _png(tmp_path, (800, 800))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        assert img.size == (800, 800)


# ── aspect ratio fix-up ──────────────────────────────────────────────────────


def test_crop_strategy_fixes_too_tall_image(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "crop")
    src = _png(tmp_path, (400, 2000))  # aspect 0.2, way under MIN_ASPECT

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        w, h = img.size
        aspect = w / h
        assert MIN_ASPECT - _ASPECT_TOLERANCE <= aspect <= MAX_ASPECT + _ASPECT_TOLERANCE


def test_crop_strategy_fixes_too_wide_image(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "crop")
    src = _png(tmp_path, (2000, 400))  # aspect 5.0, way over MAX_ASPECT

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        w, h = img.size
        aspect = w / h
        assert MIN_ASPECT - _ASPECT_TOLERANCE <= aspect <= MAX_ASPECT + _ASPECT_TOLERANCE


def test_pad_strategy_fixes_too_tall_image_by_growing_width(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "pad")
    src = _png(tmp_path, (400, 2000), color=(10, 200, 10))

    out = prepare_for_ig(src)

    with Image.open(out) as img:
        w, h = img.size
        aspect = w / h
        assert MIN_ASPECT - _ASPECT_TOLERANCE <= aspect <= MAX_ASPECT + _ASPECT_TOLERANCE
        # padded corner is background (white), not the source color
        assert img.getpixel((0, 0)) == (255, 255, 255)


def test_pad_strategy_preserves_full_source_content(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "pad")
    src = _png(tmp_path, (2000, 400))

    out_pad = prepare_for_ig(src)
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "crop")
    out_crop = prepare_for_ig(src)

    with Image.open(out_pad) as padded, Image.open(out_crop) as cropped:
        # pad never loses source pixels, so its long side is >= the crop's
        assert padded.size[0] >= cropped.size[0]


def test_already_in_range_aspect_is_unchanged_by_crop(tmp_path):
    src = _png(tmp_path, (1000, 1000))  # aspect 1.0, already legal

    out = prepare_for_ig(src, strategy="crop")

    with Image.open(out) as img:
        assert img.size == (1000, 1000)


def test_explicit_strategy_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_ASPECT_STRATEGY", "pad")
    src = _png(tmp_path, (400, 2000))

    out = prepare_for_ig(src, strategy="crop")

    with Image.open(out) as img:
        w, h = img.size
        # crop never grows the short dimension beyond the original width
        assert w <= 400 * 2  # sanity: not padded out huge


def test_invalid_strategy_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("IG_ASPECT_STRATEGY", raising=False)
    src = _png(tmp_path, (400, 400))

    with pytest.raises(IGMediaError, match="STRATEGY"):
        prepare_for_ig(src, strategy="bogus")


# ── metadata stripping ───────────────────────────────────────────────────────


def test_exif_metadata_is_stripped(tmp_path):
    src = tmp_path / "with_exif.jpg"
    img = Image.new("RGB", (400, 400), (100, 100, 100))
    exif = img.getexif()
    exif[0x0131] = "SecretSoftwareTag"  # Software tag
    img.save(src, "JPEG", exif=exif)

    out = prepare_for_ig(src)

    with Image.open(out) as result:
        result_exif = result.getexif()
        assert 0x0131 not in result_exif


# ── errors ───────────────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path):
    with pytest.raises(IGMediaError, match="not found"):
        prepare_for_ig(tmp_path / "nope.png")


# ── R2 hosting integration ───────────────────────────────────────────────────


def test_host_for_ig_uploads_prepared_jpeg_and_returns_url(tmp_path):
    src = _png(tmp_path, (2000, 400))
    client = _r2_client()

    url = host_for_ig(src, client=client, config=_r2_config())

    assert url.startswith("https://pub-abc.r2.dev/media/")
    client.put_object.assert_called_once()
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["ContentType"] == "image/jpeg"
    assert kwargs["Key"].endswith(".jpg")


def test_host_for_ig_body_is_valid_jpeg_within_ig_bounds(tmp_path):
    src = _png(tmp_path, (2000, 400))
    client = _r2_client()
    captured: list[bytes] = []
    client.put_object.side_effect = lambda **kw: captured.append(kw["Body"].read())

    host_for_ig(src, client=client, config=_r2_config())

    import io

    with Image.open(io.BytesIO(captured[0])) as img:
        assert img.format == "JPEG"
        w, h = img.size
        assert MIN_WIDTH <= w <= MAX_WIDTH
        assert MIN_ASPECT - _ASPECT_TOLERANCE <= w / h <= MAX_ASPECT + _ASPECT_TOLERANCE


# ── media URL staleness check (HEAD probe) ───────────────────────────────────
#
# litterbox/R2 tmp links can die before the [📤 Опубликовать] tap. is_media_url_alive
# HEAD-probes the URL so the bot can give an honest "медиа протухло" instead of a
# confusing Meta container-creation failure. Never touches real network in tests —
# urllib.request.urlopen is monkeypatched.


class _FakeResp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_is_media_url_alive_true_on_200(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["method"] = req.get_method()
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResp(200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert is_media_url_alive("https://pub-abc.r2.dev/media/x.jpg") is True
    assert seen["method"] == "HEAD"
    assert seen["url"] == "https://pub-abc.r2.dev/media/x.jpg"


def test_is_media_url_alive_true_on_redirect_3xx(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResp(302))
    assert is_media_url_alive("https://pub-abc.r2.dev/media/x.jpg") is True


def test_is_media_url_alive_false_on_404(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert is_media_url_alive("https://litterbox.catbox.moe/dead.jpg") is False


def test_is_media_url_alive_false_on_network_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert is_media_url_alive("https://pub-abc.r2.dev/media/x.jpg") is False


def test_is_media_url_alive_false_on_timeout(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert is_media_url_alive("https://pub-abc.r2.dev/media/x.jpg") is False


def test_is_media_url_alive_sends_browser_user_agent(monkeypatch):
    # R2's public *.r2.dev host sits behind Cloudflare, which 403-blocks the
    # default "Python-urllib/x" UA. Without a browser UA every live R2 URL reads
    # as dead and fail-close kills the publish. Same UA-ban class as Replicate.
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _FakeResp(200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert is_media_url_alive("https://pub-abc.r2.dev/media/x.jpg") is True
    assert seen["ua"], "HEAD-проба ушла без User-Agent — Cloudflare её забанит"
    assert "Python-urllib" not in seen["ua"]
    assert "Mozilla/5.0" in seen["ua"]
