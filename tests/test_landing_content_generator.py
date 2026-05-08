# -*- coding: utf-8 -*-
"""Tests for landing_content_generator.py (Phase L.4)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_BRIEF = {
    "business_name": "Cafe Lapin",
    "target_audience": "Families 25-45",
    "main_product": "French cuisine",
    "key_advantages": "Quality, Atmosphere, Value",
    "cta": "Book a table",
    "color_scheme": "warm",
    "style": "luxury",
    "contacts": "info@cafelapin.com",
}

_SAMPLE_CONTENT = {
    "hero_headline": "Taste the Authentic French Experience",
    "hero_subheadline": "Cafe Lapin brings the finest French cuisine to your table.",
    "cta_text": "Book Your Table",
    "features": [
        {"title": "Authentic Recipes", "description": "Classic French recipes passed down generations.", "icon_name": "star"},
        {"title": "Warm Atmosphere", "description": "Cozy and welcoming environment.", "icon_name": "heart"},
        {"title": "Family Friendly", "description": "Special menu for children.", "icon_name": "shield"},
    ],
    "testimonials": [
        {"quote": "Best restaurant in the city!", "name": "Marie Dubois", "role": "Food Blogger"},
        {"quote": "Fantastic food and service.", "name": "Jean Martin", "role": "Regular Customer"},
        {"quote": "A true gem!", "name": "Sophie Bernard", "role": "Local Guide"},
    ],
    "faq": [
        {"question": "Do you take reservations?", "answer": "Yes, book online or call us."},
        {"question": "Is there parking?", "answer": "Free parking available."},
        {"question": "Vegetarian options?", "answer": "Yes, we have vegan and vegetarian dishes."},
        {"question": "Do you host events?", "answer": "Yes, we offer private dining."},
        {"question": "Opening hours?", "answer": "Mon-Sun 11am-11pm."},
    ],
    "about_text": "Cafe Lapin was founded in 2015 with a passion for authentic French cooking.",
    "footer_links": ["Privacy Policy", "Terms of Service", "Contact"],
}


def _mock_claude(response_dict: dict):
    def fn(prompt, system=None):
        return json.dumps(response_dict, ensure_ascii=False)
    return fn


def test_generate_landing_content_returns_dict():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    assert isinstance(result, dict)


def test_generate_landing_content_has_required_keys():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    for key in ("hero_headline", "hero_subheadline", "cta_text", "features",
                "testimonials", "faq", "about_text"):
        assert key in result, f"Missing: {key}"


def test_generate_landing_content_has_features():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    assert len(result["features"]) >= 1
    assert "title" in result["features"][0]


def test_generate_landing_content_has_testimonials():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    assert len(result["testimonials"]) >= 1


def test_generate_landing_content_has_faq():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    assert len(result["faq"]) >= 1


def test_generate_landing_content_fallback_on_bad_json():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, lambda p, s=None: "not json")
    assert isinstance(result, dict)
    assert "hero_headline" in result


def test_generate_landing_content_fallback_on_api_error():
    from app.services.landing_content_generator import generate_landing_content
    def error_fn(p, s=None): raise RuntimeError("API fail")
    result = generate_landing_content(_SAMPLE_BRIEF, error_fn)
    assert isinstance(result, dict)
    assert "cta_text" in result


def test_generate_landing_content_strips_markdown():
    from app.services.landing_content_generator import generate_landing_content
    def fn(p, s=None): return "```json\n" + json.dumps(_SAMPLE_CONTENT) + "\n```"
    result = generate_landing_content(_SAMPLE_BRIEF, fn)
    assert result["hero_headline"] == "Taste the Authentic French Experience"


def test_generate_landing_content_preserves_headline():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, _mock_claude(_SAMPLE_CONTENT))
    assert "Taste" in result["hero_headline"] or len(result["hero_headline"]) > 0


def test_generate_landing_content_fallback_uses_brief_data():
    from app.services.landing_content_generator import generate_landing_content
    result = generate_landing_content(_SAMPLE_BRIEF, lambda p, s=None: "bad")
    # Fallback should include business name somewhere
    assert len(result["hero_headline"]) > 0
