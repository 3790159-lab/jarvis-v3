from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_operator_task_center import JarvisOperatorTaskCenter, read_env_file
from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent
from app.services.jarvis_brain_executor import JarvisBrainExecutor


def tg_call(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode("utf-8") if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def send(token: str, chat_id: str, text: str) -> None:
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or [text]
    for chunk in chunks:
        tg_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        })


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-zа-яё0-9\s_-]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    a = normalize_text(a)
    b = normalize_text(b)
    if not a or not b:
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    words_a = set(a.split())
    words_b = set(b.split())
    overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
    return max(direct, overlap)


def n8n_agent() -> JarvisN8nSuperAgent:
    return JarvisN8nSuperAgent(PROJECT_ROOT)


def n8n_readiness_text() -> str:
    agent = n8n_agent()
    r = agent.specialist.readiness()
    return (
        "🧩 n8n readiness\n\n"
        f"Статус: {r.status}\n"
        f"Base URL: {r.base_url}\n"
        f"API key: {'есть' if r.api_key_present else 'нет'}\n"
        f"HTTP root: {'ok' if r.http_root_ok else 'fail'}\n"
        f"Public API: {'ok' if r.public_api_ok else 'fail'}\n\n"
        f"Следующий шаг: {r.next_best_action}"
    )


def format_n8n_latest() -> str:
    agent = n8n_agent()
    latest = agent.latest_run()
    if not latest.get("found"):
        return "n8n Super Agent ещё не запускался."

    run = latest.get("run", {})
    return (
        "🧠 Последний n8n Super Agent run\n\n"
        f"Статус: {run.get('status')}\n"
        f"Тип workflow: {run.get('workflow_kind')}\n"
        f"Workflow ID: {run.get('workflow_id')}\n"
        f"Задача: {run.get('user_task')}\n\n"
        f"{run.get('summary')}"
    )


def run_n8n_workflow(task: str, workflow_kind: str | None = None) -> str:
    agent = n8n_agent()
    task = task.strip() or "Create and test Jarvis n8n workflow"

    result = agent.run(
        user_task=task,
        workflow_kind=workflow_kind,
        activate=True,
        test_webhook=True,
    )

    workflow_url = f"{agent.specialist.base_url}/workflow/{result.workflow_id}" if result.workflow_id else "not available"

    if result.status == "tested":
        header = "✅ Готово: workflow создан, активирован и протестирован."
    elif result.status == "active":
        header = "⚠️ Workflow создан и активирован, но тест требует проверки."
    else:
        header = "⚠️ Workflow создан не полностью."

    next_actions = []
    if result.status == "tested":
        next_actions.append("Можно открыть workflow в n8n и посмотреть схему.")
        next_actions.append("Можно усложнить pipeline: добавить Telegram, Google Sheets, Gmail или AI-анализ.")
        next_actions.append("Можно использовать production webhook в других автоматизациях.")
    else:
        next_actions.append("Нужно открыть workflow и посмотреть ошибку выполнения.")
        next_actions.append("Можно запустить тест ещё раз после правки.")

    return (
        f"{header}\n\n"
        f"Тип: {result.workflow_kind}\n"
        f"Workflow ID: {result.workflow_id}\n"
        f"Открыть в n8n:\n{workflow_url}\n\n"
        f"{result.summary}\n\n"
        "Что можно сделать дальше:\n"
        + "\n".join(f"• {x}" for x in next_actions)
    )


def executions_root() -> Path:
    return PROJECT_ROOT / "jarvis_stage3_artifacts" / "operator_task_center" / "executions"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_execution() -> dict:
    root = executions_root()
    if not root.exists():
        return {"found": False}
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"found": False}
    data = read_json(files[0]) or {}
    data["found"] = True
    return data


def execution_by_task_id(task_id: str) -> dict:
    path = executions_root() / f"{task_id}.json"
    if not path.exists():
        return {"found": False, "task_id": task_id}
    data = read_json(path) or {}
    data["found"] = True
    return data


def format_execution(data: dict) -> str:
    if not data.get("found"):
        return "Я не нашёл результат этой задачи."

    return (
        "📄 Результат задачи\n\n"
        f"ID: {data.get('task_id')}\n"
        f"Loop: {data.get('loop_run_id')}\n"
        f"Статус: {data.get('loop_status')}\n"
        f"Решение: {data.get('recommended_action')}\n"
        f"Итог: {data.get('mutation_outcome')}\n\n"
        f"{data.get('operator_message')}\n\n"
        f"Следующий шаг: {data.get('next_best_action')}"
    )


def find_task(center: JarvisOperatorTaskCenter, query: str) -> dict:
    tasks = center.list_tasks()
    q = normalize_text(query)
    best = None
    best_score = 0.0

    for t in tasks:
        hay = " ".join([
            str(t.get("task_id", "")),
            str(t.get("title", "")),
            str(t.get("objective", "")),
            str(t.get("status", "")),
            str(t.get("stage", "")),
        ])
        score = similarity(q, hay)
        if score > best_score:
            best = t
            best_score = score

    if not best or best_score < 0.12:
        return {"found": False, "query": query, "score": best_score}

    return {"found": True, "score": round(best_score, 3), "task": best}


