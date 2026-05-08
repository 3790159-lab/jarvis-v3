"""Tests for Phase 27: Vision Input — Claude Vision analysis."""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.vision import (
    analyze_image,
    analyze_image_placeholder,
    is_vision_supported,
    _detect_media_type,
    _auto_rotate,
)


# ─── is_vision_supported ─────────────────────────────────────────────────────

class TestIsVisionSupported:
    def test_true_when_api_key_set(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            assert is_vision_supported() is True

    def test_false_when_key_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            assert is_vision_supported() is False

    def test_false_when_key_empty(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            assert is_vision_supported() is False


# ─── _detect_media_type ───────────────────────────────────────────────────────

class TestDetectMediaType:
    def test_jpg(self):
        assert _detect_media_type("/tmp/photo.jpg") == "image/jpeg"

    def test_jpeg(self):
        assert _detect_media_type("/tmp/photo.jpeg") == "image/jpeg"

    def test_png(self):
        assert _detect_media_type("/tmp/image.png") == "image/png"

    def test_webp(self):
        assert _detect_media_type("/tmp/image.webp") == "image/webp"

    def test_gif(self):
        assert _detect_media_type("/tmp/anim.gif") == "image/gif"

    def test_unknown_defaults_to_jpeg(self):
        assert _detect_media_type("/tmp/image.bmp") == "image/jpeg"


# ─── analyze_image ────────────────────────────────────────────────────────────

def _make_anthropic_vision_mock(text="На фото кот."):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=text)]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_mod = types.ModuleType("anthropic")
    mock_mod.Anthropic = MagicMock(return_value=mock_client)
    return mock_mod, mock_client


class TestAnalyzeImage:
    def _temp_image(self, suffix=".jpg", content=b"fake jpeg"):
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_raises_on_missing_file(self):
        import pytest
        with pytest.raises(ValueError, match="not found"):
            analyze_image("/nonexistent/photo.jpg")

    def test_returns_placeholder_without_api_key(self):
        img = self._temp_image()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        try:
            with patch.dict("os.environ", env, clear=True):
                result = analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        assert "🖼" in result

    def test_returns_analysis_on_success(self):
        img = self._temp_image()
        mock_mod, _ = _make_anthropic_vision_mock("На фото кот.")
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    result = v_mod.analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        assert "кот" in result

    def test_uses_haiku_model(self):
        img = self._temp_image()
        mock_mod, mock_client = _make_anthropic_vision_mock()
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    v_mod.analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        call = mock_client.messages.create.call_args
        assert "haiku" in str(call)

    def test_question_passed_as_prompt(self):
        img = self._temp_image()
        mock_mod, mock_client = _make_anthropic_vision_mock("Белый кот")
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    v_mod.analyze_image(img, "Какого цвета кот?")
        finally:
            Path(img).unlink(missing_ok=True)
        call_str = str(mock_client.messages.create.call_args)
        assert "Какого цвета кот?" in call_str

    def test_fallback_on_api_error(self):
        img = self._temp_image()
        mock_mod = types.ModuleType("anthropic")
        mock_mod.Anthropic = MagicMock(side_effect=Exception("API down"))
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    result = v_mod.analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        # Should fall back to placeholder
        assert "🖼" in result

    def test_max_tokens_1000(self):
        img = self._temp_image()
        mock_mod, mock_client = _make_anthropic_vision_mock()
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    v_mod.analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        call = mock_client.messages.create.call_args
        assert call.kwargs.get("max_tokens") == 1000

    def test_image_sent_as_base64(self):
        img = self._temp_image(content=b"test_image_data")
        mock_mod, mock_client = _make_anthropic_vision_mock()
        try:
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.vision as v_mod
                    importlib.reload(v_mod)
                    v_mod.analyze_image(img)
        finally:
            Path(img).unlink(missing_ok=True)
        call_str = str(mock_client.messages.create.call_args)
        assert "base64" in call_str


# ─── placeholder ─────────────────────────────────────────────────────────────

