# -*- coding: utf-8 -*-
"""Tests for landing_generator_v2.py (Phase L.4)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BRIEF = {
    "business_name": "Cafe Lapin",
    "color_scheme": "warm",
    "style": "luxury",
    "contacts": "info@cafelapin.com",
    "cta": "Book a table",
}
_CONTENT = {
    "hero_headline": "Taste the Authentic French Experience",
    "hero_subheadline": "We bring French cuisine to your table.",
    "cta_text": "Book Your Table",
    "features": [
        {"title": "Quality", "description": "High quality food.", "icon_name": "star"},
        {"title": "Cozy", "description": "Warm atmosphere.", "icon_name": "heart"},
    ],
    "testimonials": [
        {"quote": "Amazing!", "name": "Marie", "role": "Customer"},
    ],
    "faq": [
        {"question": "Reservations?", "answer": "Yes, book online."},
    ],
    "about_text": "Founded 2015.",
    "footer_links": ["Privacy Policy", "Contact"],
}


def test_generate_landing_v2_returns_html():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert isinstance(html, str)
    assert "<!DOCTYPE html>" in html


def test_generate_landing_v2_contains_business_name():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Cafe Lapin" in html


def test_generate_landing_v2_contains_hero_headline():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Taste the Authentic French Experience" in html


def test_generate_landing_v2_contains_features():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Quality" in html
    assert "Cozy" in html


def test_generate_landing_v2_contains_testimonials():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Amazing!" in html or "Marie" in html


def test_generate_landing_v2_contains_faq():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Reservations?" in html


def test_generate_landing_v2_is_responsive():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "viewport" in html
    assert "max-width" in html


def test_generate_landing_v2_has_nav():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "<nav" in html


def test_generate_landing_v2_has_footer():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "<footer" in html


def test_generate_landing_v2_has_cta():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "Book Your Table" in html


def test_generate_landing_v2_has_javascript():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, _CONTENT)
    assert "<script>" in html


def test_generate_landing_v2_modern_style():
    from app.services.landing_generator_v2 import generate_landing_v2
    brief = dict(_BRIEF)
    brief["style"] = "modern"
    html = generate_landing_v2(brief, _CONTENT)
    assert "2563EB" in html  # modern primary color


def test_generate_landing_v2_luxury_style():
    from app.services.landing_generator_v2 import generate_landing_v2
    brief = dict(_BRIEF)
    brief["style"] = "luxury"
    html = generate_landing_v2(brief, _CONTENT)
    assert "0A0A0A" in html  # luxury bg


def test_generate_landing_v2_health_style():
    from app.services.landing_generator_v2 import generate_landing_v2
    brief = dict(_BRIEF)
    brief["style"] = "health"
    html = generate_landing_v2(brief, _CONTENT)
    assert "16A34A" in html  # health primary


def test_generate_landing_v2_empty_content():
    from app.services.landing_generator_v2 import generate_landing_v2
    html = generate_landing_v2(_BRIEF, {})
    assert isinstance(html, str)
    assert "<!DOCTYPE html>" in html


def test_save_landing_v2_creates_file():
    from app.services.landing_generator_v2 import save_landing_v2
    import app.services.landing_generator_v2 as lgv2
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = lgv2._LANDINGS_DIR
        lgv2._LANDINGS_DIR = Path(tmp)
        try:
            path = save_landing_v2(_BRIEF, _CONTENT)
            assert Path(path).exists()
            assert Path(path).stat().st_size > 1000
        finally:
            lgv2._LANDINGS_DIR = old_dir


def test_save_landing_v2_filename_contains_biz_name():
    from app.services.landing_generator_v2 import save_landing_v2
    import app.services.landing_generator_v2 as lgv2
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = lgv2._LANDINGS_DIR
        lgv2._LANDINGS_DIR = Path(tmp)
        try:
            path = save_landing_v2(_BRIEF, _CONTENT)
            assert "Cafe_Lapin" in path or "landing_v2" in path
        finally:
            lgv2._LANDINGS_DIR = old_dir


def test_save_landing_v2_utf8_encoding():
    from app.services.landing_generator_v2 import save_landing_v2
    import app.services.landing_generator_v2 as lgv2
    brief = dict(_BRIEF)
    brief["business_name"] = "Кафе Лапен"
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = lgv2._LANDINGS_DIR
        lgv2._LANDINGS_DIR = Path(tmp)
        try:
            path = save_landing_v2(brief, _CONTENT)
            content = Path(path).read_text(encoding="utf-8")
            assert "Кафе Лапен" in content
        finally:
            lgv2._LANDINGS_DIR = old_dir
