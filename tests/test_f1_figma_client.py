"""Phase F.1: Figma API client foundation."""
from __future__ import annotations

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_figma_api_key_required():
    from app.services.figma_client import _get_api_key
    original = os.environ.pop("FIGMA_API_KEY", None)
    try:
        try:
            _get_api_key()
            assert False, "Should raise RuntimeError"
        except RuntimeError as e:
            assert "FIGMA_API_KEY" in str(e)
    finally:
        if original is not None:
            os.environ["FIGMA_API_KEY"] = original


def test_get_account_info_returns_dict():
    from app.services import figma_client
    # Without real key, should return error dict (not raise)
    os.environ.pop("FIGMA_API_KEY", None)
    result = figma_client.get_account_info()
    assert isinstance(result, dict)
    assert "error" in result


def test_generate_wireframe_returns_json():
    from app.services.figma_client import generate_wireframe_json
    result = generate_wireframe_json("restaurant landing page")
    assert isinstance(result, dict)
    assert "name" in result
    assert "frames" in result
    assert len(result["frames"]) >= 1


def test_wireframe_has_desktop_and_mobile_frames():
    from app.services.figma_client import generate_wireframe_json
    result = generate_wireframe_json("app landing")
    frame_names = [f["name"] for f in result["frames"]]
    assert any("Desktop" in n or "desktop" in n for n in frame_names)
    assert any("Mobile" in n or "mobile" in n for n in frame_names)


def test_wireframe_name_contains_description():
    from app.services.figma_client import generate_wireframe_json
    result = generate_wireframe_json("coffee shop")
    assert "coffee" in result["name"].lower() or "coffee shop" in result["name"].lower()


def test_export_wireframe_creates_file(tmp_path):
    from app.services.figma_client import export_wireframe
    output = str(tmp_path / "sub" / "test_wireframe.fig.json")
    path = export_wireframe("SaaS tool", output)
    assert os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    assert "name" in data
    assert "frames" in data


def test_export_wireframe_creates_parent_dirs(tmp_path):
    from app.services.figma_client import export_wireframe
    deep_path = str(tmp_path / "a" / "b" / "c" / "wire.json")
    path = export_wireframe("deep test", deep_path)
    assert os.path.exists(path)
