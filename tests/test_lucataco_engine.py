from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.services.block_m2_face_swap.engines.lucataco_engine import (
    LucatacoSwapEngine,
)


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