def format_task_status(match: dict) -> str:
    if not match.get("found"):
        return "Я не нашёл похожую задачу. Попробуй: /find n8n"

    t = match["task"]
    return (
        "📌 Нашёл задачу:\n\n"
        f"ID: {t.get('task_id')}\n"
        f"Название: {t.get('title')}\n"
        f"Статус: {t.get('status')}\n"
        f"Стадия: {t.get('stage')}\n"
        f"Прогресс: {t.get('progress')}%\n"
        f"Следующее действие: {t.get('next_action')}"
    )


def openai_chat(prompt: str, center: JarvisOperatorTaskCenter) -> str | None:
    env = read_env_file(PROJECT_ROOT)
    api_key = os.environ.get("OPENAI_API_KEY") or env.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL") or env.get("OPENAI_MODEL") or "gpt-4o-mini"
    if not api_key:
        return None

    status = center.operator_status()
    tasks = center.list_tasks()[:8]

    system = (
        "Ты Jarvis — AI-оператор проекта Daniil. Отвечай на русском, естественно, уверенно и конкретно. "
        "Не уходи от ответа. Предлагай конкретный следующий шаг. "
        "Ты умеешь: Telegram Bridge, Operator Task Center, Executor, Unified Autonomous Loop, "
        "Safe Mutation, Risk Engine, Memory, Explainability, n8n Super Agent. "
        "n8n Super Agent уже умеет создавать, деплоить, активировать и тестировать workflow, "
        "включая реальную HTTP Request node к внешнему API."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps({
                    "message": prompt,
                    "status": status,
                    "recent_tasks": tasks,
                }, ensure_ascii=False),
            },
        ],
        "temperature": 0.55,
        "max_tokens": 850,
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def classify_intent(text: str) -> str:
    lower = normalize_text(text)

    if lower.startswith("/"):
        return "command"

    if any(x in lower for x in ["как там", "статус задачи", "что с задачей", "задача про", "результат задачи"]):
        return "task_lookup"

    if any(x in lower for x in ["n8n", "воркфлоу", "workflow", "автоматизац", "пайплайн", "pipeline", "многошаг", "многоуров", "цепоч", "webhook"]):
        return "n8n"

    starts = ["сделай", "создай", "запусти", "проверь", "подготовь", "проанализируй", "исправь", "добавь", "настрой", "интегрируй", "выполни", "реализуй"]
    if any(lower.startswith(x) for x in starts):
        return "task_create"

    return "answer"


def choose_n8n_kind_from_text(text: str) -> str | None:
    lower = normalize_text(text)

    if any(x in lower for x in ["динамич", "dynamic", "сам выбери", "сам постро", "несколько сервис", "разные сервис", "интеллектуальн"]):
        return "dynamic_pipeline"

    if any(x in lower for x in ["pipeline", "пайплайн", "многошаг", "многоуров", "цепоч", "логическ", "webhook api отчет", "webhook api отчёт"]):
        return "multi_step_pipeline"

    if "telegram send" in lower or "send telegram" in lower or ("отправ" in lower and ("telegram" in lower or "телеграм" in lower)):
        return "telegram_send_message"

    if "telegram" in lower or "телеграм" in lower or "alert" in lower or "уведом" in lower:
        return "telegram_operator_alert"

    if "night" in lower or "ноч" in lower or "отч" in lower or "report" in lower or "лог" in lower:
        return "night_report_logger"

    if "http" in lower or "api" in lower or "внешн" in lower:
        return "http_request_probe"

    return None


def brain_executor() -> JarvisBrainExecutor:
    return JarvisBrainExecutor(PROJECT_ROOT)


def run_brain_task(text: str) -> str:
    result = brain_executor().execute(text, dry_run=False)
    return result.human_summary + "\n\nЧто дальше:\n" + "\n".join(f"• {x}" for x in result.next_actions)


