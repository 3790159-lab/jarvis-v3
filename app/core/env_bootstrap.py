from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def bootstrap_env() -> str:
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    load_dotenv(env_path, override=False)
    return str(env_path)
