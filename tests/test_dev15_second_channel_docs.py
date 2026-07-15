# -*- coding: utf-8 -*-
"""DEV-15: second-channel-before-touching-cloudflared rule must actually be
documented, not just exist in the working tree of whoever wrote it.

Mirrors tests/test_dev1_session_handoff_docs.py - these are docs read by CC
itself before it touches cloudflared/tunnel/network, not by application
code, so nothing else would notice if the rule silently regressed away.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
SECOND_CHANNEL_DOC = REPO_ROOT / "docs" / "REMOTE_ACCESS_SECOND_CHANNEL.md"


def test_claude_md_instructs_checking_second_channel_before_network_ops():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "Второй канал доступа" in text
    assert "check_remote_status.ps1" in text
    assert "cloudflared" in text


def test_claude_md_points_to_second_channel_doc():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "REMOTE_ACCESS_SECOND_CHANNEL.md" in text


def test_second_channel_doc_documents_tailscale_decision_and_live_test():
    text = SECOND_CHANNEL_DOC.read_text(encoding="utf-8")
    assert "Tailscale" in text
    assert "RustDesk" in text and "AnyDesk" in text
    assert "Stop-Service cloudflared" in text
