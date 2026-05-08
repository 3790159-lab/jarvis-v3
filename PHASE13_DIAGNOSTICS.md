# Phase 13 Diagnostics — File Message Handling Root Cause

## Симптом
Бот получает документ/фото с caption "Привет, просмотри что это за файлы и расскажи про них" → отвечает research'ем вместо анализа файла.

## Root Cause (найдено 2026-05-01)

### 1. Caption routing conflict — ГЛАВНАЯ ПРОБЛЕМА

В `_handle_file_message` (line 1295-1298):
```python
if caption:
    pack = classify_message(caption, state)
    run_intent(chat_id, pack, state)
```

`classify_message` имеет file_triggers только для узких паттернов:
- "суммируй файл", "кратко про файл", "что в файле", "о чём файл"
- НЕТ: "просмотри", "расскажи про", "что это", "посмотри на"

А `research_triggers` (line 563) содержат **"расскажи про"** — поэтому caption
"расскажи про файлы" → research intent вместо summarize_file.

### 2. File triggers слишком строгие

Пользователь обычно пишет caption в свободной форме:
- "просмотри что это" → не матчится ни одним file_trigger
- "что за файлы" → не матчится
- "посмотри" → не матчится
- "расскажи про них" → матчится research_triggers!

### 3. Отсутствует контекст "мы уже знаем что это файл"

При вызове из `_handle_file_message` мы точно знаем что пришёл файл.
classify_message не знает этого контекста и применяет общую логику.

### 4. state["last_uploaded_file"] сохраняется ДО classify_message

Это хорошо — state есть. Но file_triggers проверяются только при
state.get("last_uploaded_file") — и они всё равно не матчатся.

## Fix Strategy

### A. Новая функция classify_file_caption(caption) — file-aware router
Когда вызывается из _handle_file_message, применяем расширенные паттерны:
- "просмотри", "посмотри", "взгляни", "что это" → summarize_file
- "расскажи про", "расскажи что" → summarize_file (в контексте файла)
- "извлеки", "вытащи", "достань" → extract_from_file
- "найди в", "есть ли в" → ask_about_file
- "бухгалтерия", "счёт", "финансы" → accounting
- DEFAULT (любой другой caption) → summarize_file (разумный default)

### B. Расширить file_triggers в classify_message
Добавить паттерны которые часто используются как file captions.

### C. Forward message support
msg.get("forward_from") + document → обработать как обычный файл.

## Files Changed
- tools/jarvis_smart_telegram_control.py
  - Новая функция classify_file_caption()
  - _handle_file_message: использует classify_file_caption вместо classify_message
  - Расширенные file_triggers в classify_message
  - Forward message handling в main()
  - media_group buffering

## Tests Added (target 15+)
- caption routing tests
- forward message tests
- media_group tests
