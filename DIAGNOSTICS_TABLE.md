# DIAGNOSTICS_TABLE.md — Phase 7.A

Date: 2026-04-30  
Status: Discovery complete. No code changes made.

---

## 1. Блок intent="table" в боте

Файл: `tools/jarvis_smart_telegram_control.py`, строки 482–522

```
send(chat_id, "📊 Принял...")      ← сообщение пользователю НЕМЕДЛЕННО
data = backend_post(               ← блокирующий HTTP вызов к backend
    "/api/jarvis/telegram-tools/internet-table",
    {"query": ..., "max_results": 10, "send_to_telegram": True},
    timeout=300                    ← 5 минут
)
msg = "✅ Таблица готова.\n..."    ← ТОЛЬКО если data получен
send(chat_id, msg)
```

**Проблема**: `backend_post` вызывает `http_json`, у которой нет `try/except`.
Любая ошибка (backend DOWN, HTTP 500, timeout) — неперехваченное исключение.

---

## 2. Полная цепочка вызовов

```
Telegram user
  │ текст (напр. "создай таблицу AI сервисов")
  ▼
tools/jarvis_smart_telegram_control.py :: main()
  for upd in updates:
    offset += 1            ← offset обновляется ДО обработки
    handle(chat_id, text)
      classify_message() → intent="table"
      run_intent():
        send("📊 Принял...")           ← [1] пользователь видит это
        backend_post(timeout=300)      ← [2] блокирует поток на ≤5 мин
          └── http_json()              ← [3] urlopen БЕЗ try/except
                                           ↑ ИСКЛЮЧЕНИЕ ЗДЕСЬ если backend DOWN
                                           или HTTP 4xx/5xx
        [4] ИСКЛЮЧЕНИЕ уходит в стек
      handle() завершается с исключением
    main() :: except Exception as e:
      print("ERR:", repr(e))           ← [5] тихий лог в консоль
      time.sleep(3)                    ← [6] пауза и продолжение

  ↑ offset уже обновлён → сообщение НЕ переобрабатывается
  ↑ пользователь видит только [1], никогда не видит результата
```

---

## 3. Диагностика backend endpoint

### Роутер: зарегистрирован, но условно

`app/main.py`, строки 321–326:
```python
try:
    from app.routers.jarvis_telegram_file_tools_router import router as ...
    app.include_router(...)
except Exception as e:
    print("WARN: failed to include jarvis_telegram_file_tools_router:", e)
```

Если import упадёт — маршрут не зарегистрирован. Backend ответит **HTTP 404**.  
Сам import проверен — загружается успешно (если backend запущен).

### Backend в момент теста: **DOWN**

```
>>> GET http://127.0.0.1:8015/health
URLError: [WinError 10061] No connection could be made because
the target machine actively refused it
```

Это **первопричина** проблемы #1. Backend не запущен → `http_json` бросает
`URLError` → main loop тихо глотает → пользователь видит только "📊 Принял".

### Внутренняя цепочка в backend (когда он запущен)

```
POST /api/jarvis/telegram-tools/internet-table
  └── jarvis_telegram_file_tools_router :: internet_table()
        └── jarvis_telegram_file_tools :: build_internet_table()
              ├── internet_search(query)          timeout=60s  → Tavily
              ├── internet_research(query)        timeout=90s  → Perplexity
              ├── csv.DictWriter → CSV (всегда)
              ├── from openpyxl import Workbook   ← ПРОВАЛ
              │     except Exception:             ← тихий fallback на CSV
              │     table_path = csv_path         ← пользователь ждал .xlsx
              └── _send_telegram_document(table_path)
                    └── TELEGRAM_ALLOWED_CHAT_ID = os.getenv(...)
                          ← берёт из ENV BACKEND-процесса, не от запроса
```

---

## 4. Все env keys для генерации таблиц

| Переменная | Статус | Нужна для |
|---|---|---|
| `TAVILY_API_KEY` | ✅ есть в .env | `internet_search()` |
| `PERPLEXITY_API_KEY` | ✅ есть в .env | `internet_research()` |
| `TELEGRAM_BOT_TOKEN` | ✅ есть в .env | `_send_telegram_document()` |
| `TELEGRAM_ALLOWED_CHAT_ID` | ✅ есть в .env | `_send_telegram_document()` |
| `openpyxl` (пакет) | ❌ **НЕ УСТАНОВЛЕН** | Excel (.xlsx) генерация |

