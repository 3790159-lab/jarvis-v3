# -*- coding: utf-8 -*-
"""Vizir Hermes StepHandler — Phase M (mocks, $0, TDD).

Hermes is a multi-step agent that runs FREELY inside ONE Vizir step (up to
max_iterations), under Vizir's frame: cost-cap + timeout + acceptance held by the
Coordinator/Vizir above. The handler bridges Hermes's per-iteration session
counters to the proven ctx callbacks (report_cost reserve-before-spend +
report_progress). ``run_fn`` is injected so these tests never import/run real
Hermes and spend $0 (mirrors handlers_grok's ``analyze_fn`` seam)."""
import asyncio

from app.services.vizir.models import Step
from app.services.vizir.handlers_hermes import make_hermes_handler, _derive_provider


def test_derive_provider_maps_model_to_native_provider():
    # cost pricing only resolves with an explicit provider -> must never be blind
    assert _derive_provider("claude-sonnet-4-6") == "anthropic"
    assert _derive_provider("anthropic/claude-opus-4-8") == "anthropic"
    assert _derive_provider("grok-4.3") == "xai"
    assert _derive_provider("xai/grok-4") == "xai"
    assert _derive_provider("some-unknown-model") is None


def _run(coro):
    return asyncio.run(coro)


def _fake_run(*, final_response="<html></html>", cost_usd=0.03, iterations=4,
              stopped_reason="completed", tokens=1234, steps_costs=None,
              artifact_path=None, capture=None):
    """Build a fake run_fn(prompt, *, model, enabled_toolsets, disabled_toolsets,
    max_iterations, on_step) -> dict. Optionally streams per-iteration cumulative
    cost via on_step (steps_costs) and records the call kwargs into ``capture``."""
    def run_fn(prompt, *, model, enabled_toolsets, disabled_toolsets,
               max_iterations, on_step):
        if capture is not None:
            capture.update(dict(prompt=prompt, model=model,
                                enabled_toolsets=enabled_toolsets,
                                disabled_toolsets=disabled_toolsets,
                                max_iterations=max_iterations))
        for cum in (steps_costs or []):
            on_step(cum, note=f"iteration cumulative ${cum:.4f}")
        return dict(final_response=final_response, cost_usd=cost_usd,
                    iterations=iterations, stopped_reason=stopped_reason,
                    tokens=tokens, artifact_path=artifact_path)
    return run_fn


def test_handler_normalizes_successful_run_result():
    handler = make_hermes_handler(run_fn=_fake_run(
        final_response="<html><body>chat</body></html>", cost_usd=0.042,
        iterations=7, stopped_reason="completed", tokens=2048,
        artifact_path="/tmp/jarvis_chat.html"))
    step = Step(kind="hermes", params={"prompt": "build a chat"}, estimated_usd=0.10)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is True
    assert abs(res.cost_usd - 0.042) < 1e-9
    assert res.result["final_response"] == "<html><body>chat</body></html>"
    assert res.result["iterations"] == 7
    assert res.result["stopped_reason"] == "completed"
    assert res.result["tokens"] == 2048
    assert res.result["artifact_path"] == "/tmp/jarvis_chat.html"


def test_empty_output_is_not_charged():
    handler = make_hermes_handler(run_fn=_fake_run(final_response="", cost_usd=0.0,
                                                   artifact_path=None))
    step = Step(kind="hermes", params={"prompt": "build a chat"}, estimated_usd=0.10)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is False          # nothing produced -> not charged (refusal не списан)
    assert res.error


def test_report_cost_and_progress_emitted_each_iteration():
    # Hermes streams cumulative cost after each of 3 iterations: 0.01, 0.03, 0.06
    handler = make_hermes_handler(run_fn=_fake_run(
        steps_costs=[0.01, 0.03, 0.06], cost_usd=0.06))
    costs, notes = [], []
    ctx = {"task": None, "results": {},
           "report_cost": lambda a: costs.append(a),
           "report_progress": lambda n: notes.append(n)}
    res = _run(handler(Step(kind="hermes", params={"prompt": "x"}), ctx))

    assert res.ok is True
    # DELTAS reported (reserve-before-next-iteration), not cumulative
    assert [round(c, 4) for c in costs] == [0.01, 0.02, 0.03]
    assert len(notes) == 3                               # one progress note per iteration


def test_toolset_lockdown_and_default_engine_passed_to_hermes():
    cap = {}
    handler = make_hermes_handler(run_fn=_fake_run(capture=cap,
                                                   final_response="<html></html>"))
    _run(handler(Step(kind="hermes", params={"prompt": "x"}), {"task": None, "results": {}}))

    # default engine = Claude (stronger at code); dangerous toolsets locked out
    assert cap["model"] == "claude-sonnet-4-6"
    for forbidden in ("terminal", "code_execution", "delegation"):
        assert forbidden in cap["disabled_toolsets"]
        assert forbidden not in cap["enabled_toolsets"]
    # multi-step power preserved: a real iteration ceiling is passed through
    assert cap["max_iterations"] >= 1


def test_handler_awaits_async_run_fn():
    # the real subprocess adapter is async -> the handler must await it
    async def async_run(prompt, *, model, enabled_toolsets, disabled_toolsets,
                        max_iterations, on_step):
        on_step(0.01, note="i1")
        return dict(final_response="<html>async</html>", cost_usd=0.01,
                    iterations=1, stopped_reason="completed", tokens=5)

    handler = make_hermes_handler(run_fn=async_run)
    res = _run(handler(Step(kind="hermes", params={"prompt": "x"}),
                       {"task": None, "results": {}}))
    assert res.ok is True
    assert res.result["final_response"] == "<html>async</html>"
    assert abs(res.cost_usd - 0.01) < 1e-9


def test_step_params_override_toolsets_and_model():
    cap = {}
    handler = make_hermes_handler(run_fn=_fake_run(capture=cap,
                                                   final_response="<html></html>"))
    step = Step(kind="hermes", params={
        "prompt": "x", "model": "anthropic/claude-sonnet-4-6",
        "enabled_toolsets": ["file"], "max_iterations": 12})
    _run(handler(step, {"task": None, "results": {}}))

    assert cap["model"] == "anthropic/claude-sonnet-4-6"
    assert cap["enabled_toolsets"] == ["file"]
    assert cap["max_iterations"] == 12
