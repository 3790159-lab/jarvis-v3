from pathlib import Path

bot_path = Path("app/telegram_bot.py")
code = bot_path.read_text(encoding="utf-8")

# === добавляем универсальный обработчик ===
if "handle_all_messages" not in code:

    insert_code = """

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    text = message.text.strip()

    # игнорируем команды (они уже обрабатываются отдельно)
    if text.startswith("/"):
        return

    try:
        response = requests.post(
            f"{API_BASE_URL}/api/respond",
            json={"text": text},
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            reply = data.get("reply", "Нет ответа от системы")
        else:
            reply = f"Ошибка backend: {response.status_code}"

    except Exception as e:
        reply = f"Ошибка: {str(e)}"

    bot.reply_to(message, reply)
"""

    # вставляем в конец файла
    code += "\n" + insert_code
    bot_path.write_text(code, encoding="utf-8")
    print("[OK] Added universal message handler")

# === делаем /goal нормальной командой ===
if 'def handle_goal' not in code:

    goal_code = """

@bot.message_handler(commands=['goal'])
def handle_goal(message):
    text = message.text.replace("/goal", "").strip()

    if not text:
        bot.reply_to(message, "Напиши задачу после /goal")
        return

    try:
        response = requests.post(
            f"{API_BASE_URL}/api/goals",
            json={"objective": text},
            timeout=20
        )

        if response.status_code == 200:
            data = response.json()
            bot.reply_to(message, f"Goal создан: {data.get('goal_id')}")
        else:
            bot.reply_to(message, f"Ошибка: {response.status_code}")

    except Exception as e:
        bot.reply_to(message, f"Ошибка: {str(e)}")
"""

    code = bot_path.read_text(encoding="utf-8") + "\n" + goal_code
    bot_path.write_text(code, encoding="utf-8")
    print("[OK] Added /goal handler")

print("[DONE] Bot upgraded")
