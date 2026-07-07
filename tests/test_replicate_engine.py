# -*- coding: utf-8 -*-
"""Tests for the Replicate Phase A wan engine.

Post-fix the engine talks raw ``httpx`` to ``api.replicate.com`` (submit →
poll → download), exactly like ``ReplicateSeedanceEngine`` — it MUST NOT
import the ``replicate`` SDK (pydantic-v1 → ConfigError under Python 3.14).
All HTTP is driven through injected ``httpx.MockTransport``.
"""
from __future__ import annotations

import ast
import importlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TerminalVideoError,
    TransientVideoError,
)
from app.services.block_m2_video.engines.replicate_engine import (
    REPLICATE_MODELS,
    ReplicateEngine,
    ReplicateEngineError,
    ReplicateEngineTransientError,
)

_ENGINE_MOD = "app.services.block_m2_video.engines.replicate_engine"
_ROUTER_MOD = "app.services.block_m2_video.engines.router"


# ── helpers ──────────────────────────────────────────────────────────────────


def _img(tmp_path: Path) -> Path:
    p = tmp_path / "src.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return p


def _engine(handler, dl_handler=None, **kw) -> ReplicateEngine:
    return ReplicateEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(
            dl_handler or (lambda r: httpx.Response(200, content=b"MP4"))
        ),
        backoff_base=0.0,
        **kw,
    )


def _req(tmp_path: Path, **kw) -> VideoRequest:
    data = dict(
        persona_id="p",
        persona_name="b",
        input_image_path=_img(tmp_path),
        prompt="move",
        seconds=5,
        seed=42,
    )
    data.update(kw)
    return VideoRequest(**data)


# ── A. import teeth (the check the pydantic downgrade lacked) ─────────────────


def test_module_does_not_import_replicate_sdk():
    """Static AST scan: no ``import replicate`` / ``from replicate import``.

    Matches the exact top-level module ``replicate`` (not the substring in
    ``replicate_engine`` / ``replicate_seedance_engine``). Mutation: re-adding
    ``import replicate`` → this fails.
    """
    spec = importlib.util.find_spec(_ENGINE_MOD)
    assert spec and spec.origin
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                a.name for a in node.names
                if a.name == "replicate" or a.name.startswith("replicate.")
            ]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "replicate" or mod.startswith("replicate."):
                offenders.append(mod)
    assert not offenders, f"engine must not import the replicate SDK: {offenders}"


def test_router_imports_without_replicate_sdk(monkeypatch):
    """Reproduces the prod crash: importing the router must NOT drag in the
    ``replicate`` SDK. We ban ``replicate`` at the import machinery, evict the
    cached engine modules, and re-import the router fresh.

    Mutation: re-adding ``import replicate`` to the engine → ImportError → fails.
    """
    monkeypatch.setenv("REPLICATE_API_TOKEN", "t")

    class _BlockReplicate(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "replicate" or name.startswith("replicate."):
                raise ImportError("replicate SDK is banned on the animation path")
            return None

    blocker = _BlockReplicate()
    saved = dict(sys.modules)

    def _evict():
        for m in list(sys.modules):
            if (
                m == "replicate"
                or m.startswith("replicate.")
                or "block_m2_video.engines" in m
            ):
                sys.modules.pop(m, None)

    _evict()
    sys.meta_path.insert(0, blocker)
    try:
        router = importlib.import_module(_ROUTER_MOD)
        assert hasattr(router, "EngineRouter")
        eng_mod = importlib.import_module(_ENGINE_MOD)
        eng = eng_mod.ReplicateEngine()
        assert eng.engine_name == "replicate"
    finally:
        sys.meta_path.remove(blocker)
        _evict()
        sys.modules.update({k: v for k, v in saved.items() if k not in sys.modules})


def test_live_smoke_import_no_mocks():
    """Live smoke under the running interpreter (3.14), zero mocks: the router
    and engine modules import and the engine constructs. This is the always-on
    real-interpreter check that the pydantic downgrade slipped past.
    """
    router = importlib.import_module(_ROUTER_MOD)
    eng_mod = importlib.import_module(_ENGINE_MOD)
    assert router.EngineRouter
    eng = eng_mod.ReplicateEngine(api_token="smoke")
    assert eng.engine_name == "replicate"


# ── B. error taxonomy (money-safety parity with Seedance) ────────────────────


def test_errors_subclass_shared_bases():
    assert issubclass(ReplicateEngineError, TerminalVideoError)
    assert issubclass(ReplicateEngineTransientError, TransientVideoError)


# ── C. behavior parity (submit → poll → download over MockTransport) ─────────


@pytest.mark.asyncio
async def test_generate_success_and_cost(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["input"]["image"].startswith("data:")  # data-URI, not a file handle
            assert body["input"]["prompt"] == "move"
            assert body["input"]["duration"] == 5
            assert body["input"]["seed"] == 42
            # official-model endpoint for the default (wan-2.2)
            assert req.url.path == "/v1/models/wan-video/wan-2.2-i2v-fast/predictions"
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "succeeded", "output": "https://cdn/x.mp4"})

    eng = _engine(handler)
    res = await eng.generate(_req(tmp_path))
    assert res.output_path.exists()
    assert res.output_path.read_bytes() == b"MP4"
    assert res.cost_usd == pytest.approx(0.060 * 5)  # wan-2.2 rate
    assert res.engine == "replicate"
    assert res.model == "wan-video/wan-2.2-i2v-fast"
    assert res.seed == 42
    assert res.extra["video_url"] == "https://cdn/x.mp4"


