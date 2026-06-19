"""Centralised environment bootstrap for every runtime entry point.

Import this ONCE, as early as possible, from any standalone process that needs
the project's secrets in ``os.environ`` *before* other modules read them:

    from app import env_bootstrap  # noqa: F401  (side-effect: loads .env files)

It is the single source of truth for env loading, replacing ad-hoc
``load_dotenv`` calls scattered across the bot, sniper and helper scripts.

What it does (idempotently — safe to import many times):
  1. Puts the project root on ``sys.path`` so ``import app.*`` works when a
     script is run directly from a subdirectory (fixes the sniper's
     ``ModuleNotFoundError: No module named 'app'``).
  2. Loads ``.env`` then ``.env.runpod`` into ``os.environ``.

Precedence (matches the pydantic ``SettingsConfigDict(env_file=(.env,
.env.runpod))`` order used by runpod_config): a real shell export wins over
``.env``; ``.env.runpod`` wins over ``.env``. In practice the two files hold
disjoint keys (TELEGRAM_*/OPENAI_* vs RUNPOD_*/COMFYUI_*), so the only thing
that actually matters here is that *both* files get loaded.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILES = (
    (_PROJECT_ROOT / ".env", False),         # do not clobber real shell exports
    (_PROJECT_ROOT / ".env.runpod", True),   # runpod file wins over .env
)
_LOADED = False


def _ensure_path() -> None:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _manual_load(path: Path, override: bool) -> None:
    """Minimal .env parser used only if python-dotenv is unavailable.

    ``utf-8-sig`` strips a leading BOM (``.env.runpod`` ships with one).
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = val


def load_env() -> None:
    """Load .env + .env.runpod into os.environ. Idempotent."""
    global _LOADED
    _ensure_path()
    if _LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        for path, override in _ENV_FILES:
            _manual_load(path, override)
    else:
        for path, override in _ENV_FILES:
            if path.exists():
                load_dotenv(path, override=override, encoding="utf-8-sig")
    _LOADED = True


# Load on import so a bare `from app import env_bootstrap` is enough.
load_env()
