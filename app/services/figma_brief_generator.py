# -*- coding: utf-8 -*-
"""Figma design brief generator — uses Claude API to create detailed design specs."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_DESIGN_SYSTEM = """You are a senior product designer.
Given a brief description, create a detailed, actionable design brief as valid JSON.
Return ONLY valid JSON, no markdown, no explanations.

JSON structure:
{
  "project_name": "Short 2-3 word name",
  "project_type": "landing|dashboard|mobile_app|website|e_commerce|portfolio",
  "target_audience": "Description of target users",
  "design_style": "modern|luxury|playful|business|health|minimal|dark",
  "color_palette": {
    "primary": "#hex",
    "secondary": "#hex",
    "accent": "#hex",
    "bg": "#hex",
    "text": "#hex"
  },
  "typography": {
    "heading_font": "Font name",
    "body_font": "Font name",
    "heading_size": "48px",
    "body_size": "16px",
    "heading_weight": "700"
  },
  "components": [
    {"name": "Header", "content": "Logo + nav links", "type": "navigation"},
    {"name": "Hero", "content": "Headline + CTA button", "type": "hero"},
    {"name": "Features", "content": "3 feature cards", "type": "section"},
    {"name": "Testimonials", "content": "Customer quotes", "type": "social_proof"},
    {"name": "CTA", "content": "Call to action banner", "type": "cta"},
    {"name": "Footer", "content": "Links + contacts", "type": "footer"}
  ],
  "desktop_frame": {
    "width": 1440,
    "height": 900,
    "sections": ["Header", "Hero", "Features", "Testimonials", "CTA", "Footer"]
  },
  "mobile_frame": {
    "width": 375,
    "height": 812,
    "sections": ["Header", "Hero", "Features", "CTA", "Footer"]
  },
  "raw_prompt": "original user input"
}"""

_STYLE_PALETTES = {
    "modern": {"primary": "#2563EB", "secondary": "#64748B", "accent": "#F59E0B", "bg": "#FFFFFF", "text": "#1E293B"},
    "luxury": {"primary": "#B8860B", "secondary": "#2C2C2C", "accent": "#FFD700", "bg": "#0A0A0A", "text": "#F5F5DC"},
    "playful": {"primary": "#FF6B6B", "secondary": "#4ECDC4", "accent": "#FFE66D", "bg": "#FAFAFA", "text": "#2D3436"},
    "business": {"primary": "#1A1A2E", "secondary": "#16213E", "accent": "#0F3460", "bg": "#E9ECEF", "text": "#212529"},
    "health": {"primary": "#2ECC71", "secondary": "#27AE60", "accent": "#F39C12", "bg": "#F8FFF8", "text": "#2C3E50"},
    "minimal": {"primary": "#000000", "secondary": "#666666", "accent": "#FF0000", "bg": "#FFFFFF", "text": "#333333"},
    "dark": {"primary": "#BB86FC", "secondary": "#03DAC6", "accent": "#CF6679", "bg": "#121212", "text": "#E0E0E0"},
}

_COMPONENT_ICONS = {
    "navigation": "Nav",
    "hero": "Hero",
    "section": "Sec",
    "social_proof": "Review",
    "cta": "CTA",
    "footer": "Foot",
    "pricing": "Price",
    "gallery": "Gallery",
    "form": "Form",
    "features": "Features",
}

_COLOR_EMOJI = {
    "#": "🟦",
    "0": "⬛",
    "1": "🟫",
    "2": "🟩",
    "3": "🟦",
    "4": "🟪",
    "5": "🟦",
    "6": "🟧",
    "7": "🟨",
    "8": "🟥",
    "9": "🟥",
    "A": "🟨",
    "B": "⬜",
    "C": "🟩",
    "D": "🟦",
    "E": "🟧",
    "F": "⬜",
}


def _color_square(hex_color: str) -> str:
    """Return a colored emoji block approximating the hex color."""
    h = hex_color.lstrip("#").upper()
    if not h:
        return "[?]"
    r = int(h[0:2], 16) if len(h) >= 2 else 0
    g = int(h[2:4], 16) if len(h) >= 4 else 0
    b = int(h[4:6], 16) if len(h) >= 6 else 0
    if r > 200 and g < 100 and b < 100:
        return "[RED]"
    if r < 100 and g > 150 and b < 100:
        return "[GRN]"
    if r < 100 and g < 100 and b > 150:
        return "[BLU]"
    if r > 200 and g > 150 and b < 100:
        return "[YEL]"
    if r > 150 and g < 100 and b > 150:
        return "[PUR]"
    if r < 80 and g < 80 and b < 80:
        return "[BLK]"
    if r > 200 and g > 200 and b > 200:
        return "[WHT]"
    return "[CLR]"


def _parse_brief_json(raw: str, user_prompt: str) -> Dict[str, Any]:
    """Extract JSON from Claude response, with fallback defaults."""
    text = raw.strip()
    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Minimal fallback
        data = _build_fallback_brief(user_prompt)

    data.setdefault("raw_prompt", user_prompt)
    # Ensure palette
    style = data.get("design_style", "modern")
    if "color_palette" not in data or not isinstance(data.get("color_palette"), dict):
        data["color_palette"] = _STYLE_PALETTES.get(style, _STYLE_PALETTES["modern"])
    if "components" not in data or not data["components"]:
        data["components"] = _default_components()

    return data


def _default_components() -> List[Dict[str, str]]:
    return [
        {"name": "Header", "content": "Logo + navigation", "type": "navigation"},
        {"name": "Hero", "content": "Main headline + CTA", "type": "hero"},
        {"name": "Features", "content": "3 key features", "type": "section"},
        {"name": "CTA", "content": "Call to action", "type": "cta"},
        {"name": "Footer", "content": "Links + contacts", "type": "footer"},
    ]


def _build_fallback_brief(user_prompt: str) -> Dict[str, Any]:
    return {
        "project_name": "My Project",
        "project_type": "landing",
        "target_audience": "General audience",
        "design_style": "modern",
        "color_palette": _STYLE_PALETTES["modern"],
        "typography": {
            "heading_font": "Inter",
            "body_font": "Inter",
            "heading_size": "48px",
            "body_size": "16px",
            "heading_weight": "700",
        },
        "components": _default_components(),
        "desktop_frame": {"width": 1440, "height": 900, "sections": ["Header", "Hero", "Features", "CTA", "Footer"]},
        "mobile_frame": {"width": 375, "height": 812, "sections": ["Header", "Hero", "Features", "CTA", "Footer"]},
        "raw_prompt": user_prompt,
    }


def generate_design_brief(user_prompt: str, claude_api_fn=None) -> Dict[str, Any]:
    """
    Generate a detailed design brief from a user prompt.
    claude_api_fn: callable(prompt, system) -> str. If None, uses block_l_common.claude_api_call.
    """
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5")

    prompt = f"Create a design brief for: {user_prompt}"
    try:
        raw = claude_api_fn(prompt, _DESIGN_SYSTEM)
        brief = _parse_brief_json(raw, user_prompt)
    except Exception:
        brief = _build_fallback_brief(user_prompt)

    return brief


def format_brief_preview(brief: Dict[str, Any]) -> str:
    """Format brief as Russian-language Telegram preview text (HTML)."""
    name = brief.get("project_name", "Проект")
    ptype = brief.get("project_type", "landing")
    style = brief.get("design_style", "modern")
    palette = brief.get("color_palette", {})
    components = brief.get("components", [])

    # Color squares (ASCII-safe)
    colors_preview = " ".join(
        f"{_color_square(v)} {k}" for k, v in palette.items()
    )

    # Component list
    comp_list = ", ".join(c.get("name", "?") for c in components[:6])

    type_ru = {
        "landing": "Лендинг",
        "dashboard": "Дашборд",
        "mobile_app": "Мобильное приложение",
        "website": "Веб-сайт",
        "e_commerce": "Интернет-магазин",
        "portfolio": "Портфолио",
    }.get(ptype, ptype.capitalize())

    style_ru = {
        "modern": "Современный",
        "luxury": "Люкс",
        "playful": "Игривый",
        "business": "Деловой",
        "health": "Здоровье",
        "minimal": "Минимализм",
        "dark": "Тёмный",
    }.get(style, style.capitalize())

    return (
        f"<b>{name}</b>\n"
        f"Тип: {type_ru} | Стиль: {style_ru}\n\n"
        f"Палитра: {colors_preview}\n\n"
        f"Компоненты: {comp_list}\n"
        f"Desktop 1440px + Mobile 375px"
    )
