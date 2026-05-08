from app import settings

print("OPENAI key loaded:", bool(settings.OPENAI_API_KEY))
print("Telegram token loaded:", bool(settings.TELEGRAM_BOT_TOKEN))

settings.validate_settings()
print("Settings validation: OK")