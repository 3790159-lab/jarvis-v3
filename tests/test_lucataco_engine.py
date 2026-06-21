from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.services.block_m2_face_swap.engines.lucataco_engine import (
    LucatacoSwapEngine,
)
from app.services.block_m_common.faceswap_client import PredictionFailed


def _make_jpg(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, (123, 50, 200)).save(path, "JPEG")
    return path


@pytest.mark.asyncio
async def test_swap_batch_aligns_results_and_marks_no_face(tmp_path):
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")
    t1 = _make_jpg(tmp_path / "1.jpg")

    # MockTransport: target "0" succeeds with a URL, target "1" gets no-face.
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["n"] += 1
            return httpx.Response(201, json={"id": f"p{state['n']}"})
        # the prediction id tells us which target; odd->url, even->None
        pid = request.url.path.rsplit("/", 1)[-1]
        if pid == "p1":
            return httpx.Response(200, json={"status": "succeeded", "output": "https://img/ok.jpg"})
        return httpx.Response(200, json={"status": "succeeded", "output": None})

    # The result download also goes through the same transport.
    def dl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0FAKEJPEG")

    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler),
        concurrency=2,
    )
    out = await engine.swap_batch(src, [t0, t1])
    assert len(out) == 2
    assert out[0] is not None and out[0].exists()  # downloaded
    assert out[1] is None  # no-face → failure
    assert engine.cost_per_swap_usd == pytest.approx(0.005)


@pytest.mark.asyncio
async def test_prediction_failed_does_not_abort_batch(tmp_path):
    """A PredictionFailed for one target must not abort the remaining targets."""
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")
    t1 = _make_jpg(tmp_path / "1.jpg")

    # target 0 → prediction fails; target 1 → succeeds with a URL
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["n"] += 1
            return httpx.Response(201, json={"id": f"p{state['n']}"})
        pid = request.url.path.rsplit("/", 1)[-1]
        if pid == "p1":
            # target 0's prediction failed
            return httpx.Response(200, json={"status": "failed", "error": "no face detected"})
        # target 1's prediction succeeded
        return httpx.Response(200, json={"status": "succeeded", "output": "https://img/ok2.jpg"})

    def dl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0FAKEJPEG")

    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler),
        concurrency=2,
    )
    out = await engine.swap_batch(src, [t0, t1])
    assert len(out) == 2
    assert out[0] is None                           # failed prediction → None
    assert out[1] is not None and out[1].exists()   # succeeded → downloaded


# ---------------------------------------------------------------------------
# NEW: Rate-limit hardening tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transient_is_retried_and_recovers(tmp_path):
    """MONEY: A target whose first-pass submits all 429 (LucatacoTransientError) must
    be re-submitted in the retry sweep and the recovered result returned.

    The mock returns 429 for the first POST and 201+succeeded for the second POST.
    We use max_retries=1 inside the client so the single 429 exhausts retries and
    raises LucatacoTransientError — simulating a sustained 429 burst.
    """
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")

    post_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_calls["n"] += 1
            if post_calls["n"] == 1:
                # First pass: return 429 → will exhaust retries → LucatacoTransientError
                return httpx.Response(429, json={"detail": "rate limited"})
            # Retry sweep: succeed
            return httpx.Response(201, json={"id": "psweep"})
        # Poll for sweep prediction
        return httpx.Response(200, json={"status": "succeeded", "output": "https://img/sweep.jpg"})

    def dl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0FAKEJPEG")

    # Monkey-patch the client to use max_retries=1 so a single 429 exhausts
    # retries quickly (keeps the test fast without real sleep).
    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler),
        concurrency=1,
    )
    # Patch _run_prediction to use max_retries=1
    original_run = engine._client._run_prediction

    async def fast_run(payload, max_retries=8):
        return await original_run(payload, max_retries=1)

    engine._client._run_prediction = fast_run  # type: ignore[method-assign]

    out = await engine.swap_batch(src, [t0])
    assert len(out) == 1
    assert out[0] is not None and out[0].exists(), (
        "Transient (429-exhausted) target should recover in retry sweep"
    )
    # Sweep must have submitted a second POST (post_calls["n"] == 2)
    assert post_calls["n"] == 2, (
        f"Expected 2 POST submissions (first pass + sweep), got {post_calls['n']}"
    )


@pytest.mark.asyncio
async def test_no_face_is_not_retried_money(tmp_path):
    """MONEY: succeeded+output=None (NO_FACE) must stay None and be submitted EXACTLY ONCE.

    Re-submitting a NO_FACE result re-bills the account (we already paid for the
    prediction that returned no face). The sweep must never touch this target.
    """
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")

    post_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_calls["n"] += 1
            return httpx.Response(201, json={"id": f"pnf{post_calls['n']}"})
        # All polls: succeeded with output=None (no face)
        return httpx.Response(200, json={"status": "succeeded", "output": None})

    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")),
        concurrency=1,
    )
    out = await engine.swap_batch(src, [t0])
    assert len(out) == 1
    assert out[0] is None, "NO_FACE (succeeded+output=None) must map to None"
    assert post_calls["n"] == 1, (
        f"NO_FACE must be submitted EXACTLY ONCE (no sweep re-submit). "
        f"Got {post_calls['n']} POST(s) — re-submitting would re-bill the account."
    )


@pytest.mark.asyncio
async def test_prediction_failed_is_not_retried_money(tmp_path):
    """MONEY: PredictionFailed (status=failed) must stay None and be submitted EXACTLY ONCE.

    Re-submitting a failed prediction re-bills the account. The sweep must never
    touch targets whose predictions returned status=failed or status=canceled.
    """
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")

    post_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_calls["n"] += 1
            return httpx.Response(201, json={"id": f"ppf{post_calls['n']}"})
        # All polls: prediction failed
        return httpx.Response(200, json={"status": "failed", "error": "internal error"})

    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")),
        concurrency=1,
    )
    out = await engine.swap_batch(src, [t0])
    assert len(out) == 1
    assert out[0] is None, "PredictionFailed must map to None"
    assert post_calls["n"] == 1, (
        f"PredictionFailed must be submitted EXACTLY ONCE (no sweep re-submit). "
        f"Got {post_calls['n']} POST(s) — re-submitting would re-bill the account."
    )
