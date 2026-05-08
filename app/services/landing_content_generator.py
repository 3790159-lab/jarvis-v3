# -*- coding: utf-8 -*-
"""Landing content generator — uses Claude API to write compelling landing page copy."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_CONTENT_SYSTEM = """You are an expert copywriter specializing in landing pages.
Given a business brief, generate compelling landing page content.
Return ONLY valid JSON, no markdown.

JSON structure:
{
  "hero_headline": "Short compelling headline, 5-10 words",
  "hero_subheadline": "One or two sentences expanding on the headline",
  "cta_text": "Action verb + benefit, max 5 words",
  "features": [
    {"title": "Feature name", "description": "2-3 sentences", "icon_name": "zap"},
    {"title": "Feature name", "description": "2-3 sentences", "icon_name": "shield"},
    {"title": "Feature name", "description": "2-3 sentences", "icon_name": "star"}
  ],
  "testimonials": [
    {"quote": "Realistic quote about the business", "name": "First Last", "role": "Role or City"},
    {"quote": "Another realistic quote", "name": "First Last", "role": "Role or City"},
    {"quote": "Third realistic quote", "name": "First Last", "role": "Role or City"}
  ],
  "faq": [
    {"question": "Common question about this business", "answer": "Clear helpful answer"},
    {"question": "Another common question", "answer": "Clear helpful answer"},
    {"question": "Third question", "answer": "Clear helpful answer"},
    {"question": "Fourth question", "answer": "Clear helpful answer"},
    {"question": "Fifth question", "answer": "Clear helpful answer"}
  ],
  "about_text": "2-3 paragraphs about the business",
  "footer_links": ["Privacy Policy", "Terms of Service", "Contact"]
}

Generate realistic, professional content based on the brief. Match the language/locale of the business name."""


def _parse_content_json(raw: str) -> Optional[Dict[str, Any]]:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _build_fallback_content(brief: Dict[str, Any]) -> Dict[str, Any]:
    biz = brief.get("business_name", "Business")
    product = brief.get("main_product", "our service")
    adv = brief.get("key_advantages", "Quality, Speed, Value")
    cta = brief.get("cta", "Contact Us")
    return {
        "hero_headline": f"Welcome to {biz}",
        "hero_subheadline": f"We offer {product}. {adv}",
        "cta_text": cta.capitalize(),
        "features": [
            {"title": a.strip(), "description": f"{a.strip()} — we deliver it.", "icon_name": "star"}
            for a in adv.split(",")[:3]
        ] or [{"title": "Quality", "description": "High quality service.", "icon_name": "star"}],
        "testimonials": [
            {"quote": f"Great {product}!", "name": "Happy Customer", "role": "Client"},
            {"quote": f"Excellent service from {biz}.", "name": "Satisfied User", "role": "Customer"},
            {"quote": "Highly recommended!", "name": "Regular Client", "role": "Loyal Customer"},
        ],
        "faq": [
            {"question": f"What does {biz} offer?", "answer": f"We offer {product}."},
            {"question": "How can I get started?", "answer": f"Just {cta.lower()}."},
            {"question": "What are your hours?", "answer": "We are available Monday-Friday."},
            {"question": "Do you offer support?", "answer": "Yes, we have full support."},
            {"question": "How do I contact you?", "answer": "See contacts below."},
        ],
        "about_text": f"{biz} is dedicated to {product}. {adv}.",
        "footer_links": ["Privacy Policy", "Terms of Service", "Contact"],
    }


def generate_landing_content(brief: Dict[str, Any], claude_api_fn=None) -> Dict[str, Any]:
    """
    Generate compelling landing page content from a brief.
    Returns dict with hero_headline, features, testimonials, faq, etc.
    """
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=3000)

    brief_text = "\n".join(f"{k}: {v}" for k, v in brief.items())
    prompt = f"Generate landing page content for this brief:\n{brief_text}"

    try:
        raw = claude_api_fn(prompt, _CONTENT_SYSTEM)
        content = _parse_content_json(raw)
        if not content:
            content = _build_fallback_content(brief)
    except Exception:
        content = _build_fallback_content(brief)

    # Ensure all required keys
    content.setdefault("hero_headline", brief.get("business_name", "Welcome"))
    content.setdefault("hero_subheadline", brief.get("main_product", ""))
    content.setdefault("cta_text", brief.get("cta", "Get Started"))
    content.setdefault("features", [])
    content.setdefault("testimonials", [])
    content.setdefault("faq", [])
    content.setdefault("about_text", "")
    content.setdefault("footer_links", ["Privacy Policy", "Contact"])

    return content
