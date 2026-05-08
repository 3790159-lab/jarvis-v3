# RUN_JARVIS.md — Полный гайд по запуску

## Быстрый старт

```powershell
.\start_jarvis.ps1
```

Откроется 2 окна: **Jarvis Backend :8010** и **Jarvis Telegram Bot**.  
Проверь: `curl http://127.0.0.1:8010/health` должен вернуть `{"status":"ok"}`.

---

## Способ 1: start_jarvis.ps1 (рекомендуемый)

```powershell
# Запуск обоих сервисов
.\start_jarvis.ps1

# Только backend
.\start_jarvis.ps1 -BackendOnly

# Только бот
.\start_jarvis.ps1 -BotOnly

# Другой порт (не нужно менять .env)
.\start_jarvis.ps1 -Port 8011
```

Скрипт:
1. Загружает `.env`
2. Устанавливает `BACKEND_BASE_URL=http://127.0.0.1:8010`
3. Запускает backend в новом окне через Windows Terminal или cmd
4. Через 3 секунды запускает бот в отдельном окне

---

## Способ 2: Ручной запуск (2 отдельных терминала)

**Терминал 1 — Backend:**
```powershell
cd "путь\к\supervisor_v1_5_smart_telegram"
$env:BACKEND_BASE_URL = "http://127.0.0.1:8010"
$env:PYTHONPATH = $PWD
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

**Терминал 2 — Telegram Bot:**
```powershell
cd "путь\к\supervisor_v1_5_smart_telegram"
$env:BACKEND_BASE_URL = "http://127.0.0.1:8010"
$env:PYTHONPATH = $PWD
.\.venv\Scripts\python.exe tools\jarvis_smart_telegram_control.py
```

---

## Способ 3: Windows Terminal (вкладки)

```powershell
# Открывает 2 вкладки автоматически
wt.exe new-tab --title "Jarvis Backend" powershell.exe -NoProfile -NoExit -Command {
    $env:BACKEND_BASE_URL="http://127.0.0.1:8010"; $env:PYTHONPATH="."
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
}
```

---

## Как проверить что всё работает

```powershell
# 1. Backend health
curl http://127.0.0.1:8010/health
# Ожидаем: {"status":"ok"} или {"ok":true}

# 2. Список агентов (через Telegram)
# Напиши в Telegram: /agents
# Ожидаем: 13 агентов в реестре

# 3. Self-check (через Telegram)
# Напиши в Telegram: /diag
# Ожидаем: все ✅
```

---

## Как остановить

- **Ctrl+C** в обоих окнах (backend и bot)
- Или закрой окна крестиком — боты завершатся корректно

---

## Команды Telegram (Block D2)

| Команда | Описание |
|---------|----------|
| `/diag` | Self-check: 8 проверок системы |
| `/mesh` | Smart Router — управление режимами |
| `/cowork status` | Статус Cowork watcher |
| `/cowork send <задача>` | Отправить задачу в Cowork |
| `/n8n list` | Список n8n workflows |
| `/n8n run <id>` | Запустить workflow |
| `/n8n status <exec_id>` | Статус выполнения |
| `/n8n enable/disable <id>` | Вкл/выкл workflow |
| Голосовое сообщение | Транскрипция → обработка (нужен OPENAI_API_KEY) |
| Фото без подписи | Vision анализ (нужен ANTHROPIC_API_KEY) |
| "Столица Франции?" | Быстрый ответ через Claude Haiku (не Perplexity) |

### MCP Server для Claude Desktop

Jarvis можно добавить как MCP сервер (см. `MCP_SERVER_SETUP.md`):
```json
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["scripts/run_mcp_server.py"],
      "env": {"JARVIS_BACKEND": "http://127.0.0.1:8010"}
    }
  }
}
```

---

## Как обновить код

```powershell
git pull
# Перезапусти: .\start_jarvis.ps1
```

Зависимости изменились?
```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

---

## Troubleshooting

### Бот видит "Backend DOWN"
**Причина:** `BACKEND_BASE_URL` в `.env` указывает на старый порт (8015).  
**Решение:** Убедись что в `.env`:
```
BACKEND_BASE_URL=http://127.0.0.1:8010
JARVIS_BASE_URL=http://127.0.0.1:8010
```
Или запускай через `start_jarvis.ps1` — он всегда переопределяет это значение.

### Окна не появляются
**start_jarvis.ps1** использует Windows Terminal → cmd → powershell в порядке приоритета.  
Попробуй **Способ 2** (ручной запуск) — он всегда работает.

### Port already in use
```powershell
# Найти процесс на порту 8010
netstat -ano | findstr :8010
# Остановить (замени PID)
Stop-Process -Id <PID>
```

### ModuleNotFoundError
```powershell
# Убедись что используешь venv python
.\.venv\Scripts\python.exe -c "import fastapi; print('OK')"

# Если ошибка — переустанови зависимости
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### python не найден
Используй прямой путь: `.\.venv\Scripts\python.exe`  
Системный python на этом PC — pythoncore-3.12-64, не в PATH как `python`.

---

## Структура сервисов

| Сервис | Порт | Команда |
|--------|------|---------|
| FastAPI Backend | 8010 | `uvicorn app.main:app --port 8010` |
| Telegram Bot | — | `python tools/jarvis_smart_telegram_control.py` |
| n8n (опционально) | 5678 | Docker контейнер |

---

## Для разработчика

- **Архитектура и планы:** [BLOCK_D_PLAN.md](BLOCK_D_PLAN.md)
- **История Block C:** [SESSION_SUMMARY_BLOCK_C.md](SESSION_SUMMARY_BLOCK_C.md)
- **Тесты:** `.\venv\Scripts\python.exe -m pytest tests/ -q`
- **Cowork Bridge setup:** [COWORK_BRIDGE_SETUP.md](COWORK_BRIDGE_SETUP.md)
