from app import settings

print("RAW KEY:", repr(settings.OPENAI_API_KEY))
print("STARTS WITH sk:", settings.OPENAI_API_KEY.startswith("sk"))
print("KEY LENGTH:", len(settings.OPENAI_API_KEY))