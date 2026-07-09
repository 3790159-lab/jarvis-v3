# Supervisor V1.5 Smart Telegram

This version improves the assistant layer on top of the working Telegram/UI automation loop.

## What changed in v1.5

- better distinction between **questions** and **commands**
- typo-tolerant request normalization for common cases like `Пайтан`, `Paython`, `pyton`
- safer fallback: unknown questions no longer trigger shell diagnostics automatically
- built-in direct answers for help, status, greetings, and the first general-knowledge topic (`bridges`)
- optional **OpenAI-compatible LLM** support for better interpretation and explanation
- updated UI on port **8015**

## Core flow

Telegram or UI -> assistant interpreter -> planner/router -> executor -> assistant explanation -> response

## Run on Windows

1. Create and activate a venv
2. Install requirements
3. Copy `.env.example` to `.env`
4. Set at least `DEFAULT_PROJECT_ROOT`
5. Start API, worker, and optionally Telegram bridge

### API

```powershell
.\scripts\start_api.bat
```

### Worker

```powershell
.\scripts\start_worker.bat
```

### Telegram bridge

```powershell
.\scripts\start_telegram_bot.bat
```

## Optional OpenAI-compatible LLM

By default the system is fully local and rule-based.

If you want stronger natural-language understanding and better final explanations, fill these settings in `.env`:

```text
LLM_MODE=openai_compatible
LLM_API_KEY=...
LLM_BASE_URL=https://your-endpoint.example/v1
LLM_MODEL=...
LLM_USE_FOR_INTERPRETATION=1
LLM_USE_FOR_RESPONSE_FORMATTING=1
```

If those settings are missing, the assistant keeps working in offline rule-based mode.

## Example natural-language requests

- `Что ты умеешь?`
- `Проверь версию Пайтан в проекте`
- `Покажи файлы проекта`
- `Запусти git status в проекте`
- `Сможешь мне рассказать по какой схеме проектируют мосты?`

## Current limitations

- broad general knowledge remains limited in offline mode unless an LLM is configured
- task execution is still intentionally restricted to built-in flows and executors
- Codex integration depends on the local `codex` command being installed and configured

## Pipeline

Dev-конвейер: /dev_task в Telegram → CC (TDD, worktree) → таргет-тесты → [Мердж] тапом админа.
