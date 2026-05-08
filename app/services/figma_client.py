"""Figma API client — wireframe generation and file export."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List

FIGMA_API_BASE = "https://api.figma.com/v1"


def _get_api_key() -> str:
    key = os.getenv("FIGMA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FIGMA_API_KEY not configured. "
            "Get key at https://www.figma.com/developers/api "
            "and add to .env: FIGMA_API_KEY=figd_..."
        )
    return key


def _get_headers() -> Dict[str, str]:
    return {
        "X-Figma-Token": _get_api_key(),
        "Content-Type": "application/json",
    }


def get_account_info() -> Dict:
    """Test Figma API connection. Returns user info or error dict."""
    try:
        req = urllib.request.Request(
            f"{FIGMA_API_BASE}/me",
            headers=_get_headers(),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


def generate_wireframe_json(description: str, style: str = "modern") -> Dict:
    """Generate Figma-importable wireframe JSON from a text description."""
    return {
        "name": f"Wireframe: {description[:50]}",
        "frames": _generate_basic_landing_frames(description, style),
    }


def _generate_basic_landing_frames(description: str, style: str) -> List[Dict]:
    color_bg = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
    color_header = {"r": 0.2, "g": 0.4, "b": 0.9, "a": 1.0}
    color_text = {"r": 0.1, "g": 0.1, "b": 0.1, "a": 1.0}

    def rect_node(node_id, name, x, y, w, h, fill):
        return {
            "id": node_id,
            "name": name,
            "type": "RECTANGLE",
            "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h},
            "fills": [{"type": "SOLID", "color": fill}],
            "strokes": [],
            "cornerRadius": 0,
        }

    def text_node(node_id, name, x, y, w, h, text, fill=None):
        return {
            "id": node_id,
            "name": name,
            "type": "TEXT",
            "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h},
            "characters": text,
            "fills": [{"type": "SOLID", "color": fill or color_text}],
        }

    desktop_children = [
        rect_node("1:10", "Header BG", 0, 0, 1440, 80, color_header),
        text_node("1:11", "Brand Logo", 40, 24, 200, 32, description[:20].title(), {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
        text_node("1:12", "Nav Links", 900, 24, 500, 32, "Features   Pricing   Contact"),
        rect_node("1:20", "Hero BG", 0, 80, 1440, 600, {"r": 0.97, "g": 0.97, "b": 1.0, "a": 1.0}),
        text_node("1:21", "Hero Title", 320, 250, 800, 80, description.title()),
        text_node("1:22", "Hero Subtitle", 360, 350, 720, 50, f"The best solution for {description[:30]}"),
        rect_node("1:23", "CTA Button", 580, 430, 280, 56, color_header),
        text_node("1:24", "CTA Text", 620, 448, 200, 24, "Get Started", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
        rect_node("1:30", "Features BG", 0, 680, 1440, 400, {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
        text_node("1:31", "Features Title", 600, 710, 240, 40, "Features"),
        rect_node("1:32", "Feature 1", 120, 780, 360, 200, {"r": 0.95, "g": 0.95, "b": 0.95, "a": 1.0}),
        rect_node("1:33", "Feature 2", 540, 780, 360, 200, {"r": 0.95, "g": 0.95, "b": 0.95, "a": 1.0}),
        rect_node("1:34", "Feature 3", 960, 780, 360, 200, {"r": 0.95, "g": 0.95, "b": 0.95, "a": 1.0}),
        rect_node("1:40", "Footer BG", 0, 1080, 1440, 120, {"r": 0.1, "g": 0.1, "b": 0.1, "a": 1.0}),
        text_node("1:41", "Footer Text", 580, 1116, 280, 32, f"© 2026 {description[:15].title()}", {"r": 0.8, "g": 0.8, "b": 0.8, "a": 1.0}),
    ]

    mobile_children = [
        rect_node("2:10", "Mobile Header", 0, 0, 375, 60, color_header),
        text_node("2:11", "Mobile Brand", 20, 16, 150, 28, description[:15].title(), {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
        rect_node("2:20", "Mobile Hero", 0, 60, 375, 300, {"r": 0.97, "g": 0.97, "b": 1.0, "a": 1.0}),
        text_node("2:21", "Mobile Title", 20, 130, 335, 60, description.title()),
        rect_node("2:22", "Mobile CTA", 60, 220, 255, 48, color_header),
        text_node("2:23", "Mobile CTA Text", 100, 234, 175, 24, "Get Started", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}),
    ]

    return [
        {
            "id": "1:1",
            "name": "Desktop Landing",
            "type": "FRAME",
            "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1440, "height": 1200},
            "backgroundColor": color_bg,
            "children": desktop_children,
        },
        {
            "id": "2:1",
            "name": "Mobile Landing",
            "type": "FRAME",
            "absoluteBoundingBox": {"x": 1500, "y": 0, "width": 375, "height": 812},
            "backgroundColor": color_bg,
            "children": mobile_children,
        },
    ]


def export_wireframe(description: str, output_path: str) -> str:
    """Export wireframe as JSON file user can import to Figma. Returns file path."""
    wireframe = generate_wireframe_json(description)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(wireframe, indent=2, ensure_ascii=False), encoding="utf-8")

    return str(out)
