from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil
import re

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_fix_router_conflict_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

MAIN_FILE = PROJECT_ROOT / "app" / "main.py"
BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"

for path in [MAIN_FILE, BOT_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

main_text = MAIN_FILE.read_text(encoding="utf-8")

# Удаляем legacy import missions_router
main_text = re.sub(
    r"^from app\.api\.endpoints\.missions import router as missions_router\s*$\n?",
    "",
    main_text,
    flags=re.MULTILINE,
)

# Удаляем legacy include_router(missions_router)
main_text = re.sub(
    r"^app\.include_router\(missions_router\)\s*$\n?",
    "",
    main_text,
    flags=re.MULTILINE,
)

# Проверяем goals_router import
if "from app.api.goals_router import router as goals_router" not in main_text:
    main_text = main_text.replace(
        "from fastapi import FastAPI",
        "from fastapi import FastAPI\nfrom app.api.goals_router import router as goals_router"
    )

# Проверяем responses_router import
if "from app.api.responses import router as responses_router" not in main_text:
    main_text = main_text.replace(
        "from fastapi import FastAPI",
        "from fastapi import FastAPI\nfrom app.api.responses import router as responses_router"
    )

# Оставляем только нужные include_router
lines = [line.rstrip() for line in main_text.splitlines()]
new_lines = []
seen_goals = False
seen_responses = False

for line in lines:
    if line.strip() == "app.include_router(goals_router)":
        if not seen_goals:
            new_lines.append(line)
            seen_goals = True
        continue
    if line.strip() == "app.include_router(responses_router)":
        if not seen_responses:
            new_lines.append(line)
            seen_responses = True
        continue
    new_lines.append(line)

main_text = "\n".join(new_lines).rstrip() + "\n"

if "app.include_router(responses_router)" not in main_text:
    main_text += "app.include_router(responses_router)\n"
if "app.include_router(goals_router)" not in main_text:
    main_text += "app.include_router(goals_router)\n"

MAIN_FILE.write_text(main_text, encoding="utf-8")

# Небольшое улучшение telegram_bot.py — красивый вывод /run
if BOT_FILE.exists():
    bot_text = BOT_FILE.read_text(encoding="utf-8")
    bot_text = bot_text.replace(
        '    bot.reply_to(msg, str(data))',
        '    if isinstance(data, dict):\n        bot.reply_to(msg, f"Mission result\\nmission_id: {data.get(\'mission_id\')}\\nstatus: {data.get(\'status\')}\\nsummary: {data.get(\'summary\')}")\n    else:\n        bot.reply_to(msg, str(data))'
    )
    BOT_FILE.write_text(bot_text, encoding="utf-8")

print(f"[OK] Updated: {MAIN_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
