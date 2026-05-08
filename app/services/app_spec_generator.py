# -*- coding: utf-8 -*-
"""App spec generator — uses Claude API to create bolt.diy-ready app specifications."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_APP_SPEC_SYSTEM = """You are a senior full-stack developer.
Given an app description, create a complete app specification for bolt.diy as valid JSON.
Return ONLY valid JSON, no markdown, no explanations.

JSON structure:
{
  "app_name": "TrackFit",
  "tagline": "Your personal fitness companion",
  "description": "A modern fitness tracking app with progress visualization and workout logging.",
  "tech_stack": "React + Tailwind CSS + Lucide Icons",
  "features": [
    {"name": "Workout Logger", "description": "Log daily workouts with sets/reps", "priority": "high"},
    {"name": "Progress Charts", "description": "Visual charts for weight and reps over time", "priority": "high"},
    {"name": "Exercise Library", "description": "Browse 50+ exercises with instructions", "priority": "medium"},
    {"name": "Goals Setting", "description": "Set weekly workout goals", "priority": "medium"},
    {"name": "Streak Tracker", "description": "Track consistency streaks", "priority": "low"}
  ],
  "components": ["Header with nav", "Dashboard with stats cards", "Workout form", "Chart component", "Exercise list"],
  "data_model": [
    {"entity": "Workout", "fields": ["id", "date", "exercises", "duration", "notes"]},
    {"entity": "Exercise", "fields": ["id", "name", "sets", "reps", "weight"]}
  ],
  "pages": [
    {"name": "Dashboard", "purpose": "Overview of stats and recent workouts"},
    {"name": "Log Workout", "purpose": "Form to add new workout"},
    {"name": "History", "purpose": "Calendar view of past workouts"},
    {"name": "Progress", "purpose": "Charts and analytics"}
  ],
  "styling": {
    "color_scheme": "dark",
    "primary_color": "#6366f1",
    "design_system": "Tailwind CSS with custom utility classes"
  },
  "bolt_diy_prompt": "Create a modern fitness tracking web app called TrackFit using React and Tailwind CSS...",
  "raw_user_input": "original description"
}

The bolt_diy_prompt field should be 200-500 words, well-structured, and include:
- App name and purpose
- Tech stack (React + Tailwind CSS + Lucide Icons preferred)
- All features with specific UI details
- Color scheme and design direction
- Any specific libraries needed
- Make it specific enough that bolt.diy can build it in one pass"""

_SIMPLE_SPEC_SYSTEM = """You are a senior developer. Create a minimal app spec for bolt.diy.
Return ONLY valid JSON.
JSON: {"app_name": "...", "tagline": "...", "features": ["...", "...", "..."], "bolt_diy_prompt": "..."}
The bolt_diy_prompt should be 100-200 words."""


def _parse_spec_json(raw: str, user_input: str) -> Dict[str, Any]:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        text = match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _build_fallback_spec(user_input)

    data.setdefault("raw_user_input", user_input)
    if "features" not in data or not data["features"]:
        data["features"] = [{"name": "Main Feature", "description": "Core functionality", "priority": "high"}]
    if "bolt_diy_prompt" not in data or not data["bolt_diy_prompt"]:
        data["bolt_diy_prompt"] = _build_default_bolt_prompt(data, user_input)

    return data


def _build_fallback_spec(user_input: str) -> Dict[str, Any]:
    return {
        "app_name": "MyApp",
        "tagline": "A modern web application",
        "description": user_input,
        "tech_stack": "React + Tailwind CSS + Lucide Icons",
        "features": [
            {"name": "Main Feature", "description": "Core app functionality", "priority": "high"},
            {"name": "User Interface", "description": "Clean and responsive UI", "priority": "high"},
            {"name": "Data Management", "description": "Store and manage app data", "priority": "medium"},
        ],
        "components": ["Header", "Main Content", "Footer"],
        "data_model": [],
        "pages": [{"name": "Home", "purpose": "Main page"}],
        "styling": {"color_scheme": "light", "primary_color": "#2563EB", "design_system": "Tailwind CSS"},
        "bolt_diy_prompt": _build_default_bolt_prompt_str(user_input),
        "raw_user_input": user_input,
    }


def _build_default_bolt_prompt_str(description: str) -> str:
    return (
        f"Build a modern, beautiful web application: {description}\n\n"
        "Tech stack: React with Tailwind CSS for styling, Lucide React for icons.\n\n"
        "Requirements:\n"
        "- Clean, modern UI with professional design\n"
        "- Responsive layout (mobile + desktop)\n"
        "- Interactive components with smooth animations\n"
        "- Local state management with React hooks\n"
        "- No backend needed - use localStorage for data persistence\n\n"
        "Design: Use a cohesive color palette, clear typography hierarchy, "
        "card-based layouts where appropriate. Make it look production-ready."
    )


def _build_default_bolt_prompt(spec: Dict[str, Any], user_input: str) -> str:
    name = spec.get("app_name", "MyApp")
    description = spec.get("description", user_input)
    features = spec.get("features", [])
    feature_lines = "\n".join(
        f"- {f.get('name', '?')}: {f.get('description', '')}"
        for f in features[:5]
    )
    return (
        f"Build {name}: {description}\n\n"
        f"Tech stack: React + Tailwind CSS + Lucide Icons\n\n"
        f"Core features:\n{feature_lines}\n\n"
        "Requirements:\n"
        "- Responsive design (mobile + desktop)\n"
        "- Clean modern UI with consistent design system\n"
        "- Local state with React hooks, localStorage for persistence\n"
        "- Professional quality, production-ready appearance"
    )


def generate_app_spec(user_description: str, claude_api_fn=None) -> Dict[str, Any]:
    """
    Generate a full app specification from user description.
    claude_api_fn: callable(prompt, system) -> str.
    """
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=3000)

    prompt = f"Create an app spec for bolt.diy: {user_description}"
    try:
        raw = claude_api_fn(prompt, _APP_SPEC_SYSTEM)
        spec = _parse_spec_json(raw, user_description)
    except Exception:
        spec = _build_fallback_spec(user_description)

    return spec


def generate_simple_app_spec(description: str, claude_api_fn=None) -> Dict[str, Any]:
    """Lighter version for /create_simple."""
    if claude_api_fn is None:
        from app.services.block_l_common import claude_api_call
        claude_api_fn = lambda p, s=None: claude_api_call(p, system=s, model="claude-sonnet-4-5", max_tokens=1000)

    prompt = f"Simple app: {description}"
    try:
        raw = claude_api_fn(prompt, _SIMPLE_SPEC_SYSTEM)
        spec = _parse_spec_json(raw, description)
        # Limit features for simple spec
        if isinstance(spec.get("features"), list):
            spec["features"] = spec["features"][:3]
    except Exception:
        spec = _build_fallback_spec(description)
        spec["features"] = spec["features"][:3]

    return spec


def format_spec_preview(spec: Dict[str, Any]) -> str:
    """Format spec as Russian-language Telegram preview (HTML)."""
    name = spec.get("app_name", "MyApp")
    tagline = spec.get("tagline", "")
    stack = spec.get("tech_stack", "React + Tailwind")
    features = spec.get("features", [])

    feature_lines = "\n".join(
        f"{i}. {f.get('name', '?')} [{f.get('priority', 'med')}]"
        for i, f in enumerate(features[:5], 1)
    )

    return (
        f"<b>{name}</b>\n"
        f"{tagline}\n\n"
        f"Stack: {stack}\n\n"
        f"Фичи:\n{feature_lines}"
    )