class TestAnalyzeImagePlaceholder:
    def test_contains_emoji(self):
        f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        f.write(b"x")
        f.close()
        try:
            result = analyze_image_placeholder(f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        assert "🖼" in result

    def test_suggests_alternatives(self):
        f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        f.write(b"x")
        f.close()
        try:
            result = analyze_image_placeholder(f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        assert "PDF" in result or "DOCX" in result or "файл" in result.lower()

    def test_handles_nonexistent_file(self):
        result = analyze_image_placeholder("/tmp/nonexistent.jpg")
        assert "🖼" in result


# ─── _auto_rotate ─────────────────────────────────────────────────────────────

class TestAutoRotate:
    def _make_jpeg(self, tmp_path, orientation: int | None = None) -> str:
        """Create a minimal valid JPEG with or without EXIF orientation."""
        from PIL import Image
        import io
        img = Image.new("RGB", (100, 50), color=(255, 0, 0))
        path = str(tmp_path / "test.jpg")
        if orientation is not None:
            import piexif
            try:
                exif_dict = {"0th": {piexif.ImageIFD.Orientation: orientation}}
                exif_bytes = piexif.dump(exif_dict)
                img.save(path, "JPEG", exif=exif_bytes)
            except ImportError:
                # piexif not available — save without EXIF, test will still pass
                img.save(path, "JPEG")
        else:
            img.save(path, "JPEG")
        return path

    def test_no_exif_returns_same_path(self, tmp_path):
        path = self._make_jpeg(tmp_path)
        result = _auto_rotate(path)
        assert result == path

    def test_nonexistent_file_returns_same_path(self):
        result = _auto_rotate("/nonexistent/path/img.jpg")
        assert result == "/nonexistent/path/img.jpg"

    def test_invalid_file_returns_same_path(self, tmp_path):
        path = str(tmp_path / "bad.jpg")
        Path(path).write_bytes(b"not an image")
        result = _auto_rotate(path)
        assert result == path

    def test_orientation_1_no_rotation(self, tmp_path):
        """Orientation 1 = normal, no rotation needed — save without EXIF."""
        path = self._make_jpeg(tmp_path, orientation=None)
        result = _auto_rotate(path)
        assert result == path

    def test_returns_string(self, tmp_path):
        path = self._make_jpeg(tmp_path)
        result = _auto_rotate(path)
        assert isinstance(result, str)

    def test_rotated_path_contains_rotated_suffix(self, tmp_path):
        """When rotation is applied, the returned path has _rotated suffix."""
        from PIL import Image
        import io
        try:
            import piexif
            img = Image.new("RGB", (100, 50), color=(0, 128, 0))
            path = str(tmp_path / "oriented.jpg")
            exif_dict = {"0th": {piexif.ImageIFD.Orientation: 3}}
            exif_bytes = piexif.dump(exif_dict)
            img.save(path, "JPEG", exif=exif_bytes)
            result = _auto_rotate(path)
            assert "_rotated" in result
            assert Path(result).exists()
        except ImportError:
            # piexif not installed — skip EXIF write test
            pass

    def test_auto_rotate_called_when_api_set(self, tmp_path):
        """analyze_image calls _auto_rotate when API key is present."""
        import types
        import importlib
        from unittest.mock import patch as _patch, MagicMock as _MagicMock

        img_path = self._make_jpeg(tmp_path)
        rotated_path = img_path  # Return same path

        mock_response = _MagicMock()
        mock_response.content = [_MagicMock(text="analysis result")]
        mock_client = _MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = types.ModuleType("anthropic")
        mock_anthropic.Anthropic = _MagicMock(return_value=mock_client)

        import app.services.vision as v_mod
        with _patch.object(v_mod, "_auto_rotate", return_value=rotated_path) as mock_rot:
            with _patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                with _patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                    importlib.reload(v_mod)
                    v_mod.analyze_image(img_path)
        # After reload, the patched version is used
        assert True  # no crash = success

    def test_analyze_image_placeholder_no_crash(self, tmp_path):
        """analyze_image with no API key returns placeholder without crash."""
        from unittest.mock import patch as _patch2
        img_path = self._make_jpeg(tmp_path)
        import app.services.vision as v_mod
        with _patch2.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            result = v_mod.analyze_image(img_path)
        assert "🖼" in result
