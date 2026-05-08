# -*- coding: utf-8 -*-
"""Tests for bolt_diy_health.py (Phase L.2)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_get_bolt_url_returns_localhost():
    from app.services.bolt_diy_health import get_bolt_url
    url = get_bolt_url()
    assert "localhost" in url or "127.0.0.1" in url
    assert "5173" in url


def test_check_bolt_running_returns_true_on_200():
    from app.services.bolt_diy_health import check_bolt_running
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = check_bolt_running(timeout=1)
    assert result is True


def test_check_bolt_running_returns_false_on_connection_error():
    from app.services.bolt_diy_health import check_bolt_running
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = check_bolt_running(timeout=1)
    assert result is False


def test_check_bolt_running_returns_false_on_timeout():
    from app.services.bolt_diy_health import check_bolt_running
    import socket
    with patch("urllib.request.urlopen", side_effect=TimeoutError()):
        result = check_bolt_running(timeout=1)
    assert result is False


def test_format_bolt_not_running_message_russian():
    from app.services.bolt_diy_health import format_bolt_not_running_message
    msg = format_bolt_not_running_message()
    assert isinstance(msg, str)
    assert len(msg) > 20
    # Should contain pnpm or bolt instructions
    assert "pnpm" in msg or "bolt" in msg.lower()


def test_format_bolt_running_message():
    from app.services.bolt_diy_health import format_bolt_running_message
    msg = format_bolt_running_message()
    assert "5173" in msg or "localhost" in msg
