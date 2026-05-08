from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.core.bootstrap_utf8  # noqa: F401
from app.core.text_utils import write_text_utf8, read_text_utf8, clean_text

base = PROJECT_ROOT / "jarvis_stage3_artifacts" / "generated_projects" / "managed_autonomous_api" / "artifacts" / "output"
base.mkdir(parents=True, exist_ok=True)

source_text = "Создай файл artifacts/output/telegram_test.txt и запиши туда: Jarvis Telegram test successful."
fixed_text = clean_text(source_text)

log_file = base / "telegram_task_output.txt"
target_file = base / "telegram_test.txt"

write_text_utf8(log_file, f"Telegram task received: {fixed_text}\n")
write_text_utf8(target_file, "Jarvis Telegram test successful.\n")

print(read_text_utf8(log_file))
print(read_text_utf8(target_file))
