# -*- coding: utf-8 -*-
"""Vizir drivers — thin adapters that render core events and resolve approvals.

The core is driver-agnostic; a driver only formats events and wires a
Coordinator. CCDriver renders to a chat journal now; a JarvisDriver will render
to Telegram later (Phase 4) — same core, different driver.
"""
