# -*- coding: utf-8 -*-
"""bolt.diy health check and URL helpers."""
from __future__ import annotations

import urllib.request
import urllib.error

_BOLT_URL = "http://localhost:5173"


def get_bolt_url() -> str:
    return _BOLT_URL


def check_bolt_running(timeout: int = 5) -> bool:
    """Return True if bolt.diy is responding at localhost:5173."""
    try:
        req = urllib.request.Request(_BOLT_URL, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        try:
            req = urllib.request.Request(_BOLT_URL)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 500
        except Exception:
            return False


def format_bolt_not_running_message() -> str:
    """Russian instructions for starting bolt.diy."""
    return (
        "bolt.diy не запущен!\n\n"
        "Запусти его:\n"
        "1. Открой PowerShell\n"
        "2. cd C:\\Users\\Daniil Lapin\\Downloads\\bolt.diy\n"
        "3. pnpm run dev\n"
        "4. Подожди пока появится localhost:5173\n"
        "5. Попробуй команду снова\n\n"
        "bolt.diy нужен для генерации приложений через AI."
    )


def format_bolt_running_message() -> str:
    """Russian message when bolt.diy is running."""
    return f"bolt.diy запущен: {_BOLT_URL}"
