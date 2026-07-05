# -*- coding: utf-8 -*-
"""PII scrubbing + PII-safe Telegram report building.

Monitoring output must carry ONLY the extracted content + cost + steps into
Telegram — never cookies, auth headers, bearer/API tokens, raw DOM, storage
state, or ``sensitive_data`` values. See plan §5bis.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .engine import BrowserResult

_MASK = "***"
_PATTERNS = [
    (re.compile(r'(?im)^(set-cookie|cookie)\s*:\s*.*$'), r'\1: ' + _MASK),   # cookie lines
    (re.compile(r'(?i)(authorization\s*:\s*bearer\s+)\S+'), r'\1' + _MASK),  # auth header
    (re.compile(r'(?i)\bsk-[a-z0-9-]{8,}'), _MASK),                          # api tokens
    (re.compile(r'(?i)\bbearer\s+[a-z0-9._\-]{10,}'), 'Bearer ' + _MASK),    # bare bearer
]


def scrub_pii(text: str, secrets: Optional[List[str]] = None) -> str:
    """Mask cookies/auth headers/tokens and any explicit secret values."""
    out = text or ""
    for s in (secrets or []):
        if s:
            out = out.replace(s, _MASK)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def build_report(res: BrowserResult, secrets: Optional[List[str]] = None) -> str:
    """TG-safe report: extracted (scrubbed) + cost + steps. No dom/headers ever."""
    extract = scrub_pii(res.extracted or "(ничего не извлечено)", secrets)
    return ("🌐 Готово (%s).\nШагов: %d · Стоимость: $%.2f\n\n%s"
            % (res.stopped_reason, res.steps, res.cost_usd, extract))
