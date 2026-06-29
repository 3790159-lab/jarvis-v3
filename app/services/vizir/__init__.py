# -*- coding: utf-8 -*-
"""Vizir — transport/driver-agnostic task coordinator (core).

Phase 1: core models, coordinator loop, money-gate (free + mock-charge),
product safeguards (per-task budget, actor, step policy). No real paid calls.

The core knows nothing about CC or Telegram; a thin driver renders its events
and approves phase transitions (CC-driver now, Jarvis-driver later).
"""
