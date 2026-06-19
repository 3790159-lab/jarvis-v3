# -*- coding: utf-8 -*-
"""Detached FastAPI backend entry point for the JarvisBackendGuardian task.

Loaded the same way the sniper bootstraps: put the project root on sys.path
(P1), then import the canonical env bootstrap which loads .env + .env.runpod
into os.environ (P2) BEFORE the app is imported. This means the launcher
needs to inject NO environment variables of its own — all secrets/config come
from the .env files via env_bootstrap.

Then serve ``app.main:app`` with uvicorn. Run by
``scripts/backend_guardian_detached.ps1``; can also be run standalone:

    python scripts/run_backend_detached.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# P1: make `import app.*` work when run directly from scripts/ (mirrors the
# sniper preamble — needed before we can import env_bootstrap itself).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import app.env_bootstrap  # noqa: E402,F401  side-effect: loads .env + .env.runpod

import uvicorn  # noqa: E402

HOST = "127.0.0.1"
PORT = 8010


def main() -> None:
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
