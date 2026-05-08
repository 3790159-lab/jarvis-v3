from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil
import re

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_respond_endpoint_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

MAIN_FILE = PROJECT_ROOT / "app" / "main.py"
API_DIR = PROJECT_ROOT / "app" / "api"
RESPONSES_FILE = API_DIR / "responses.py"

for path in [MAIN_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

API_DIR.mkdir(parents=True, exist_ok=True)

responses_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["responses"])


class RespondRequest(BaseModel):
    text: str | None = None
    message: str | None = None
    user_text: str | None = None
    input: str | None = None


def pick_text(payload: RespondRequest) -> str:
    for value in (payload.text, payload.message, payload.user_text, payload.input):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def simple_local_reply(text: str) -> tuple[str, str, str, float]:
    normalized = text.strip().lower()

    greetings = ("привет", "здравствуйте", "добрый день", "добрый вечер", "hello", "hi")
    if any(token in normalized for token in greetings):
        return (
            "Привет! Я Jarvis V3. Сейчас я умею проверять backend, принимать цели, обрабатывать обычные сообщения через /api/respond и работать как Telegram-интерфейс для supervisor. Дальше меня можно усилить умным роутингом, mission flow и LLM-мозгом.",
            "qa",
            "local_qa",
            0.93,
        )

    if "что ты умеешь" in normalized or "what can you do" in normalized:
        return (
            "Сейчас я умею: 1) проверять здоровье backend; 2) принимать обычные сообщения из Telegram; 3) создавать goal через /goal; 4) принимать задачи через /jarvis; 5) отвечать простыми локальными ответами через backend endpoint /api/respond. Следующий шаг — подключить умный роутинг и LLM.",
            "qa",
            "local_qa",
            0.95,
        )

    if "улучш" in normalized or "improve" in normalized:
        return (
            "Я бы усилил себя в трёх направлениях: 1) умный роутинг — различать вопрос, задачу и mission; 2) надёжность — retries, lock, health-restart, structured logs; 3) интеллект — подключение OpenAI или Ollama, память и многошаговое планирование.",
            "qa",
            "local_qa",
            0.91,
        )

    if "health" in normalized or "здоров" in normalized:
        return (
            "Backend маршрут /api/respond работает. Telegram bot подключён. Следующий шаг — проверить mission flow и добавить умный AI-routing.",
            "qa",
            "local_qa",
            0.88,
        )

    task_markers = (
        "создай", "сделай", "запусти", "напиши", "create", "make", "run", "write"
    )
    if any(marker in normalized for marker in task_markers):
        return (
            "Я понял задачу. Сейчас базовый /api/respond отвечает локально. Для реального выполнения задач лучше использовать /jarvis <задача> или /goal <цель>, а следующим шагом мы можем научить backend автоматически превращать такие сообщения в mission.",
            "task",
            "local_router",
            0.84,
        )

    if normalized in {"2+2", "сколько будет 2+2", "what is 2+2"}:
        return ("2 + 2 = 4.", "qa", "local_qa", 0.99)

    return (
        f"Я получил сообщение: {text}\n\nСейчас /api/respond уже работает в локальном режиме. Следующий шаг — подключить умный AI-routing и реальную обработку задач через supervisor.",
        "qa",
        "local_fallback",
        0.72,
    )


@router.post("/respond")
def respond(payload: RespondRequest) -> dict[str, Any]:
    text = pick_text(payload)
    if not text:
        return {
            "reply": "Пустой запрос.",
            "mode": "qa",
            "source": "local_validation",
            "confidence": 1.0,
        }

    reply, mode, source, confidence = simple_local_reply(text)
    return {
        "reply": reply,
        "mode": mode,
        "source": source,
        "confidence": confidence,
    }
'''

RESPONSES_FILE.write_text(responses_code, encoding="utf-8", newline="\n")

main_text = MAIN_FILE.read_text(encoding="utf-8")

if "from app.api.responses import router as responses_router" not in main_text:
    fastapi_import = "from fastapi import FastAPI"
    replacement = fastapi_import + "\n\nfrom app.api.responses import router as responses_router"
    main_text = main_text.replace(fastapi_import, replacement)

if "app.include_router(responses_router)" not in main_text:
    mission_line = "app.include_router(missions_router)"
    if mission_line in main_text:
        main_text = main_text.replace(mission_line, mission_line + "\napp.include_router(responses_router)")
    else:
        main_text += "\napp.include_router(responses_router)\n"

MAIN_FILE.write_text(main_text, encoding="utf-8", newline="\n")

print(f"[OK] Created: {RESPONSES_FILE}")
print(f"[OK] Updated: {MAIN_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
