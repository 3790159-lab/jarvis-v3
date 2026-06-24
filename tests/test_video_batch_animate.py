import pytest
from pathlib import Path
from types import SimpleNamespace

from app.services.block_m2_video.batch_animate import animate_batch
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)
from app.services.block_m2_video.engines.router import EngineRouter


class _FakeEngine:
    engine_name = "fake_spicy"
    async def is_available(self): return True
    async def generate(self, request): ...


@pytest.mark.asyncio
async def test_router_spicy_returns_injected_wavespeed():
    router = EngineRouter(wavespeed=_FakeEngine())
    eng = await router.select("spicy")
    assert eng.engine_name == "fake_spicy"


class _ScriptedEngine:
    """generate() behavior driven by a per-call script keyed on request.prompt."""
    engine_name = "scripted"
    def __init__(self, script):
        self.script = script
        self.calls = {}
    async def generate(self, request):
        key = request.prompt
        self.calls[key] = self.calls.get(key, 0) + 1
        behavior = self.script[key].pop(0)
        if behavior == "ok":
            return SimpleNamespace(output_path=Path(f"/tmp/{key}.mp4"))
        if behavior == "transient":
            raise TransientVideoError("429")
        raise TerminalVideoError("failed")


def _req(prompt):
    return SimpleNamespace(prompt=prompt)


@pytest.mark.asyncio
async def test_animate_batch_isolates_and_aligns():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["terminal"], "c": ["ok"]})
    out = await animate_batch(eng, [_req("a"), _req("b"), _req("c")], concurrency=2)
    assert [p.as_posix() if p else None for p in out] == ["/tmp/a.mp4", None, "/tmp/c.mp4"]


@pytest.mark.asyncio
async def test_animate_batch_sweeps_transient_only():
    eng = _ScriptedEngine({"a": ["transient", "ok"], "b": ["terminal"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=2, sweep_pause=0.0)
    assert out[0] == Path("/tmp/a.mp4")   # recovered by sweep
    assert out[1] is None                  # terminal stays failed
    assert eng.calls["a"] == 2             # retried once (transient)
    assert eng.calls["b"] == 1             # MONEY: terminal never retried


@pytest.mark.asyncio
async def test_animate_batch_respects_cancel():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["ok"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=1, cancel_check=lambda: True)
    assert out == [None, None]             # nothing generated
    assert eng.calls == {}


@pytest.mark.asyncio
async def test_animate_batch_progress_cb_error_does_not_abort():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["ok"]})

    def bad_cb(stage, payload):
        raise RuntimeError("telegram down")

    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=2, progress_cb=bad_cb)
    assert out == [Path("/tmp/a.mp4"), Path("/tmp/b.mp4")]  # completed despite cb errors


@pytest.mark.asyncio
async def test_animate_batch_records_terminal_reason_in_errors_out():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["terminal"], "c": ["ok"]})
    errs: dict[int, str] = {}
    out = await animate_batch(
        eng, [_req("a"), _req("b"), _req("c")],
        concurrency=2, errors_out=errs,
    )
    assert out[1] is None
    assert set(errs.keys()) == {1}          # only the failed index recorded
    assert "failed" in errs[1]              # the TerminalVideoError message text
    assert 0 not in errs and 2 not in errs  # successes never recorded


@pytest.mark.asyncio
async def test_animate_batch_records_exhausted_transient_reason():
    # transient on first attempt AND on the sweep retry -> stays None, recorded
    eng = _ScriptedEngine({"a": ["transient", "transient"]})
    errs: dict[int, str] = {}
    out = await animate_batch(
        eng, [_req("a")], concurrency=1, sweep_pause=0.0, errors_out=errs,
    )
    assert out[0] is None
    assert 0 in errs
    assert "429" in errs[0]


@pytest.mark.asyncio
async def test_animate_batch_errors_out_clear_on_sweep_recovery():
    # transient then ok on sweep -> recovered, NOT left in errors_out
    eng = _ScriptedEngine({"a": ["transient", "ok"]})
    errs: dict[int, str] = {}
    out = await animate_batch(
        eng, [_req("a")], concurrency=1, sweep_pause=0.0, errors_out=errs,
    )
    assert out[0] == Path("/tmp/a.mp4")
    assert errs == {}                        # recovered -> no leftover reason


@pytest.mark.asyncio
async def test_animate_batch_errors_out_optional_backcompat():
    # default (no errors_out) keeps the list-only contract unchanged
    eng = _ScriptedEngine({"a": ["ok"], "b": ["terminal"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=2)
    assert [p.as_posix() if p else None for p in out] == ["/tmp/a.mp4", None]
