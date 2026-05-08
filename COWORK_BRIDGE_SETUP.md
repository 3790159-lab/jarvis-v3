# Cowork Bridge Setup Guide

## Что это

Filesystem bridge между Jarvis (Telegram) и Claude Desktop (Cowork).

```
Telegram → Jarvis → [inbox/*.json] → Claude Desktop → [outbox/*.json] → Jarvis → Telegram
```

Пользователь пишет в Telegram: "Разбери папку Downloads по датам и типам файлов"
→ Jarvis создаёт task.json в inbox
→ Claude Desktop видит файл, выполняет задачу
→ Пишет результат в outbox
→ Jarvis читает результат и отвечает пользователю

## Структура папок

```
state/
  cowork_inbox/     ← Jarvis пишет сюда задачи
  cowork_outbox/    ← Cowork пишет сюда результаты
  cowork_archive/   ← Обработанные результаты
```

## Шаг 1: Открыть Claude Desktop

Установи Claude Desktop (claude.ai/download) если ещё не установлен.

## Шаг 2: Дать доступ к папкам

В Claude Desktop Settings → Files → добавь доступ к:
- `<project_root>/state/cowork_inbox`
- `<project_root>/state/cowork_outbox`

Или укажи полные пути в системных настройках MCP.

## Шаг 3: Настроить system prompt для Cowork

В начале разговора с Claude Desktop вставь:

```
Ты — файловый агент Cowork, связанный с Jarvis.

ИНСТРУКЦИЯ:
Каждые 30 секунд проверяй папку state/cowork_inbox/ на наличие новых .json файлов.
Когда появляется новый файл:
1. Прочитай JSON: поля task_id, instruction, context, deadline
2. Выполни инструкцию (работай с файловой системой, создавай файлы, организуй данные)
3. Запиши результат в state/cowork_outbox/<task_id>.json в формате:
   {
     "task_id": "<task_id из входящего файла>",
     "status": "done",
     "result": "<описание что сделано>",
     "files_created": ["<список созданных файлов>"],
     "completed_at": "<ISO timestamp>"
   }
4. Удали входящий файл из inbox

Приоритет: deadline поле в задаче. Если deadline прошёл — отметь status: "expired".
```

## Шаг 4: Тест

1. Запусти Jarvis: `python tools/jarvis_smart_telegram_control.py`
2. Напиши в Telegram: `/cowork организуй файлы в ~/Downloads по типам`
3. Jarvis создаст task.json в inbox
4. Проверь что Claude Desktop видит файл (через `/cowork status`)
5. Claude Desktop выполнит задачу и напишет в outbox
6. Jarvis прочитает и ответит тебе в Telegram

## Программный API

```python
from app.services.cowork_bridge import send_task_to_cowork, make_task, get_task_result

task = make_task(
    instruction="Создай отчёт по расходам из файлов в ~/Downloads/receipts",
    context={"output_format": "xlsx", "currency": "RUB"},
    deadline_sec=300,
)
task_id = send_task_to_cowork(task)

# Позже:
result = get_task_result(task_id)
if result:
    print(result["result"])
```

## Task JSON формат

```json
{
  "task_id": "uuid4",
  "instruction": "Organize all PDFs in ~/Downloads by date",
  "context": {
    "source_folder": "~/Downloads",
    "output_format": "folders_by_month"
  },
  "callback_marker": "jarvis_cb_1234567890",
  "submitted_at": "2026-05-01T10:00:00Z",
  "deadline": "2026-05-01T10:05:00Z",
  "status": "pending"
}
```

## Troubleshooting

**Claude Desktop не видит папку**: Проверь права доступа и путь в настройках.
**Задача не выполняется**: Проверь что Claude Desktop активен и system prompt загружен.
**Timeout**: Увеличь `deadline_sec` в make_task (по умолчанию 300 сек).
**Проверить статус**: `/cowork status` в Telegram — покажет pending/outbox count.

## Block D план

После настройки Cowork bridge, Block D добавит:
- Автоматический `/cowork` роутинг сложных файловых задач
- Watchdog мониторинг outbox (без polling)
- Двусторонние progress updates
- Очередь задач с приоритетами
