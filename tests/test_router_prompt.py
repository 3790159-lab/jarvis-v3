# -*- coding: utf-8 -*-
"""The router system prompt must advertise the P1 tools and stay honest about stubs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import _DEFAULT_SYSTEM_PROMPT as P


def test_prompt_mentions_new_p1_tools():
    for name in ("web_research", "build_table", "generate_image", "answer_about_file"):
        assert name in P, f"prompt missing tool {name}"


def test_prompt_is_honest_about_templated_brain_engineer():
    # must flag that deep analysis / engineering is command-based AND templated
    # (the "шаблон" marker is the actual honesty signal — assert it directly,
    # not behind an `or` that a stray "/engineer" mention could satisfy).
    assert "шаблон" in P.lower()
    assert "/brain" in P
    assert "/engineer" in P


def test_prompt_keeps_persona_photo_as_stub():
    # must stay in the "not connected / stub" section, not be promoted into the
    # "ЧТО ТЫ РЕАЛЬНО УМЕЕШЬ" capabilities list.
    assert "generate_persona_photo" in P
    stub_header = P.index("ЧТО ПОКА НЕ ПОДКЛЮЧЕНО")
    assert P.index("generate_persona_photo") > stub_header
