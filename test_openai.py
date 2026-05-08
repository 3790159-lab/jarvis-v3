from app.ai_provider import AIProvider

provider = AIProvider()
result = provider.ask("Привет. Ответь коротко: работает ли OpenAI подключение?")

print(result)