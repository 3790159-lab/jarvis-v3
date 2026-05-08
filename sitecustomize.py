import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

_ROOT = Path(__file__).resolve().parent
_ENV = _ROOT / ".env"

try:
    if load_dotenv and _ENV.exists():
        load_dotenv(_ENV, override=False)
except Exception:
    pass

db_path = os.environ.get("DATABASE_PATH", "").strip()
if not db_path:
    db_path = str((_ROOT / "state" / "supervisor.db").resolve())

db_file = Path(db_path)
try:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if not db_file.exists():
        db_file.touch()
    os.environ["DATABASE_PATH"] = str(db_file)
except Exception:
    pass

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
