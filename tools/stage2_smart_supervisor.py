from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_stage2_smart_supervisor_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"

shutil.copy2(BOT_FILE, BACKUP_DIR / "telegram_bot.py")

code = BOT_FILE.read_text(encoding="utf-8")

# === Добавляем last mission storage ===
if "LAST_MISSION_FILE" not in code:
    insert = '''

LAST_MISSION_FILE = Path("state/last_mission.txt")


def save_last_mission(mission_id: str):
    try:
        LAST_MISSION_FILE.write_text(mission_id, encoding="utf-8")
    except Exception:
        pass


def load_last_mission() -> str | None:
    try:
        if LAST_MISSION_FILE.exists():
            return LAST_MISSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None
'''
    code = code.replace("LOG.info(\"Telegram bot initialized\")", insert + "\nLOG.info(\"Telegram bot initialized\")")

# === Сохраняем последнюю mission после goal ===
code = code.replace(
    "return format_goal_response(data, status)",
    "result = format_goal_response(data, status)\n    if isinstance(data, dict) and data.get('mission_id'):\n        save_last_mission(data['mission_id'])\n    return result"
)

# === Улучшаем /mission ===
code = code.replace(
    "if not mission_id:",
    "if not mission_id or '<' in mission_id:\n        mission_id = load_last_mission()\n        if not mission_id:\n            bot.reply_to(message, 'Укажи mission_id или сначала создай goal')\n            return"
)

# === Улучшаем /run ===
code = code.replace(
    "if not mission_id:",
    "if not mission_id or '<' in mission_id:\n        mission_id = load_last_mission()\n        if not mission_id:\n            bot.reply_to(message, 'Нет mission для запуска')\n            return"
)

# === Улучшаем /logs ===
code = code.replace(
    "if not mission_id:",
    "if not mission_id or '<' in mission_id:\n        mission_id = load_last_mission()\n        if not mission_id:\n            bot.reply_to(message, 'Нет mission для логов')\n            return"
)

BOT_FILE.write_text(code, encoding="utf-8")

print("[OK] Stage 2 Smart Supervisor applied")
print("[OK] Backup:", BACKUP_DIR)