@pytest.mark.asyncio
async def test_generate_accepts_list_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "succeeded", "output": ["https://cdn/list.mp4"]})

    eng = _engine(handler)
    res = await eng.generate(_req(tmp_path))
    assert res.extra["video_url"].endswith("list.mp4")


@pytest.mark.asyncio
async def test_output_path_under_state_personas_videos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "succeeded", "output": "https://cdn/x.mp4"})

    eng = _engine(handler)
    res = await eng.generate(_req(tmp_path, persona_id="persona_zzz"))
    expected = Path("state/personas/videos") / "persona_zzz" / res.generation_id / "output.mp4"
    assert res.output_path == expected
    assert res.output_path.exists()


@pytest.mark.asyncio
async def test_429_exhausted_is_transient(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate"})

    eng = _engine(handler, max_retries=2)
    with pytest.raises(ReplicateEngineTransientError):
        await eng.generate(_req(tmp_path))


@pytest.mark.asyncio
async def test_4xx_terminal_not_retried(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    posts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        return httpx.Response(422, json={"detail": "bad"})

    eng = _engine(handler, max_retries=5)
    with pytest.raises(ReplicateEngineError):
        await eng.generate(_req(tmp_path))
    assert posts["n"] == 1  # MONEY: 4xx (≠429) is billable/deterministic → never retried


@pytest.mark.asyncio
async def test_failed_status_terminal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})

    eng = _engine(handler)
    with pytest.raises(ReplicateEngineError):
        await eng.generate(_req(tmp_path))


@pytest.mark.asyncio
async def test_missing_input_image_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = _engine(lambda r: httpx.Response(500))
    req = _req(tmp_path, input_image_path=tmp_path / "nope.png")
    with pytest.raises(ReplicateEngineError):
        await eng.generate(req)


# ── D. construction / availability ───────────────────────────────────────────


def test_missing_api_token_raises(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    with pytest.raises(ReplicateEngineError):
        ReplicateEngine()


@pytest.mark.asyncio
async def test_is_available_true_when_token_set():
    eng = ReplicateEngine(api_token="t")
    assert await eng.is_available() is True


# ── E. default-model tooth (mutation both directions) ────────────────────────


def test_default_model_is_wan22():
    assert REPLICATE_MODELS[0]["id"] == "wan-video/wan-2.2-i2v-fast"
    eng = ReplicateEngine(api_token="t")
    assert eng.model_id == "wan-video/wan-2.2-i2v-fast"
    assert eng.cost_per_sec == pytest.approx(0.060)


def test_wan25_still_selectable():
    eng = ReplicateEngine(api_token="t", model_id="wan-video/wan-2.5-i2v-fast")
    assert eng.model_id == "wan-video/wan-2.5-i2v-fast"
    assert eng.cost_per_sec == pytest.approx(0.020)
