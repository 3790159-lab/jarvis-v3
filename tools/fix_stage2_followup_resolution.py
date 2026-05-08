from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_stage2_followup_fix_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"
shutil.copy2(BOT_FILE, BACKUP_DIR / "telegram_bot.py")

code = BOT_FILE.read_text(encoding="utf-8")

old_logs = """    if looks_like_logs_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, для какой mission показать логи.")
            return
        status, data = get_logs_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = str(data.get("logs", "No logs found."))[:3500]
        else:
            reply = f"Logs error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply[:1000], intent="followup_logs", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

new_logs = """    current_memory = memory.get()
    has_context_mission = bool(current_memory.get("last_mission_id") or load_last_mission())

    if looks_like_logs_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, для какой mission показать логи.")
            return
        status, data = get_logs_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = str(data.get("logs", "No logs found."))[:3500]
        else:
            reply = f"Logs error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply[:1000], intent="followup_logs", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

old_run = """    if looks_like_run_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, какую mission запускать.")
            return
        status, data = run_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_run_result(data)
        else:
            reply = f"Run error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_run", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

new_run = """    if looks_like_run_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, какую mission запускать.")
            return
        status, data = run_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_run_result(data)
        else:
            reply = f"Run error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_run", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

old_status = """    if looks_like_status_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, о какой mission речь.")
            return
        status, data = get_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_mission_info(data)
        else:
            reply = f"Mission error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_status", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

new_status = """    if looks_like_status_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, о какой mission речь.")
            return
        status, data = get_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_mission_info(data)
        else:
            reply = f"Mission error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_status", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return
"""

code = code.replace(old_logs, new_logs)
code = code.replace(old_run, new_run)
code = code.replace(old_status, new_status)

BOT_FILE.write_text(code, encoding="utf-8")

print("[OK] Stage 2 follow-up logic improved")
print(f"[OK] Backup: {BACKUP_DIR}")
