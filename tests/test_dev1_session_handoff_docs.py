# -*- coding: utf-8 -*-
"""DEV-1: session-handoff + stop-limit rules must actually be documented.

Guards against the CLAUDE.md rule or the state/cc_session_context.md schema
template silently regressing (deleted/edited away) in a future change -
these are read by CC itself at session start, not by application code, so
nothing else would notice if they disappeared.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
TEMPLATE = REPO_ROOT / "docs" / "cc_session_context.template.md"


def test_claude_md_instructs_reading_session_context_at_start():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "cc_session_context.md" in text
    assert "ОБЯЗАНА первым делом при старте прочитать" in text


def test_claude_md_documents_start_cc_ps1():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "scripts/start_cc.ps1" in text
    assert "tmux" in text
    assert "WSL" in text


def test_claude_md_documents_stop_limit_escalation():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "2 итерации" in text
    assert "эскалировать" in text
    assert "принять" in text and "чинить уровнем" in text and "отложить" in text


def test_claude_md_documents_integration_marker_for_wsl_tmux_tests():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "pytest.mark.integration" in text
    assert "SKIP" in text


def test_session_context_template_has_required_schema_fields():
    text = TEMPLATE.read_text(encoding="utf-8")
    for heading in ("## Арка", "## Сделано", "## Ждёт", "## Открытые confirm", "## iteration_counter"):
        assert heading in text, f"missing section: {heading}"
