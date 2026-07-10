# -*- coding: utf-8 -*-
"""Tests for the MEDIA_STORAGE dispatcher (r2 | litterbox).

No real upload — both backends are monkeypatched. Verifies the flag routes to
the right backend, defaults to r2, and re-raises backend failures as an honest
MediaDeliveryError.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import app.services.media_delivery as md
from app.services.media_delivery import MediaDeliveryError, host_media
from app.services.r2_storage import R2Error
from app.services.block_m2_video.litterbox_uploader import LitterboxError


@pytest.fixture
def _file(tmp_path) -> Path:
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"data")
    return p


@pytest.mark.anyio
async def test_defaults_to_r2(_file, monkeypatch):
    monkeypatch.delenv("MEDIA_STORAGE", raising=False)
    r2 = AsyncMock(return_value="https://pub-abc.r2.dev/media/x.mp4")
    lb = AsyncMock(return_value="https://litter.catbox.moe/x.mp4")
    monkeypatch.setattr(md, "upload_file_async", r2)
    monkeypatch.setattr(md, "upload_to_litterbox", lb)

    url = await host_media(_file)

    assert url == "https://pub-abc.r2.dev/media/x.mp4"
    r2.assert_awaited_once()
    lb.assert_not_awaited()


@pytest.mark.anyio
async def test_explicit_r2(_file, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "r2")
    r2 = AsyncMock(return_value="https://pub-abc.r2.dev/media/x.mp4")
    monkeypatch.setattr(md, "upload_file_async", r2)

    url = await host_media(_file)

    assert url.startswith("https://pub-abc.r2.dev/")
    r2.assert_awaited_once()


@pytest.mark.anyio
async def test_litterbox_flag_routes_to_litterbox(_file, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "litterbox")
    r2 = AsyncMock(return_value="https://pub-abc.r2.dev/media/x.mp4")
    lb = AsyncMock(return_value="https://litter.catbox.moe/x.mp4")
    monkeypatch.setattr(md, "upload_file_async", r2)
    monkeypatch.setattr(md, "upload_to_litterbox", lb)

    url = await host_media(_file, retention="1h")

    assert url == "https://litter.catbox.moe/x.mp4"
    lb.assert_awaited_once()
    assert lb.await_args.kwargs.get("retention") == "1h"
    r2.assert_not_awaited()


@pytest.mark.anyio
async def test_unknown_flag_falls_back_to_r2(_file, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "banana")
    r2 = AsyncMock(return_value="https://pub-abc.r2.dev/media/x.mp4")
    monkeypatch.setattr(md, "upload_file_async", r2)

    url = await host_media(_file)

    assert url.startswith("https://pub-abc.r2.dev/")
    r2.assert_awaited_once()


@pytest.mark.anyio
async def test_r2_failure_wrapped(_file, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "r2")
    monkeypatch.setattr(
        md, "upload_file_async", AsyncMock(side_effect=R2Error("boom"))
    )

    with pytest.raises(MediaDeliveryError, match="r2"):
        await host_media(_file)


@pytest.mark.anyio
async def test_litterbox_failure_wrapped(_file, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "litterbox")
    monkeypatch.setattr(
        md, "upload_to_litterbox", AsyncMock(side_effect=LitterboxError("down"))
    )

    with pytest.raises(MediaDeliveryError, match="litterbox"):
        await host_media(_file)