Все API-ключи присутствуют. **openpyxl не установлен** → таблица будет в CSV,
не в Excel. Это тихая деградация, не крэш.

---

## 5. Найденные проблемы по приоритету

### P0 — Backend не запущен (не код, а операционная проблема)
Пользователь запускает бот, но не запускает backend. Бот не сообщает об этом.

### P1 — `http_json` не имеет try/except (КРИТИЧНО)

```python
# СЕЙЧАС (ломает всё при любой ошибке backend):
def http_json(method, url, payload=None, timeout=180):
    req = urllib.request.Request(...)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # бросает при ошибке
        return json.loads(r.read().decode())

# Нужно:
def http_json(method, url, payload=None, timeout=180):
    try:
        req = urllib.request.Request(...)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "_error": str(e), "_error_type": type(e).__name__}
```

**Последствие**: любая ошибка (backend DOWN, HTTP 500/404, timeout) вызывает
исключение, которое поглощается в `main()` без уведомления пользователя.

### P2 — Нет проверки состояния backend перед долгими задачами

Бот не проверяет, доступен ли backend, перед отправкой "📊 Принял". Пользователь
ждёт 5 минут (timeout), потом тишина.

### P3 — Нет honest error reporting в run_intent для table

Нет ни одного `try/except` вокруг `backend_post` в блоке `intent == "table"`.
Ошибка никогда не передаётся пользователю.

### P4 — openpyxl не установлен → тихая деградация CSV

`build_internet_table` молча переключается на CSV без уведомления.
Пользователь ожидает .xlsx, получает (если всё прошло) .csv.

### P5 — `_send_telegram_document` использует статичный `TELEGRAM_ALLOWED_CHAT_ID`

Backend читает chat_id из своего ENV, не из запроса бота.
Если ENV на backend не совпадает — файл уходит не туда или не уходит вообще.
Текущее значение `237616472` совпадает с ботом → сейчас OK, но архитектурно хрупко.

### P6 — Нет async job / статуса (проблема #3)

Таблица — долгая операция (до 150 сек). Нет job_id, нет `/status` эндпоинта.
Повторный вопрос "ты выполнил?" уходит в research → Perplexity вместо проверки.

---

## 6. Проблема #2 — "Привет" → заглушка

`classify_message("Привет", {})`:
- Не попадает ни в одну категорию
- `raw.endswith("?")` → False
- `len(raw) > 70` → False
- Финал: `return {"intent": "chat", "query": raw}`

`run_intent("chat", ...)`:
```python
if intent == "chat":
    q = apply_language(query, state)
    send(chat_id, "💬 Понял. Дам ответ через интернет/brain, чтобы не гадать.")
    data = backend_post("/api/jarvis/tools/internet/research", {"query": q}, timeout=240)
    answer = data.get("answer") or "Не смог найти уверенный ответ."
    send(chat_id, answer)
```

Проблема: "Привет" отправляется в Perplexity как запрос исследования.
`apply_language` добавляет "Ответ и таблицу сделай на русском языке..." к "Привет".
Perplexity получает "Привет. Ответ и таблицу..." и возвращает что-то бессмысленное.

---

## 7. Что нужно для honest fallback (предложения)

### Минимальный фикс (одна функция):

Обернуть `http_json` в try/except и вернуть `{"ok": False, "_error": ...}`.
Добавить в каждый `run_intent` блок проверку `if not data.get("ok")` с сообщением.

```python
# Пример для table блока:
send(chat_id, "📊 Принял...")
data = backend_post("/api/jarvis/telegram-tools/internet-table", {...}, timeout=300)
if data.get("_error") or not data.get("ok"):
    send(chat_id, f"⚠️ Не смог получить таблицу: {data.get('_error', 'нет ответа от backend')}")
    return
# ... остальная логика
```

### Startup check (опционально):

При старте бота — проверить `GET /health`, предупредить оператора в консоли
если backend недоступен.

---

## 8. Краткая сводка

| Проблема | Причина | Где именно |
|---|---|---|
| Таблица не приходит | backend DOWN + `http_json` без try/except → тихий крэш | `tools/jstc.py::http_json` |
| "📊 Принял" без результата | send до call, ошибка после send не перехватывается | `run_intent`, блок `table` |
| "Привет" → заглушка | `intent="chat"` → Perplexity (не подходит для приветствий) | `classify_message` + `run_intent` |
| Нет статуса задачи | нет job_id, нет /status эндпоинта | архитектурный gap |
| Excel не генерируется | openpyxl не установлен, тихий fallback | backend env |
