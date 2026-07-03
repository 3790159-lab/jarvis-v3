# -*- coding: utf-8 -*-
"""Tests for Block M common infrastructure (M.1.0)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Package imports ──────────────────────────────────────────────────────────

class TestPackageImports:
    def test_import_replicate_client(self):
        from app.services.block_m_common import ReplicateVideoClient
        assert callable(ReplicateVideoClient)

    def test_import_persona_storage(self):
        from app.services.block_m_common import PersonaStorage, Persona
        assert callable(PersonaStorage)
        assert Persona is not None

    def test_import_video_queue(self):
        from app.services.block_m_common import VideoJob, VideoQueue
        assert callable(VideoQueue)
        assert VideoJob is not None

    def test_import_cost_tracker(self):
        from app.services.block_m_common import CostTracker, DailyLimitExceeded, DAILY_LIMIT_USD
        assert callable(CostTracker)
        assert issubclass(DailyLimitExceeded, Exception)
        assert DAILY_LIMIT_USD == 10.0


# ─── ReplicateVideoClient — init ─────────────────────────────────────────────

class TestReplicateVideoClientInit:
    def test_raises_without_token(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        with pytest.raises(RuntimeError, match="REPLICATE_API_TOKEN"):
            ReplicateVideoClient()

    def test_accepts_explicit_token(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="r8_explicit")
        assert client._token == "r8_explicit"

    def test_reads_token_from_env(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_from_env")
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient()
        assert client._token == "r8_from_env"

    def test_strips_whitespace_from_env_token(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_TOKEN", "  r8_spaces  ")
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient()
        assert client._token == "r8_spaces"

    def test_sets_auth_header(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="r8_tok")
        assert client._headers["Authorization"] == "Token r8_tok"


# ─── ReplicateVideoClient — generate_kling_v21 ───────────────────────────────

class TestKlingV21:
    @pytest.mark.anyio
    async def test_returns_expected_keys(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = "https://example.com/video.mp4"
            result = await client.generate_kling_v21("https://img.com/src.jpg", "move", 5)

        assert set(result.keys()) == {"video_url", "cost_usd", "duration_sec"}

    @pytest.mark.anyio
    async def test_string_output_preserved(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = "https://example.com/video.mp4"
            result = await client.generate_kling_v21("https://img.com/src.jpg", "move", 5)

        assert result["video_url"] == "https://example.com/video.mp4"
        assert result["duration_sec"] == 5

    @pytest.mark.anyio
    async def test_list_output_uses_first_item(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = ["https://example.com/first.mp4", "https://second.mp4"]
            result = await client.generate_kling_v21("https://img.com/src.jpg", "move", 5)

        assert result["video_url"] == "https://example.com/first.mp4"

    @pytest.mark.anyio
    async def test_cost_scales_with_duration(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = "https://v.mp4"
            r5 = await client.generate_kling_v21("img", "prompt", 5)
            r10 = await client.generate_kling_v21("img", "prompt", 10)

        assert r10["cost_usd"] == pytest.approx(r5["cost_usd"] * 2, rel=0.01)

    @pytest.mark.anyio
    async def test_cost_is_positive(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = "https://v.mp4"
            result = await client.generate_kling_v21("img", "prompt", 5)

        assert result["cost_usd"] > 0


# ─── ReplicateVideoClient — train_flux_lora ──────────────────────────────────

def _make_async_client_mock(session_mock):
    """Build an async context manager mock that yields session_mock."""
    async_ctx = MagicMock()
    async_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    async_ctx.__aexit__ = AsyncMock(return_value=None)
    return async_ctx


def _patch_train_internals(client, weights_url="https://weights.com/lora.safetensors"):
    """Return a tuple of patch.object calls for all train_flux_lora dependencies."""
    from unittest.mock import patch as _patch
    return (
        _patch.object(client, "_get_username", new_callable=AsyncMock),
        _patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock),
        _patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock),
        _patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock),
        _patch.object(client, "_submit_training", new_callable=AsyncMock),
        _patch.object(client, "_poll_training", new_callable=AsyncMock),
    )


class TestFluxLoraTraining:
    @pytest.mark.anyio
    async def test_train_returns_expected_keys(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "testuser"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "training_abc"
            m_poll.return_value = {"weights": "https://weights.com/lora.safetensors"}
            result = await client.train_flux_lora(["img1", "img2"], "sks_test", 500)

        assert "weights_url" in result
        assert "cost_usd" in result

    @pytest.mark.anyio
    async def test_train_dict_output_extracts_weights(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "testuser"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "t1"
            m_poll.return_value = {"weights": "https://weights.com/lora.safetensors"}
            result = await client.train_flux_lora(["img1"], "sks_p", 1000)

        assert result["weights_url"] == "https://weights.com/lora.safetensors"

    @pytest.mark.anyio
    async def test_train_version_field_used_as_fallback(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "testuser"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "t1"
            m_poll.return_value = {"version": "https://version.url/lora"}
            result = await client.train_flux_lora(["img1"], "sks_p", 1000)

        assert result["weights_url"] == "https://version.url/lora"

    @pytest.mark.anyio
    async def test_training_cost_is_substantial(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient, _COST_FLUX_TRAINING
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "u"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "t1"
            m_poll.return_value = {"weights": "https://w.url"}
            result = await client.train_flux_lora(["img1"], "sks_p")

        assert result["cost_usd"] == pytest.approx(_COST_FLUX_TRAINING)

    @pytest.mark.anyio
    async def test_train_flux_lora_uses_trainings_endpoint(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient, _FLUX_TRAINER_MODEL
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "u"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "training_abc"
            m_poll.return_value = {"weights": "https://w.url"}
            await client.train_flux_lora(["img"], "sks_test")

        m_sub.assert_called_once()
        assert m_sub.call_args[0][0] == _FLUX_TRAINER_MODEL

    @pytest.mark.anyio
    async def test_train_flux_lora_polls_correct_url(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "u"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "training_xyz999"
            m_poll.return_value = {"weights": "https://w.url"}
            await client.train_flux_lora(["img"], "sks_test")

        m_poll.assert_called_once_with("training_xyz999")

    @pytest.mark.anyio
    async def test_train_flux_lora_extracts_weights_from_output(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_up, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "u"
            m_zip.return_value = b"fakezip"
            m_up.return_value = "https://zip.url"
            m_sub.return_value = "t1"
            m_poll.return_value = {"weights": "https://exact-weights.safetensors"}
            result = await client.train_flux_lora(["img"], "sks_test")

        assert result["weights_url"] == "https://exact-weights.safetensors"

    @pytest.mark.anyio
    async def test_get_username_returns_authenticated_user(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"username": "myuser123"})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            username = await client._get_username()

        assert username == "myuser123"

    @pytest.mark.anyio
    async def test_ensure_destination_creates_if_missing(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        get_response = MagicMock()
        get_response.status_code = 404

        post_response = MagicMock()
        post_response.raise_for_status = MagicMock()

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=get_response)
        session_mock.post = AsyncMock(return_value=post_response)

        with patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            await client._ensure_destination_exists("testuser/my-lora")

        session_mock.post.assert_called_once()
        post_url = session_mock.post.call_args[0][0]
        assert post_url == "https://api.replicate.com/v1/models"

    # ── New M.1.2.2 tests ──────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_get_latest_version_returns_hash(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"latest_version": {"id": "abc123hash"}})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            version = await client._get_latest_version("ostris/flux-dev-lora-trainer")

        assert version == "abc123hash"

    @pytest.mark.anyio
    async def test_create_zip_from_urls(self):
        import io as _io
        import zipfile as _zipfile
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        img_bytes = b"fake_png_data_here"
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = img_bytes

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            result = await client._create_zip_from_urls(["url1.jpg", "url2.jpg"])

        zf = _zipfile.ZipFile(_io.BytesIO(result))
        names = zf.namelist()
        assert len(names) == 2
        assert all(zf.read(n) == img_bytes for n in names)

    @pytest.mark.anyio
    async def test_upload_zip_to_replicate_returns_url(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"urls": {"get": "https://serve/file.zip"}})

        session_mock = AsyncMock()
        session_mock.post = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            url = await client._upload_zip_to_replicate(b"fake_zip_bytes")

        assert url == "https://serve/file.zip"

    @pytest.mark.anyio
    async def test_submit_training_uses_version_url(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"id": "training_xyz"})

        session_mock = AsyncMock()
        session_mock.post = AsyncMock(return_value=response)

        with patch.object(client, "_get_latest_version", new_callable=AsyncMock) as m_ver, \
             patch("httpx.AsyncClient", return_value=_make_async_client_mock(session_mock)):
            m_ver.return_value = "abc123"
            training_id = await client._submit_training("ostris/flux-dev-lora-trainer", {})

        assert training_id == "training_xyz"
        post_url = session_mock.post.call_args[0][0]
        assert "/versions/abc123/trainings" in post_url

    @pytest.mark.anyio
    async def test_train_flux_lora_packages_into_zip(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        images = [f"https://img{i}.jpg" for i in range(3)]
        fake_zip_bytes = b"PK\x03\x04fake_zip"
        fake_zip_url = "https://replicate-files.com/training.zip"

        with patch.object(client, "_get_username", new_callable=AsyncMock) as m_user, \
             patch.object(client, "_ensure_destination_exists", new_callable=AsyncMock), \
             patch.object(client, "_create_zip_from_urls", new_callable=AsyncMock) as m_zip, \
             patch.object(client, "_upload_zip_to_replicate", new_callable=AsyncMock) as m_upload, \
             patch.object(client, "_submit_training", new_callable=AsyncMock) as m_sub, \
             patch.object(client, "_poll_training", new_callable=AsyncMock) as m_poll:
            m_user.return_value = "testuser"
            m_zip.return_value = fake_zip_bytes
            m_upload.return_value = fake_zip_url
            m_sub.return_value = "training_abc"
            m_poll.return_value = {"weights": "https://weights.url"}
            result = await client.train_flux_lora(images, "sks_test")

        m_zip.assert_called_once_with(images)
        m_upload.assert_called_once_with(fake_zip_bytes)
        submit_payload = m_sub.call_args[0][1]
        assert submit_payload["input"]["input_images"] == fake_zip_url
        assert isinstance(submit_payload["input"]["input_images"], str)
        assert result["weights_url"] == "https://weights.url"


# ─── ReplicateVideoClient — generate_flux_with_lora ──────────────────────────

class TestFluxLoraInference:
    @pytest.mark.anyio
    async def test_returns_image_url(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = ["https://example.com/img.jpg"]
            result = await client.generate_flux_with_lora("portrait", "https://lora.url", "sks_p")

        assert result["image_url"] == "https://example.com/img.jpg"

    @pytest.mark.anyio
    async def test_string_output_used_directly(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        with patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
            mock.return_value = "https://example.com/img.jpg"
            result = await client.generate_flux_with_lora("portrait", "https://lora.url", "sks_p")

        assert result["image_url"] == "https://example.com/img.jpg"

    @pytest.mark.anyio
    async def test_trigger_word_prepended_to_prompt(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")
        captured_payload = {}

        async def capture(model, payload, **kw):
            captured_payload.update(payload)
            return ["https://img.jpg"]

        with patch.object(client, "_run_prediction", side_effect=capture):
            await client.generate_flux_with_lora("smiling", "https://lora.url", "sks_sofia")

        assert captured_payload["input"]["prompt"].startswith("sks_sofia")


# ─── ReplicateVideoClient — retry logic ──────────────────────────────────────

class TestReplicateRetry:
    @pytest.mark.anyio
    async def test_succeeds_on_second_attempt(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        call_count = 0

        async def flaky_submit(url, payload):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient error")
            return "pred_ok"

        async def mock_poll(pred_id, **kw):
            return "https://result.mp4"

        with patch.object(client, "_submit", side_effect=flaky_submit), \
             patch.object(client, "_poll", side_effect=mock_poll), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._run_prediction("kwaivgi/kling-v2.1", {}, max_retries=3)

        assert result == "https://result.mp4"
        assert call_count == 2

    @pytest.mark.anyio
    async def test_raises_after_all_retries_exhausted(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        async def always_fail(url, payload):
            raise RuntimeError("persistent failure")

        with patch.object(client, "_submit", side_effect=always_fail), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="failed after"):
                await client._run_prediction("some/model", {}, max_retries=2)

    @pytest.mark.anyio
    async def test_retry_count_matches_max(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        attempts = []

        async def counting_submit(url, payload):
            attempts.append(1)
            raise RuntimeError("fail")

        with patch.object(client, "_submit", side_effect=counting_submit), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError):
                await client._run_prediction("a/b", {}, max_retries=3)

        assert len(attempts) == 3

    @pytest.mark.anyio
    async def test_exponential_backoff_sleeps(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        async def always_fail(url, payload):
            raise RuntimeError("fail")

        sleep_calls = []

        async def record_sleep(n):
            sleep_calls.append(n)

        with patch.object(client, "_submit", side_effect=always_fail), \
             patch("asyncio.sleep", side_effect=record_sleep):
            with pytest.raises(RuntimeError):
                await client._run_prediction("a/b", {}, max_retries=3)

        # Should sleep 2^1=2 and 2^2=4 between attempts
        assert sleep_calls == [2, 4]

    @pytest.mark.anyio
    async def test_429_triggers_long_backoff(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        request = httpx.Request("POST", "https://api.replicate.com/v1/models/a/b/predictions")
        response = httpx.Response(429, request=request)
        err_429 = httpx.HTTPStatusError("429", request=request, response=response)

        async def always_429(url, payload):
            raise err_429

        sleep_calls = []

        async def record_sleep(n):
            sleep_calls.append(n)

        with patch.object(client, "_submit", side_effect=always_429), \
             patch("asyncio.sleep", side_effect=record_sleep):
            with pytest.raises(RuntimeError):
                await client._run_prediction("a/b", {}, max_retries=2)

        assert len(sleep_calls) == 1
        assert sleep_calls[0] >= 10.0

    @pytest.mark.anyio
    async def test_retry_after_header_respected(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        request = httpx.Request("POST", "https://api.replicate.com/v1/models/a/b/predictions")
        response = httpx.Response(429, headers={"Retry-After": "30"}, request=request)
        err_429 = httpx.HTTPStatusError("429", request=request, response=response)

        async def always_429(url, payload):
            raise err_429

        sleep_calls = []

        async def record_sleep(n):
            sleep_calls.append(n)

        with patch.object(client, "_submit", side_effect=always_429), \
             patch("asyncio.sleep", side_effect=record_sleep):
            with pytest.raises(RuntimeError):
                await client._run_prediction("a/b", {}, max_retries=2)

        assert len(sleep_calls) == 1
        assert 29.5 < sleep_calls[0] < 33.0

    @pytest.mark.anyio
    async def test_429_eventually_succeeds(self):
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        client = ReplicateVideoClient(api_token="tok")

        request = httpx.Request("POST", "https://api.replicate.com/v1/models/a/b/predictions")
        response = httpx.Response(429, request=request)
        err_429 = httpx.HTTPStatusError("429", request=request, response=response)

        call_count = 0

        async def flaky_submit(url, payload):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise err_429
            return "pred_ok"

        async def mock_poll(pred_id, **kw):
            return ["https://result.jpg"]

        with patch.object(client, "_submit", side_effect=flaky_submit), \
             patch.object(client, "_poll", side_effect=mock_poll), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._run_prediction("a/b", {}, max_retries=5)

        assert result == ["https://result.jpg"]
        assert call_count == 3


# ─── PersonaStorage ───────────────────────────────────────────────────────────

class TestPersonaStorage:
    @pytest.fixture
    def storage(self, tmp_path):
        from app.services.block_m_common.persona_storage import PersonaStorage
        return PersonaStorage(storage_dir=tmp_path / "personas")

    @pytest.mark.anyio
    async def test_create_returns_persona(self, storage):
        from app.services.block_m_common.persona_storage import Persona
        p = await storage.create_persona("Sofia", "tall, dark hair", "fashion")
        assert isinstance(p, Persona)
        assert p.name == "Sofia"
        assert p.style == "fashion"

    @pytest.mark.anyio
    async def test_create_generates_unique_id(self, storage):
        p = await storage.create_persona("Sofia", "desc", "style")
        assert p.persona_id.startswith("persona_")
        assert len(p.persona_id) > 8

    @pytest.mark.anyio
    async def test_create_generates_trigger_word(self, storage):
        p = await storage.create_persona("Sofia", "desc", "style")
        assert p.trigger_word.startswith("sks_")

    @pytest.mark.anyio
    async def test_create_lora_weights_is_none(self, storage):
        p = await storage.create_persona("Sofia", "desc", "style")
        assert p.lora_weights_url is None

    @pytest.mark.anyio
    async def test_create_makes_persona_directory(self, storage):
        p = await storage.create_persona("DirTest", "desc", "style")
        assert (storage._dir / p.persona_id).exists()

    @pytest.mark.anyio
    async def test_get_returns_created_persona(self, storage):
        p = await storage.create_persona("Alex", "blue eyes", "lifestyle")
        fetched = await storage.get_persona(p.persona_id)
        assert fetched is not None
        assert fetched.name == "Alex"
        assert fetched.description == "blue eyes"

    @pytest.mark.anyio
    async def test_get_nonexistent_returns_none(self, storage):
        result = await storage.get_persona("persona_does_not_exist")
        assert result is None

    @pytest.mark.anyio
    async def test_list_empty_initially(self, storage):
        result = await storage.list_personas()
        assert result == []

    @pytest.mark.anyio
    async def test_list_returns_all_personas(self, storage):
        await storage.create_persona("A", "desc", "s1")
        await storage.create_persona("B", "desc", "s2")
        result = await storage.list_personas()
        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"A", "B"}

    @pytest.mark.anyio
    async def test_delete_removes_persona(self, storage):
        p = await storage.create_persona("Del", "desc", "s")
        await storage.delete_persona(p.persona_id)
        assert await storage.get_persona(p.persona_id) is None

    @pytest.mark.anyio
    async def test_delete_nonexistent_is_silent(self, storage):
        await storage.delete_persona("persona_nope")  # should not raise

    @pytest.mark.anyio
    async def test_add_seed_photo_appends(self, storage):
        p = await storage.create_persona("Photo", "desc", "s")
        await storage.add_seed_photo(p.persona_id, "/path/to/photo.jpg")
        updated = await storage.get_persona(p.persona_id)
        assert "/path/to/photo.jpg" in updated.seed_photos

    @pytest.mark.anyio
    async def test_add_multiple_seed_photos(self, storage):
        p = await storage.create_persona("Multi", "desc", "s")
        await storage.add_seed_photo(p.persona_id, "photo1.jpg")
        await storage.add_seed_photo(p.persona_id, "photo2.jpg")
        updated = await storage.get_persona(p.persona_id)
        assert len(updated.seed_photos) == 2

    @pytest.mark.anyio
    async def test_add_seed_photo_nonexistent_raises(self, storage):
        with pytest.raises(KeyError):
            await storage.add_seed_photo("persona_nope", "photo.jpg")

    @pytest.mark.anyio
    async def test_set_lora_weights(self, storage):
        p = await storage.create_persona("LoRA", "desc", "s")
        await storage.set_lora_weights(
            p.persona_id,
            "https://weights.com/lora.safetensors",
            "sks_custom",
        )
        updated = await storage.get_persona(p.persona_id)
        assert updated.lora_weights_url == "https://weights.com/lora.safetensors"
        assert updated.trigger_word == "sks_custom"

    @pytest.mark.anyio
    async def test_set_lora_nonexistent_raises(self, storage):
        with pytest.raises(KeyError):
            await storage.set_lora_weights("persona_nope", "url", "sks_x")

    @pytest.mark.anyio
    async def test_update_persona_field(self, storage):
        p = await storage.create_persona("Upd", "old desc", "s")
        await storage.update_persona(p.persona_id, description="new desc")
        updated = await storage.get_persona(p.persona_id)
        assert updated.description == "new desc"

    @pytest.mark.anyio
    async def test_update_nonexistent_raises(self, storage):
        with pytest.raises(KeyError):
            await storage.update_persona("persona_ghost", name="Ghost")

    @pytest.mark.anyio
    async def test_concurrent_creates_are_unique(self, storage):
        tasks = [storage.create_persona(f"P{i}", "desc", "s") for i in range(5)]
        personas = await asyncio.gather(*tasks)
        ids = {p.persona_id for p in personas}
        assert len(ids) == 5

    @pytest.mark.anyio
    async def test_persistence_survives_reload(self, tmp_path):
        from app.services.block_m_common.persona_storage import PersonaStorage
        s1 = PersonaStorage(storage_dir=tmp_path / "personas")
        p = await s1.create_persona("Persist", "desc", "s")

        s2 = PersonaStorage(storage_dir=tmp_path / "personas")
        fetched = await s2.get_persona(p.persona_id)
        assert fetched is not None
        assert fetched.name == "Persist"


# ─── VideoQueue ───────────────────────────────────────────────────────────────

class TestVideoQueue:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        from app.services.block_m_common.video_queue import VideoQueue
        VideoQueue.reset()
        yield
        VideoQueue.reset()

    @pytest.fixture
    def queue(self, tmp_path):
        from app.services.block_m_common.video_queue import VideoQueue
        return VideoQueue(queue_file=tmp_path / "vq.json")

    def _job(self, job_type: str = "video", chat_id: int = 123) -> "VideoJob":
        from app.services.block_m_common.video_queue import VideoJob
        return VideoJob(
            job_id=uuid.uuid4().hex[:8],
            job_type=job_type,
            persona_id="persona_001",
            params={"prompt": "test"},
            status="pending",
            user_chat_id=chat_id,
            created_at=datetime.now(timezone.utc),
        )

    @pytest.mark.anyio
    async def test_submit_returns_job_id(self, queue):
        job = self._job()
        job_id = await queue.submit(job)
        assert job_id == job.job_id

    @pytest.mark.anyio
    async def test_get_status_returns_submitted_job(self, queue):
        job = self._job()
        await queue.submit(job)
        fetched = await queue.get_status(job.job_id)
        assert fetched is not None
        assert fetched.job_type == "video"
        assert fetched.status == "pending"

    @pytest.mark.anyio
    async def test_get_status_nonexistent_returns_none(self, queue):
        result = await queue.get_status("nonexistent_id_xyz")
        assert result is None

    @pytest.mark.anyio
    async def test_list_pending_returns_all_pending(self, queue):
        await queue.submit(self._job("video"))
        await queue.submit(self._job("lora_training"))
        pending = await queue.list_pending()
        assert len(pending) == 2

    @pytest.mark.anyio
    async def test_list_pending_excludes_done_jobs(self, queue):
        job = self._job()
        await queue.submit(job)
        await queue._mark_done(job.job_id, {"result": "ok"})
        pending = await queue.list_pending()
        assert len(pending) == 0

    @pytest.mark.anyio
    async def test_list_for_user_filters_by_chat_id(self, queue):
        await queue.submit(self._job(chat_id=111))
        await queue.submit(self._job(chat_id=222))
        await queue.submit(self._job(chat_id=111))
        user_jobs = await queue.list_for_user(111)
        assert len(user_jobs) == 2
        assert all(j.user_chat_id == 111 for j in user_jobs)

    @pytest.mark.anyio
    async def test_list_for_user_empty_result(self, queue):
        await queue.submit(self._job(chat_id=111))
        result = await queue.list_for_user(999)
        assert result == []

    @pytest.mark.anyio
    async def test_mark_running_changes_status(self, queue):
        job = self._job()
        await queue.submit(job)
        await queue._mark_running(job.job_id)
        fetched = await queue.get_status(job.job_id)
        assert fetched.status == "running"

    @pytest.mark.anyio
    async def test_mark_done_changes_status_and_result(self, queue):
        job = self._job()
        await queue.submit(job)
        await queue._mark_done(job.job_id, {"video_url": "https://v.mp4"})
        fetched = await queue.get_status(job.job_id)
        assert fetched.status == "done"
        assert fetched.result == {"video_url": "https://v.mp4"}
        assert fetched.completed_at is not None

    @pytest.mark.anyio
    async def test_mark_failed_changes_status_and_error(self, queue):
        job = self._job()
        await queue.submit(job)
        await queue._mark_failed(job.job_id, "Out of memory")
        fetched = await queue.get_status(job.job_id)
        assert fetched.status == "failed"
        assert fetched.error == "Out of memory"
        assert fetched.completed_at is not None

    @pytest.mark.anyio
    async def test_persistence_across_instances(self, tmp_path):
        from app.services.block_m_common.video_queue import VideoQueue
        qfile = tmp_path / "vq_persist.json"

        VideoQueue.reset()
        q1 = VideoQueue(queue_file=qfile)
        job = self._job()
        await q1.submit(job)

        VideoQueue.reset()
        q2 = VideoQueue(queue_file=qfile)
        fetched = await q2.get_status(job.job_id)
        assert fetched is not None
        assert fetched.job_id == job.job_id

    @pytest.mark.anyio
    async def test_singleton_returns_same_instance(self, tmp_path):
        from app.services.block_m_common.video_queue import VideoQueue
        qfile = tmp_path / "vq_s.json"
        q1 = VideoQueue(queue_file=qfile)
        q2 = VideoQueue(queue_file=qfile)
        assert q1 is q2

    @pytest.mark.anyio
    async def test_multiple_job_types(self, queue):
        await queue.submit(self._job("video"))
        await queue.submit(self._job("lora_training"))
        await queue.submit(self._job("photo_batch"))
        pending = await queue.list_pending()
        types = {j.job_type for j in pending}
        assert types == {"video", "lora_training", "photo_batch"}


# ─── CostTracker ──────────────────────────────────────────────────────────────

class TestCostTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        from app.services.block_m_common.cost_tracker import CostTracker
        return CostTracker(
            expenses_file=tmp_path / "expenses.jsonl",
            daily_limit=10.0,
        )

    @pytest.mark.anyio
    async def test_initial_daily_total_is_zero(self, tracker):
        total = await tracker.get_today_total()
        assert total == 0.0

    @pytest.mark.anyio
    async def test_log_single_expense(self, tracker):
        await tracker.log_expense("kling_video", 0.10, "persona_001")
        total = await tracker.get_today_total()
        assert total == pytest.approx(0.10)

    @pytest.mark.anyio
    async def test_log_multiple_expenses_accumulate(self, tracker):
        await tracker.log_expense("kling_video", 0.10, "persona_001")
        await tracker.log_expense("flux_inference", 0.02, "persona_002")
        await tracker.log_expense("kling_video", 0.10, "persona_001")
        total = await tracker.get_today_total()
        assert total == pytest.approx(0.22)

    @pytest.mark.anyio
    async def test_check_limit_allows_when_under(self, tracker):
        await tracker.log_expense("op", 3.0, None)
        can_proceed, remaining = await tracker.check_limit()
        assert can_proceed is True
        assert remaining == pytest.approx(7.0)

    @pytest.mark.anyio
    async def test_check_limit_always_allows_budget_retired(self, tracker):
        # money-consolidation hole b: global block_m budget RETIRED -> check_limit
        # always allows (real cap = per-user audit gate). remaining stays 0.0.
        await tracker.log_expense("op", 10.0, None)
        can_proceed, remaining = await tracker.check_limit()
        assert can_proceed is True
        assert remaining == 0.0

    @pytest.mark.anyio
    async def test_log_expense_does_not_raise_budget_retired(self, tracker):
        # Retired: log_expense must NOT raise even far over the old cap.
        await tracker.log_expense("big_op", 10.0, None)
        await tracker.log_expense("extra", 0.01, None)
        stats = await tracker.get_stats()
        assert stats["daily"] == pytest.approx(10.01)

    @pytest.mark.anyio
    async def test_log_expense_over_cap_still_appends_budget_retired(self, tracker):
        await tracker.log_expense("fill", 10.0, None)
        await tracker.log_expense("blocked_op", 1.0, None)   # appends, no raise
        stats = await tracker.get_stats()
        assert "blocked_op" in stats["by_operation"]

    @pytest.mark.anyio
    async def test_get_total_for_persona(self, tracker):
        await tracker.log_expense("op", 1.0, "persona_001")
        await tracker.log_expense("op", 2.0, "persona_002")
        await tracker.log_expense("op", 0.5, "persona_001")
        total = await tracker.get_total_for_persona("persona_001")
        assert total == pytest.approx(1.5)

    @pytest.mark.anyio
    async def test_get_total_for_persona_none_persona(self, tracker):
        await tracker.log_expense("op", 1.0, None)
        total = await tracker.get_total_for_persona("persona_001")
        assert total == 0.0

    @pytest.mark.anyio
    async def test_get_stats_structure(self, tracker):
        await tracker.log_expense("kling_video", 0.10, "persona_001")
        await tracker.log_expense("flux_inference", 0.02, "persona_002")
        stats = await tracker.get_stats()
        assert {"daily", "weekly", "monthly", "total", "by_operation"}.issubset(stats.keys())

    @pytest.mark.anyio
    async def test_get_stats_daily_total(self, tracker):
        await tracker.log_expense("kling_video", 0.10, "persona_001")
        await tracker.log_expense("flux_inference", 0.05, None)
        stats = await tracker.get_stats()
        assert stats["daily"] == pytest.approx(0.15)

    @pytest.mark.anyio
    async def test_get_stats_by_operation(self, tracker):
        await tracker.log_expense("kling_video", 0.10, None)
        await tracker.log_expense("kling_video", 0.10, None)
        await tracker.log_expense("flux_inference", 0.02, None)
        stats = await tracker.get_stats()
        assert stats["by_operation"]["kling_video"] == pytest.approx(0.20)
        assert stats["by_operation"]["flux_inference"] == pytest.approx(0.02)

    @pytest.mark.anyio
    async def test_get_stats_empty(self, tracker):
        stats = await tracker.get_stats()
        assert stats["total"] == 0.0
        assert stats["daily"] == 0.0
        assert stats["by_operation"] == {}

    @pytest.mark.anyio
    async def test_expenses_file_is_jsonl(self, tracker, tmp_path):
        await tracker.log_expense("op1", 0.10, None)
        await tracker.log_expense("op2", 0.05, None)
        lines = (tmp_path / "expenses.jsonl").read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "ts" in entry
            assert "operation" in entry
            assert "cost_usd" in entry

    @pytest.mark.anyio
    async def test_log_expense_without_persona_id(self, tracker):
        await tracker.log_expense("global_op", 1.0)  # persona_id defaults to None
        total = await tracker.get_today_total()
        assert total == pytest.approx(1.0)

    @pytest.mark.anyio
    async def test_daily_limit_constant(self):
        from app.services.block_m_common.cost_tracker import DAILY_LIMIT_USD
        assert DAILY_LIMIT_USD == 10.0

    @pytest.mark.anyio
    async def test_custom_daily_limit_also_retired(self, tmp_path):
        from app.services.block_m_common.cost_tracker import CostTracker
        tracker = CostTracker(expenses_file=tmp_path / "exp.jsonl", daily_limit=1.0)
        await tracker.log_expense("op", 1.0, None)
        # Retired: even a tiny custom limit no longer blocks.
        await tracker.log_expense("extra", 0.01, None)
        stats = await tracker.get_stats()
        assert stats["daily"] == pytest.approx(1.01)

    @pytest.mark.anyio
    async def test_check_limit_remaining_correct_midway(self, tracker):
        await tracker.log_expense("op", 4.0, None)
        can_proceed, remaining = await tracker.check_limit()
        assert can_proceed is True
        assert remaining == pytest.approx(6.0)
