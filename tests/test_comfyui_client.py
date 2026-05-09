# -*- coding: utf-8 -*-
"""Tests for the async ComfyUI REST client."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.block_m2_video.runpod.comfyui_client import (
    ComfyUIClient,
    ComfyUIError,
    WorkflowResult,
)


def _make_response(
    status_code: int = 200,
    json_payload: dict | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", "http://comfy/")
    body: bytes
    headers = {}
    if content is not None:
        body = content
    elif json_payload is not None:
        body = json.dumps(json_payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = b""
    return httpx.Response(
        status_code=status_code, content=body, headers=headers, request=request
    )


def _patched_client(*responses) -> tuple[ComfyUIClient, AsyncMock]:
    client = ComfyUIClient(base_url="http://comfy.test")
    fake_http = MagicMock(spec=httpx.AsyncClient)
    fake_http.request = AsyncMock(side_effect=list(responses))
    fake_http.aclose = AsyncMock()
    client._http = fake_http  # noqa: SLF001
    return client, fake_http.request


# ── submit / history ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_submit_workflow_returns_prompt_id():
    client, request = _patched_client(
        _make_response(200, {"prompt_id": "abc123", "number": 1, "node_errors": {}})
    )

    prompt_id = await client.submit_workflow({"3": {"class_type": "X", "inputs": {}}})

    assert prompt_id == "abc123"
    method, path = request.await_args.args
    assert method == "POST"
    assert path == "/prompt"
    body = request.await_args.kwargs["json"]
    assert "prompt" in body
    assert body["client_id"] == client.client_id


@pytest.mark.anyio
async def test_get_history_returns_none_when_pending():
    client, _ = _patched_client(_make_response(404))

    result = await client.get_history("abc123")

    assert result is None


@pytest.mark.anyio
async def test_get_history_returns_none_for_empty_payload():
    client, _ = _patched_client(_make_response(200, {}))

    result = await client.get_history("abc123")

    assert result is None


# ── wait_for_completion ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_wait_for_completion_polls_until_ready():
    completed = {
        "abc123": {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "test_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            },
        }
    }
    client, request = _patched_client(
        _make_response(404),
        _make_response(200, {}),
        _make_response(200, completed),
    )

    result = await client.wait_for_completion(
        "abc123", timeout_sec=10, poll_interval=0.01
    )

    assert isinstance(result, WorkflowResult)
    assert result.prompt_id == "abc123"
    assert result.status == "completed"
    assert "9" in result.outputs
    assert result.outputs["9"][0].filename == "test_00001_.png"
    assert request.await_count == 3


@pytest.mark.anyio
async def test_wait_for_completion_raises_on_timeout():
    # always pending
    client = ComfyUIClient(base_url="http://comfy.test")
    fake_http = MagicMock(spec=httpx.AsyncClient)
    fake_http.request = AsyncMock(side_effect=lambda *a, **k: _make_response(200, {}))
    fake_http.aclose = AsyncMock()
    client._http = fake_http  # noqa: SLF001

    with pytest.raises(ComfyUIError) as info:
        await client.wait_for_completion(
            "abc123", timeout_sec=0, poll_interval=0.01
        )
    assert info.value.prompt_id == "abc123"


@pytest.mark.anyio
async def test_wait_for_completion_returns_failed_status():
    failed_entry = {
        "abc123": {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [["execution_error", "boom"]],
            },
            "outputs": {},
        }
    }
    client, _ = _patched_client(_make_response(200, failed_entry))

    result = await client.wait_for_completion(
        "abc123", timeout_sec=1, poll_interval=0.01
    )

    assert result.status == "failed"
    assert result.error == "boom"


# ── view / cancel ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_download_output_returns_bytes():
    payload = b"\x89PNG\r\n\x1a\nFAKEDATA"
    client, request = _patched_client(_make_response(200, content=payload))

    data = await client.download_output(
        "img_00001_.png", subfolder="sub", type_="output"
    )

    assert data == payload
    method, path = request.await_args.args
    assert method == "GET"
    assert path == "/view"
    assert request.await_args.kwargs["params"] == {
        "filename": "img_00001_.png",
        "subfolder": "sub",
        "type": "output",
    }


@pytest.mark.anyio
async def test_cancel_calls_interrupt():
    client, request = _patched_client(
        _make_response(200, {"interrupted": True}),
        _make_response(200, {"deleted": ["abc123"]}),
    )

    ok = await client.cancel("abc123")

    assert ok is True
    assert request.await_count == 2
    methods = [c.args for c in request.await_args_list]
    assert methods[0] == ("POST", "/interrupt")
    assert methods[1] == ("POST", "/queue")
    assert request.await_args_list[1].kwargs["json"] == {"delete": ["abc123"]}


# ── auth header ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_auth_token_added_to_headers():
    client = ComfyUIClient(
        base_url="http://comfy.test", auth_token="secret_bearer_token"
    )
    http = client._ensure_http()  # noqa: SLF001
    try:
        assert http.headers.get("Authorization") == "Bearer secret_bearer_token"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_no_auth_header_when_token_missing():
    client = ComfyUIClient(base_url="http://comfy.test")
    http = client._ensure_http()  # noqa: SLF001
    try:
        assert "Authorization" not in http.headers
    finally:
        await client.aclose()
