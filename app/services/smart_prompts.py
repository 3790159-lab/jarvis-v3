# -*- coding: utf-8 -*-
"""Smart photo prompt enhancer — uses Claude API to generate professional prompts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _ROOT / "state" / "smart_prompts_cache"
_CACHE_TTL_DAYS = 7

# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

_CATEGORY_SYSTEM = """You are an expert image prompt engineer.
Given a user's image description, classify it into exactly one category.
Return ONLY the category word, nothing else.

Categories:
- food (dishes, drinks, food products, restaurant, cafe)
- design (UI/UX, graphics, logos, websites, apps, interfaces)
- people (portraits, people, persons, faces, fashion)
- place (architecture, landscapes, cities, interiors, spaces)
- object (products, items, gadgets, furniture, accessories)
- abstract (patterns, textures, fantasy, art, abstract concepts)"""

_FOOD_SYSTEM = """You are a professional food photographer with 20 years of experience.
Your work has appeared in Bon Appetit, Food & Wine, and Michelin Guide publications.

Transform the user's simple food description into a professional food photography prompt.
Include:
- Specific dish composition and garnishes
- Camera: Canon EOS R5 or Hasselblad X2D with 85mm or 100mm macro lens
- Aperture: f/1.8 to f/2.8 for shallow depth of field
- Lighting: golden hour window light / dramatic side lighting / soft diffused north light
- Background: marble, slate, wooden table, linen, etc.
- Styling: steam, condensation, sauce drizzle, fresh herbs
- Quality tags: 8k, magazine quality, ultra-realistic, professional food styling
- Style reference: like Jonathan Lovekin, Andrew Scrivani, or Yossy Arefi
Keep it under 120 words. Return ONLY the enhanced prompt text."""

_DESIGN_SYSTEM = """You are a senior product designer and Behance/Dribbble master.
Transform the user's design description into a professional visual prompt.
Include:
- Design style (flat, glassmorphism, neumorphism, 3D, minimal)
- Color palette description
- Typography style
- Composition and layout hints
- Lighting and shadows
- Reference: Behance trending, Awwwards, Apple design language
- Quality tags: ultra HD, professional, 8k
Keep it under 100 words. Return ONLY the enhanced prompt text."""

_PEOPLE_SYSTEM = """You are a professional portrait photographer.
Your work is similar to Annie Leibovitz, Steve McCurry, and Jimmy Nelson.
Transform the user's description into a professional portrait photography prompt.
Include:
- Lighting setup (Rembrandt, butterfly, golden hour, rim light)
- Camera and lens (Canon 85mm f/1.2, Nikon 105mm f/2.8)
- Depth of field and bokeh
- Expression and emotion
- Color grade (warm/cool/moody/clean)
- Quality: 8k, magazine cover quality
Keep it under 100 words. Return ONLY the enhanced prompt text."""

_PLACE_SYSTEM = """You are a professional architectural and landscape photographer.
Transform the user's description into a professional location/architecture prompt.
Include:
- Time of day (golden hour, blue hour, midday, night)
- Weather conditions
- Camera angle and composition (wide angle, aerial, eye level)
- Lens choice (14mm, 24mm, ultra-wide)
- Color and mood
- Quality: 8k, HDR, professional
Keep it under 100 words. Return ONLY the enhanced prompt text."""

_OBJECT_SYSTEM = """You are a professional product photographer.
Transform the user's description into a professional product photography prompt.
Include:
- Lighting setup (studio lighting, softbox, ringlight, natural)
- Background (white, gradient, contextual, lifestyle)
- Angle and perspective
- Shadows and reflections
- Quality: commercial grade, 8k, sharp details
Keep it under 100 words. Return ONLY the enhanced prompt text."""

_GENERAL_SYSTEM = """You are an expert AI image prompt engineer.
Transform the user's description into a highly detailed, professional image generation prompt.
Include: style, lighting, composition, quality tags, artistic references.
Keep it under 120 words. Return ONLY the enhanced prompt text."""

_CATEGORY_SYSTEMS = {
    "food": _FOOD_SYSTEM,
    "design": _DESIGN_SYSTEM,
    "people": _PEOPLE_SYSTEM,
    "place": _PLACE_SYSTEM,
    "object": _OBJECT_SYSTEM,
    "abstract": _GENERAL_SYSTEM,
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_path(description: str) -> Path:
    from app.services.block_l_common import get_content_hash
    h = get_content_hash(description)
    return _CACHE_DIR / f"{h}.json"


def _load_from_cache(description: str) -> Optional[Dict[str, Any]]:
    import time
    p = _cache_path(description)
    if not p.exists():
        return None
    from app.services.block_l_common import load_json_safe
    data = load_json_safe(p)
    if not data:
        return None
    # TTL check
    cached_at = data.get("cached_at", 0)
    if time.time() - cached_at > _CACHE_TTL_DAYS * 86400:
        try:
            p.unlink()
        except Exception:
            pass
        return None
    return data


def _save_to_cache(description: str, result: Dict[str, Any]) -> None:
    import time
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    from app.services.block_l_common import save_json_safe
    result["cached_at"] = time.time()
    save_json_safe(_cache_path(description), result)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def detect_category(user_description: str, claude_api_fn=None) -> str:
    """Detect image category. Returns one of: food, design, people, place, object, abstract."""
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=20)

    try:
        result = claude_api_fn(user_description, _CATEGORY_SYSTEM).strip().lower()
        valid = {"food", "design", "people", "place", "object", "abstract"}
        # Extract first word that matches a category
        for word in result.split():
            word = word.strip(".,!;:")
            if word in valid:
                return word
        return "food" if any(w in user_description.lower() for w in
                              ["еда", "блюдо", "食", "food", "dish", "pasta", "soup", "coffee",
                               "кофе", "паста", "блин", "пицца", "суши"]) else "abstract"
    except Exception:
        return "abstract"


def _enhance_prompt(description: str, category: str, claude_api_fn=None) -> str:
    """Enhance description using category-specific system prompt."""
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)

    system = _CATEGORY_SYSTEMS.get(category, _GENERAL_SYSTEM)
    return claude_api_fn(description, system).strip()


def smart_enhance(user_description: str, claude_api_fn=None) -> Dict[str, Any]:
    """
    Detect category and enhance the prompt.
    Returns: {original, enhanced, category, from_cache, prompt_metadata}
    """
    # Check cache first
    cached = _load_from_cache(user_description)
    if cached:
        cached["from_cache"] = True
        return cached

    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        def _default_fn(p, s=None): return claude_api_call(p, system=s, model="claude-sonnet-4-5")
        claude_api_fn = _default_fn

    category = detect_category(user_description, claude_api_fn)
    try:
        enhanced = _enhance_prompt(user_description, category, claude_api_fn)
    except Exception:
        enhanced = user_description + ", professional photography, 8k, ultra-realistic, award-winning"

    result = {
        "original": user_description,
        "enhanced": enhanced,
        "category": category,
        "from_cache": False,
        "prompt_metadata": {
            "category": category,
            "word_count": len(enhanced.split()),
            "system_used": category,
        },
    }
    _save_to_cache(user_description, result)
    return result


# Category-specific shortcuts
def enhance_food_prompt(description: str, claude_api_fn=None) -> str:
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)
    return _enhance_prompt(description, "food", claude_api_fn)


def enhance_design_prompt(description: str, claude_api_fn=None) -> str:
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)
    return _enhance_prompt(description, "design", claude_api_fn)


def enhance_people_prompt(description: str, claude_api_fn=None) -> str:
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)
    return _enhance_prompt(description, "people", claude_api_fn)


def enhance_place_prompt(description: str, claude_api_fn=None) -> str:
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)
    return _enhance_prompt(description, "place", claude_api_fn)


def enhance_general_prompt(description: str, claude_api_fn=None) -> str:
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=300)
    return _enhance_prompt(description, "abstract", claude_api_fn)
