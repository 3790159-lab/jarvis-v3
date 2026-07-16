from __future__ import annotations
from pathlib import Path
import re

CHATTER = Path(__file__).resolve().parents[2] / "chatter"
FORBIDDEN = re.compile(r"^\s*(from|import)\s+(app|tools)(\.|\s|$)", re.MULTILINE)

def test_no_imports_from_app_or_tools():
    offenders = []
    for py in CHATTER.rglob("*.py"):
        if FORBIDDEN.search(py.read_text(encoding="utf-8")):
            offenders.append(str(py.relative_to(CHATTER)))
    assert offenders == [], f"chatter/ must not import app/ or tools/: {offenders}"

def test_chatter_package_importable():
    import chatter  # noqa: F401
