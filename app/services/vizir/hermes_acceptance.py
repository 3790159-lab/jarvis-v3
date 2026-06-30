# -*- coding: utf-8 -*-
"""Vizir acceptance for the Hermes chat-generation knee (Path A).

DETERMINISTIC приёмка — no paid LLM judge. Given the artifact HTML, decide
whether Hermes actually produced a Jarvis-styled web chat that talks to our
backend. Returns an ``AcceptanceResult`` with the failing reasons so Vizir can
report precisely WHY a run was not accepted (retry / escalate to Daniil).

Kept separate from the handler: the handler runs Hermes + meters money; Vizir
owns "did it do it right?". A non-``completed`` run (max_iterations / cost_cap)
is rejected up front — a truncated agent cannot be trusted to have finished.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AcceptanceResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)


# Dark background: a background declaration whose colour starts dark (#0x/#1x),
# or a well-known dark token / near-black rgb().
_DARK_BG = re.compile(r"background(-color)?\s*:\s*#[01][0-9a-f]", re.I)
_DARK_TOKENS = ("#000", "#010", "#0a0a", "#0d0d", "#0f0f", "#111", "#121212",
                "#1a1a", "rgb(0,0,0", "rgb(10,10")
# A 6-digit near-black hex (first channel 0x/1x) defined ANYWHERE — covers themes
# that route the colour through a CSS custom property (background:var(--bg)).
_DARK_HEX6 = re.compile(r"#[01][0-9a-f]{5}\b", re.I)
_BG_VAR = re.compile(r"background[^;{}]*var\(", re.I)

_NEON_TOKENS = ("cyan", "aqua", "#0ff", "#00ffff", "#0ef", "#0df", "#0fe",
                "#22d3ee", "#00e5ff", "#06b6d4", "#00bcd4", "rgb(0,255,255")
# Cyan / electric-blue range: hex with low red + high blue (e.g. #00d4ff, #0099cc),
# or rgb/rgba with red=0 and a high blue channel (e.g. rgba(0,212,255,...)).
_NEON_HEX = re.compile(r"#0[0-9a-f][0-9a-f]{2}[c-f][0-9a-f]\b", re.I)
_NEON_RGB = re.compile(r"rgba?\(\s*0\s*,\s*\d{1,3}\s*,\s*(1[5-9]\d|2[0-5]\d)", re.I)


def _has_dark_background(low: str) -> bool:
    if any(t in low for t in _DARK_TOKENS) or _DARK_BG.search(low):
        return True
    # CSS-variable themes: a near-black hex defined + backgrounds set via var()
    return bool(_DARK_HEX6.search(low) and _BG_VAR.search(low))


def _has_neon_accent(low: str) -> bool:
    return (any(t in low for t in _NEON_TOKENS)
            or bool(_NEON_HEX.search(low)) or bool(_NEON_RGB.search(low)))


def check_chat_acceptance(html: str) -> AcceptanceResult:
    """Deterministic structural/style check of the generated chat HTML."""
    h = html or ""
    low = h.lower()
    reasons: list[str] = []

    if "<html" not in low or "</html>" not in low:
        reasons.append("not an HTML document (missing <html>…</html>)")
    if "const api_url" not in low:
        reasons.append("missing `const API_URL` endpoint constant")
    if "8010" not in low:
        reasons.append("does not target backend port 8010")
    if "fetch(" not in low and "xmlhttprequest" not in low:
        reasons.append("no JS POST mechanism (fetch/XMLHttpRequest)")
    if "<input" not in low and "<textarea" not in low:
        reasons.append("no message input field (<input>/<textarea>)")
    if "<button" not in low and "keydown" not in low and "keypress" not in low:
        reasons.append("no send trigger (button or Enter key handler)")
    if not re.search(r"(message|chat|msg)", low):
        reasons.append("no messages container")
    if not re.search(r"(online|offline|онлайн|оффлайн|status|статус)", low):
        reasons.append("no online/offline status indicator")
    if not _has_dark_background(low):
        reasons.append("no dark theme background")
    if not _has_neon_accent(low):
        reasons.append("no neon cyan/blue accent")

    return AcceptanceResult(accepted=not reasons, reasons=reasons)


def accept_hermes_chat(value: dict) -> AcceptanceResult:
    """Vizir-side acceptance of a Hermes handler result ``value`` (the dict from
    ``HandlerResult.result``). Rejects a non-completed run, then runs the
    deterministic chat check on the final HTML (final_response, else artifact)."""
    reasons: list[str] = []
    stopped = value.get("stopped_reason")
    if stopped and stopped != "completed":
        reasons.append(f"run did not complete (stopped_reason={stopped})")

    html = value.get("final_response") or ""
    if not html:
        path = value.get("artifact_path")
        if path and Path(path).exists():
            html = Path(path).read_text(encoding="utf-8", errors="replace")

    inner = check_chat_acceptance(html)
    reasons.extend(inner.reasons)
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