def handle_message(center: JarvisOperatorTaskCenter, text: str) -> str:
    text = (text or "").strip()
    lower = normalize_text(text)
    intent = classify_intent(text)

    if text in {"/start", "/help", "help", "помощь"}:
        return (
            "🤖 Jarvis Operator на связи.\n\n"
            "Команды:\n"
            "/status — состояние системы\n"
            "/next — что дальше\n"
            "/tasks — задачи\n"
            "/find <текст> — найти задачу\n"
            "/task <текст> — поставить задачу\n"
            "/last_result — последний результат задачи\n"
            "/n8n — статус n8n\n"
            "/n8n_latest — последний n8n Super Agent результат\n"
            "/n8n_create <задача> — создать workflow по смыслу\n"
            "/n8n_http — создать внешний HTTP workflow\n"
            "/night — последняя night-сессия"
        )

    if text == "/status":
        s = center.operator_status()
        latest = s.get("latest_night_session", {})
        return (
            "📊 Состояние системы:\n\n"
            f"Активные задачи: {s.get('active_tasks')}\n"
            f"Завершённые: {s.get('completed_tasks')}\n"
            f"Ошибки: {s.get('failed_tasks')}\n"
            f"Последняя night-сессия: {latest.get('status')}\n"
            f"Apply: {latest.get('apply_count')}"
        )

    if text == "/next":
        return (
            "🧭 Я бы двигался так:\n"
            "• развивать многошаговые n8n pipeline;\\n"
            "• добавить безопасный выбор сервисов под задачу;\\n"
            "• подключить Telegram-send, Google Sheets и Gmail как реальные сервисные ноды;\\n"
            "• научить planner строить цепочки из нескольких сервисов без команд /n8n."
        )

    if text == "/tasks":
        tasks = center.list_tasks()[:12]
        if not tasks:
            return "Задач пока нет."
        return "📌 Задачи:\n" + "\n".join(
            f"• {t.get('title')} — {t.get('status')} / {t.get('stage')} / {t.get('progress')}%"
            for t in tasks
        )

    if text.startswith("/find "):
        return format_task_status(find_task(center, text.replace("/find ", "", 1).strip()))

    if text == "/last_result":
        return format_execution(latest_execution())

    if text.startswith("/result "):
        return format_execution(execution_by_task_id(text.replace("/result ", "", 1).strip()))

    if text == "/result":
        return "Укажи ID задачи: /result task_... Или проще: /find n8n"

    if text.startswith("/brain_run "):
        return run_brain_task(text.replace("/brain_run ", "", 1).strip())
    if text == "/n8n":
        return n8n_readiness_text()

    if text == "/n8n_latest":
        return format_n8n_latest()

    if text == "/n8n_http":
        return run_n8n_workflow("Create external HTTP request workflow and test it", workflow_kind="http_request_probe")

    if text.startswith("/n8n_create "):
        task = text.replace("/n8n_create ", "", 1).strip()
        kind = choose_n8n_kind_from_text(task)
        return run_n8n_workflow(task, workflow_kind=kind)

    if text == "/night":
        latest = center.latest_unified_night_session()
        if not latest.get("found"):
            return "Night-сессия пока не найдена."
        return (
            "🌙 Последняя night-сессия\n\n"
            f"ID: {latest.get('session_id')}\n"
            f"Статус: {latest.get('status')}\n"
            f"Apply: {latest.get('apply_count')}\n"
            f"Ошибки: {latest.get('failed_count')}\n"
            f"Итог: {latest.get('operator_summary')}"
        )

    if text.startswith("/task "):
        objective = text.replace("/task ", "", 1).strip()
        task = center.create_task(title=objective[:60], objective=objective, priority="normal")
        return (
            "✅ Принял задачу.\n\n"
            f"ID: {task.task_id}\n"
            f"Цель: {task.objective}\n"
            f"Статус: {task.status}\n\n"
            f"Проверить потом можно: /find {task.title[:25]}"
        )

    if intent == "task_lookup":
        cleaned = lower.replace("как там", " ").replace("статус задачи", " ").replace("задача про", " ").strip()
        return format_task_status(find_task(center, cleaned))

    if intent == "n8n":
        kind = choose_n8n_kind_from_text(text)
        return run_n8n_workflow(text, workflow_kind=kind)

    if intent == "task_create":
        task = center.create_task(title=text[:60], objective=text, priority="normal")
        return (
            "✅ Принял задачу и поставил в очередь.\n\n"
            f"ID: {task.task_id}\n"
            f"Задача: {task.objective}\n"
            f"Статус: {task.status}\n\n"
            f"Проверить можно без ID: «как там задача про {task.title[:20]}»"
        )

    llm = openai_chat(text, center)
    if llm:
        return llm

    return "Я понял. Могу ответить, поставить задачу через /task или создать n8n workflow через /n8n_create."


def main() -> int:
    env = read_env_file(PROJECT_ROOT)
    token = env.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = env.get("TELEGRAM_ALLOWED_CHAT_ID")

    if not token or not allowed_chat_id:
        print("Telegram token/chat_id missing in .env")
        return 1

    center = JarvisOperatorTaskCenter(PROJECT_ROOT)
    offset = None

    print("Jarvis Telegram n8n Control Bridge v4 started.")
    send(token, allowed_chat_id, "✅ Jarvis Telegram n8n Control Bridge v4 запущен. Команды: /n8n, /n8n_http, /n8n_create, /n8n_latest")

    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset

            data = tg_call(token, "getUpdates", params)
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id"))
                text = msg.get("text") or ""

                if chat_id != str(allowed_chat_id):
                    send(token, chat_id, "⛔ Этот чат не разрешён для Jarvis.")
                    continue

                reply = handle_message(center, text)
                send(token, chat_id, reply)

        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"bridge error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())