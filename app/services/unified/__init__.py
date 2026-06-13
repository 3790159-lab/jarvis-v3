# -*- coding: utf-8 -*-
"""Phase-4 *unified Jarvis* subsystem.

Home for the natural-language orchestration layer that turns Jarvis from a
command-matching bot into an LLM-routed assistant. Step 1 ships the
``llm_router`` package (Claude tool_use router + tool registry); later steps
add Computer Use, Office files, and live face-swap under the same namespace.

This package is intentionally separate from the legacy Ollama
``app.services.llm_router`` *module* (an intent classifier) — the two coexist
without collision.
"""
