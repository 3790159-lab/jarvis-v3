# -*- coding: utf-8 -*-
"""Tests for the litterbox.catbox.moe uploader.

All HTTP traffic is mocked via ``unittest.mock.AsyncMock`` — the real
litterbox endpoint is never touched.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.block_m2_video.litterbox_uploader import (
    LitterboxError,
    upload_to_litterbox,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _text_response(body: str, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://litterbox.catbox.moe/")
    return httpx.Response(
        status_code=status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        request=request,
    )


def _make_file(tmp_path: Path, name: str = "video.mp4") -> Path:
    p = tmp_path / name
    # Minimal MP4-ish bytes so multipart upload has real payload.
    p.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 1024)
    return p


def _mock_http(*, post_response=None, post_side_effect=None) -> MagicMock:
    http = MagicMock(spec=httpx.AsyncClient)
    if post_side_effect is not None:
        http.post = AsyncMock(side_effect=post_side_effect)
    else:
        http.post = AsyncMock(return_value=post_response)
    http.aclose = AsyncMock()
    return http


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upload_returns_url_on_success(tmp_path):
    file = _make_file(tmp_path)
    http = _mock_http(post_response=_text_response("https://litter.catbox.moe/abc.mp4"))

    url = await upload_to_litterbox(file, http_client=http)

    assert url == "https://litter.catbox.moe/abc.mp4"
    http.post.assert_awaited_once()


@pytest.mark.anyio
async def test_upload_strips_whitespace(tmp_path):
    file = _make_file(tmp_path)
    http = _mock_http(
        post_response=_text_response("https://litter.catbox.moe/abc.mp4\n  ")
    )

    url = await upload_to_litterbox(file, http_client=http)

    assert url == "https://litter.catbox.moe/abc.mp4"
    assert not url.endswith(("\n", " "))


@pytest.mark.anyio
async def test_upload_raises_on_network_error(tmp_path):
    file = _make_file(tmp_path)
    http = _mock_http(post_side_effect=httpx.ConnectError("network down"))

    with pytest.raises(LitterboxError, match="network"):
        await upload_to_litterbox(file, http_client=http)


@pytest.mark.anyio
async def test_upload_raises_on_invalid_response(tmp_path):
    file = _make_file(tmp_path)
    http = _mock_http(post_response=_text_response("Error: invalid file"))

    with pytest.raises(LitterboxError, match="invalid response"):
        await upload_to_litterbox(file, http_client=http)


@pytest.mark.anyio
async def test_upload_sends_correct_form_fields(tmp_path):
    file = _make_file(tmp_path, name="clip.mp4")
    http = _mock_http(post_response=_text_response("https://litter.catbox.moe/x.mp4"))

    await upload_to_litterbox(file, http_client=http)

    call = http.post.await_args
    assert call.kwargs["data"] == {"reqtype": "fileupload", "time": "24h"}
    files = call.kwargs["files"]
    assert "fileToUpload" in files
    name, fh, _content_type = files["fileToUpload"]
    assert name == "clip.mp4"
    # File handle should be readable binary (the helper opens with 'rb').
    assert hasattr(fh, "read")


@pytest.mark.anyio
async def test_upload_accepts_custom_retention(tmp_path):
    file = _make_file(tmp_path)
    http = _mock_http(post_response=_text_response("https://litter.catbox.moe/x.mp4"))

    await upload_to_litterbox(file, retention="72h", http_client=http)

    call = http.post.await_args
    assert call.kwargs["data"]["time"] == "72h"
