"""Phase 38: Tests for Replicate FLUX 1.1 Pro image generation + fallback chain."""
from __future__ import annotations

import json
import sys
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.replicate_image_gen import (
    generate_images_replicate,
    is_replicate_configured,
    _poll_prediction,
    _FLUX_MODEL_URL,
    _PREDICTIONS_BASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_mock(responses: list):
    """Build a side_effect list of context-manager mocks for urlopen."""
    mocks = []
    for body in responses:
        m = MagicMock()
        m.__enter__ = lambda s, b=body: MagicMock(read=lambda: json.dumps(b).encode())
        m.__exit__ = MagicMock(return_value=False)
        mocks.append(m)
    return mocks


# ---------------------------------------------------------------------------
# is_replicate_configured
# ---------------------------------------------------------------------------

class TestIsReplicateConfigured:
    def test_returns_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        assert is_replicate_configured() is False

    def test_returns_false_when_empty(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "   ")
        assert is_replicate_configured() is False

    def test_returns_true_when_key_present(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_abc123")
        assert is_replicate_configured() is True


# ---------------------------------------------------------------------------
# generate_images_replicate — error cases
# ---------------------------------------------------------------------------

class TestGenerateImagesErrors:
    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="REPLICATE_API_KEY not set"):
            generate_images_replicate("test prompt")

    def test_raises_when_empty_api_key(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "")
        with pytest.raises(ValueError):
            generate_images_replicate("test prompt")


# ---------------------------------------------------------------------------
# _poll_prediction
# ---------------------------------------------------------------------------

class TestPollPrediction:
    def test_returns_url_on_success_list_output(self, monkeypatch):
        status_body = {"status": "succeeded", "output": ["https://example.com/img1.jpg"]}
        ctx = MagicMock()
        ctx.read.return_value = json.dumps(status_body).encode()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", return_value=cm):
            result = _poll_prediction("pred_123", "r8_key")

        assert result == "https://example.com/img1.jpg"

    def test_returns_url_on_success_string_output(self, monkeypatch):
        status_body = {"status": "succeeded", "output": "https://example.com/img2.jpg"}
        ctx = MagicMock()
        ctx.read.return_value = json.dumps(status_body).encode()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", return_value=cm):
            result = _poll_prediction("pred_456", "r8_key")

        assert result == "https://example.com/img2.jpg"

    def test_raises_on_failed_status(self, monkeypatch):
        status_body = {"status": "failed", "error": "out of memory"}
        ctx = MagicMock()
        ctx.read.return_value = json.dumps(status_body).encode()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", return_value=cm):
            with pytest.raises(RuntimeError, match="out of memory"):
                _poll_prediction("pred_fail", "r8_key")

    def test_raises_on_canceled_status(self, monkeypatch):
        status_body = {"status": "canceled", "error": None}
        ctx = MagicMock()
        ctx.read.return_value = json.dumps(status_body).encode()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", return_value=cm):
            with pytest.raises(RuntimeError, match="canceled"):
                _poll_prediction("pred_cancel", "r8_key")

    def test_raises_on_timeout(self, monkeypatch):
        status_body = {"status": "processing"}
        ctx = MagicMock()
        ctx.read.return_value = json.dumps(status_body).encode()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", return_value=cm):
            with pytest.raises(TimeoutError):
                _poll_prediction("pred_timeout", "r8_key", max_wait=2)

    def test_polls_until_succeeded(self, monkeypatch):
        bodies = [
            {"status": "starting"},
            {"status": "processing"},
            {"status": "succeeded", "output": ["https://img.example.com/final.jpg"]},
        ]
        call_count = 0

        def fake_urlopen(req, timeout=10):
            nonlocal call_count
            body = bodies[min(call_count, len(bodies) - 1)]
            call_count += 1
            ctx = MagicMock()
            ctx.read.return_value = json.dumps(body).encode()
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=ctx)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("app.services.replicate_image_gen.time.sleep"), \
             patch("app.services.replicate_image_gen.urllib.request.urlopen", side_effect=fake_urlopen):
            result = _poll_prediction("pred_multi", "r8_key")

        assert result == "https://img.example.com/final.jpg"
        assert call_count == 3


# ---------------------------------------------------------------------------
# Replicate router
# ---------------------------------------------------------------------------

