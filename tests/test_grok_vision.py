"""Веха A / Этап 1 — Grok (xAI) vision client.

A thin, OpenAI-compatible vision client pointed at api.x.ai. Mirrors the
``vision.analyze_image`` signature so it is a drop-in motion-prompt engine for
the ✨ button (Этап 2), plus multi-image support for the future video arc.

Refusal detection is NOT here — ``_refusal_layer`` (engine-agnostic) is applied
one level up in ``generate_motion_prompt``. The house-style MOTION question
stays in ``motion_prompt_ai`` and is passed in as ``question``.
"""
from __future__ import annotations

import pytest

from app.services import grok_vision
from app.services.grok_vision import (
    GrokVisionResult,
    analyze_image,
    analyze_images,
    analyze_images_detailed,
    is_grok_vision_supported,
)


# ── fake OpenAI-compatible client (no network) ───────────────────────────────

class _FakeUsage:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content, usage=None):
        self.choices = [_FakeChoice(content)]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, outer):
        self._outer = outer

    def create(self, **kwargs):
        self._outer.calls.append(kwargs)
        if self._outer.raises is not None:
            raise self._outer.raises
        return _FakeResponse(self._outer.reply, self._outer.usage)


class _FakeChat:
    def __init__(self, outer):
        self.completions = _FakeCompletions(outer)


class FakeClient:
    def __init__(self, reply="ok", usage=None, raises=None):
        self.reply = reply
        self.usage = usage
        self.raises = raises
        self.calls = []
        self.chat = _FakeChat(self)


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "frame.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKEIMAGEBYTES")
    return str(p)


def _use_fake(monkeypatch, **kw):
    fake = FakeClient(**kw)
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    monkeypatch.setattr(grok_vision, "_build_client", lambda: fake)
    return fake


# ── tests ────────────────────────────────────────────────────────────────────

def test_supported_reflects_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    assert is_grok_vision_supported() is True
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert is_grok_vision_supported() is False


def test_analyze_image_returns_text_from_response(monkeypatch, img):
    _use_fake(monkeypatch, reply="slow head turn, soft blinking, photorealistic")
    out = analyze_image(img, "describe motion")
    assert out == "slow head turn, soft blinking, photorealistic"


def test_analyze_image_sends_question_and_one_image(monkeypatch, img):
    fake = _use_fake(monkeypatch, reply="ok")
    analyze_image(img, "MY QUESTION")
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == "grok-4.3"
    content = call["messages"][0]["content"]
    texts = [b for b in content if b.get("type") == "text"]
    images = [b for b in content if b.get("type") == "image_url"]
    assert texts and texts[0]["text"] == "MY QUESTION"
    assert len(images) == 1
    # image is a base64 data-URI, never a file path or raw bytes
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_multi_image_single_request_with_n_image_urls(monkeypatch, tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.png"
        p.write_bytes(b"\x89PNG" + bytes([i]))
        paths.append(str(p))
    fake = _use_fake(monkeypatch, reply="moves across frames")
    out = analyze_images(paths, "describe movement")
    assert out == "moves across frames"
    assert len(fake.calls) == 1           # ONE request, not N
    content = fake.calls[0]["messages"][0]["content"]
    images = [b for b in content if b.get("type") == "image_url"]
    assert len(images) == 3               # all 3 frames in one content


def test_no_key_unsupported_and_no_api_call(monkeypatch, img):
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    def boom():
        raise AssertionError("_build_client must not run without a key")

    monkeypatch.setattr(grok_vision, "_build_client", boom)
    assert is_grok_vision_supported() is False
    # graceful, non-crashing empty fallback; no client built
    assert analyze_image(img, "q") == ""


def test_api_error_returns_empty_not_crash(monkeypatch, img):
    _use_fake(monkeypatch, raises=RuntimeError("xAI 503"))
    # must NOT raise — soft empty fallback
    assert analyze_image(img, "q") == ""


def test_usage_and_cost_extracted(monkeypatch, img):
    usage = _FakeUsage(
        prompt_tokens=4120, completion_tokens=22, total_tokens=4142,
        cost_in_usd_ticks=57_181_000,
    )
    _use_fake(monkeypatch, reply="a prompt, photorealistic", usage=usage)
    res = analyze_images_detailed([img], "q")
    assert isinstance(res, GrokVisionResult)
    assert res.text == "a prompt, photorealistic"
    assert res.usage is not None
    assert res.usage["prompt_tokens"] == 4120
    assert res.usage["cost_in_usd_ticks"] == 57_181_000
    # ticks are nano-USD (1e9 ticks == $1): 57_181_000 -> $0.057181
    assert res.cost_usd == pytest.approx(0.057181)


def test_missing_file_raises_valueerror(monkeypatch):
    _use_fake(monkeypatch, reply="ok")
    with pytest.raises(ValueError):
        analyze_image("/no/such/frame.png", "q")
