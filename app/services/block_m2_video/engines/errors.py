# -*- coding: utf-8 -*-
"""Shared video-engine error classes.

The runner distinguishes RETRYABLE from BILLABLE failures by these bases, so a
new engine only needs to subclass the right one to get correct money behavior.
"""
from __future__ import annotations


class TransientVideoError(RuntimeError):
    """Retryable: 429 / network, NO prediction created -> safe to retry & sweep."""


class TerminalVideoError(RuntimeError):
    """Terminal/billable: completed-but-failed prediction or a 4xx -> NEVER retry."""