class TestReplicateApiUrl:
    """Phase H1.2: Verify correct model-level URL is used (not /v1/predictions)."""

    def test_uses_model_url_not_predictions_url(self):
        assert "models/black-forest-labs/flux-1.1-pro/predictions" in _FLUX_MODEL_URL

    def test_model_url_different_from_predictions_base(self):
        assert _FLUX_MODEL_URL != _PREDICTIONS_BASE

    def test_no_version_field_in_payload(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        submitted_payloads = []

        def fake_urlopen(req, timeout=30):
            body = req.data
            if body:
                submitted_payloads.append(json.loads(body))
            # Return a prediction id so we can check submit phase
            ctx = MagicMock()
            ctx.read.return_value = json.dumps({"id": "pred_test_xyz"}).encode()
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=ctx)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("app.services.replicate_image_gen.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("app.services.replicate_image_gen._poll_prediction", return_value="https://img.com/1.jpg"):
            generate_images_replicate("a cat", 1, "9:16", "realistic")

        assert submitted_payloads, "Should have submitted a payload"
        payload = submitted_payloads[0]
        assert "version" not in payload, "Should NOT send version field for model-level URL"
        assert "input" in payload

    def test_uses_model_level_url(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        used_urls = []

        def fake_urlopen(req, timeout=30):
            used_urls.append(req.full_url)
            ctx = MagicMock()
            ctx.read.return_value = json.dumps({"id": "pred_url_check"}).encode()
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=ctx)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("app.services.replicate_image_gen.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("app.services.replicate_image_gen._poll_prediction", return_value="https://img.com/1.jpg"):
            generate_images_replicate("abstract colorful pattern", 1, "9:16", "realistic")

        assert used_urls, "urlopen should be called"
        assert "models/black-forest-labs/flux-1.1-pro/predictions" in used_urls[0]

    def test_http_error_raises_with_body(self, monkeypatch):
        import urllib.error
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")

        err_body = b'{"detail": "Model not found"}'
        http_err = urllib.error.HTTPError(
            url="https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions",
            code=422,
            msg="Unprocessable Entity",
            hdrs=None,
            fp=MagicMock(read=lambda: err_body),
        )
        with patch("app.services.replicate_image_gen.urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(RuntimeError, match="422"):
                generate_images_replicate("test", 1, "9:16", "realistic")


class TestReplicateRouter:
    def test_health_endpoint_not_configured(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        from app.routers.replicate_image_router import image_gen_health
        result = image_gen_health()
        assert result["status"] == "ok"
        assert result["replicate_configured"] is False

    def test_health_endpoint_configured(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        from app.routers.replicate_image_router import image_gen_health
        result = image_gen_health()
        assert result["replicate_configured"] is True

    def test_generate_raises_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        from app.routers.replicate_image_router import _try_generate
        with pytest.raises(RuntimeError, match="REPLICATE_API_KEY"):
            _try_generate("a cat", 1, "9:16", "realistic")

    def test_try_generate_calls_replicate(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        with patch("app.services.replicate_image_gen.generate_images_replicate",
                   return_value=["https://img.com/1.jpg"]) as mock_gen:
            from app.routers.replicate_image_router import _try_generate
            urls, provider = _try_generate("mountains at sunset", 1, "9:16", "realistic")

        assert urls == ["https://img.com/1.jpg"]
        assert provider == "replicate"

    def test_try_generate_raises_on_replicate_error(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        with patch("app.services.replicate_image_gen.generate_images_replicate",
                   side_effect=RuntimeError("network error")):
            from app.routers.replicate_image_router import _try_generate
            with pytest.raises(RuntimeError, match="провайдеры"):
                _try_generate("test", 1, "9:16", "realistic")


# ---------------------------------------------------------------------------
# Bot generate intent — _send_photo_url exists
# ---------------------------------------------------------------------------

class TestBotGenerateHelpers:
    def test_send_photo_url_function_exists(self):
        from tools.jarvis_smart_telegram_control import _send_photo_url
        assert callable(_send_photo_url)

    def test_send_photo_url_calls_tg_sendPhoto(self):
        with patch("tools.jarvis_smart_telegram_control.tg_call") as mock_tg:
            from tools.jarvis_smart_telegram_control import _send_photo_url
            _send_photo_url("12345", "https://img.com/photo.jpg", "test caption")
        mock_tg.assert_called_once()
        args = mock_tg.call_args[0]
        assert args[0] == "sendPhoto"
        assert args[1]["photo"] == "https://img.com/photo.jpg"
        assert args[1]["chat_id"] == "12345"

    def test_send_photo_url_truncates_long_caption(self):
        long_caption = "x" * 2000
        with patch("tools.jarvis_smart_telegram_control.tg_call") as mock_tg:
            from tools.jarvis_smart_telegram_control import _send_photo_url
            _send_photo_url("123", "https://img.com/1.jpg", long_caption)
        payload = mock_tg.call_args[0][1]
        assert len(payload["caption"]) <= 1024


# ---------------------------------------------------------------------------
# Phase H2: Smart model selection
# ---------------------------------------------------------------------------

class TestSmartModelSelection:
    def test_people_uses_ultra_model(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("Сделай фото девушки")
        assert "flux-1.1-pro-ultra" in url

    def test_man_uses_ultra(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("portrait of a man")
        assert "flux-1.1-pro-ultra" in url

    def test_landscape_uses_ultra(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("горный пейзаж")
        assert "flux-1.1-pro-ultra" in url

    def test_default_uses_pro(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("красивый кот")
        assert "flux-1.1-pro/predictions" in url
        assert "ultra" not in url

    def test_woman_uses_ultra(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("beautiful woman smiling")
        assert "flux-1.1-pro-ultra" in url

    def test_city_uses_ultra(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("night city street")
        assert "flux-1.1-pro-ultra" in url

    def test_forest_uses_ultra(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("лес осенью")
        assert "flux-1.1-pro-ultra" in url

    def test_abstract_uses_default(self):
        from app.services.replicate_image_gen import _select_model
        url = _select_model("abstract colorful shapes")
        assert "ultra" not in url


# ---------------------------------------------------------------------------
# Phase H2: Prompt enhancement
# ---------------------------------------------------------------------------

class TestPromptEnhancement:
    def test_people_prompt_has_photorealistic_terms(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("девушка на пляже")
        assert "photorealistic" in result
        assert "Canon" in result or "lens" in result
        assert "DSLR quality" in result  # позитивний фото-якір (не інлайн-негатив)

    def test_object_prompt_has_realistic_terms(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("красивый кот")
        assert "photorealistic" in result
        assert "real photo" in result  # позитивний якір замість "no illustration"

    def test_people_prompt_includes_original(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("портрет мужчины")
        assert "портрет мужчины" in result

    def test_default_prompt_includes_original(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("синий цветок")
        assert "синий цветок" in result

    def test_people_prompt_has_dslr_quality(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("girl on beach")
        assert "DSLR quality" in result

    def test_people_prompt_has_no_inline_negatives(self):
        """FLUX (1.1 Pro) не має negative_prompt; інлайн "no anime/illustration/cgi"
        у позитивному промпті ПРИЗИВАЄ те, що забороняє (той самий бекфайр, що
        доведено на persona-шляху 2026-07-12). Тільки позитивні фото-якорі."""
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("woman portrait")
        assert "no anime" not in result
        assert "no illustration" not in result
        assert "no cgi" not in result
        assert "natural lighting" in result  # позитивні якорі лишаються

    def test_default_prompt_has_no_inline_negatives(self):
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("синий цветок")
        assert "no illustration" not in result
        assert "no anime" not in result

    def test_visual_style_forbidding_photorealistic_people_skips_anchors_uk(self):
        """nopersona-шлях мержить topic+visual_style в один текст ДО _enhance_prompt
        (див. _ig_gen_photo_prompt) — коли visual_style явно забороняє
        фотореалістичних людей (напр. clients/vera_ai_ua/brand.md), анкори
        photorealistic/DSLR/hyperrealistic-skin НЕ повинні дописуватись, навіть
        якщо тема поста згадує людину/портрет."""
        from app.services.replicate_image_gen import _enhance_prompt
        prompt = (
            "портрет команди, генеративна естетика ШІ-аватара Віри: "
            "яскраві акценти, чисті кадри, без кітчу і без фотореалістичних людей"
        )
        result = _enhance_prompt(prompt)
        assert result == prompt
        assert "DSLR quality" not in result
        assert "hyperrealistic skin texture" not in result
        assert "shot on Canon EOS R5" not in result

    def test_visual_style_forbidding_photorealistic_people_skips_anchors_en(self):
        from app.services.replicate_image_gen import _enhance_prompt
        prompt = "team update post, no photorealistic people in the visuals"
        result = _enhance_prompt(prompt)
        assert result == prompt
        assert "DSLR quality" not in result
        assert "hyperrealistic skin texture" not in result

    def test_visual_style_without_forbid_marker_still_gets_anchors(self):
        """Регрес: звичайний бренд без заборони й далі отримує анкори як раніше."""
        from app.services.replicate_image_gen import _enhance_prompt
        result = _enhance_prompt("портрет дівчини, реалізм, тепле світло")
        assert "DSLR quality" in result
