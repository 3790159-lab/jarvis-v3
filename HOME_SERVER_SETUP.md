# Jarvis на домашнем ПК — полный setup

## Что получится

✅ Jarvis работает **24/7** на твоём ПК  
✅ Запускается **автоматически** при включении  
✅ **Перезапускается сам** при сбоях  
✅ Доступен **с телефона из интернета** (Cloudflare Tunnel)  
✅ **Утренние брифы** приходят пока ты спишь  
✅ **Ежедневные бэкапы** состояния  
✅ **БЕСПЛАТНО** (без подписок, только API ключи)

---

## Шаги установки (15 минут)

### 1. Установить зависимости

```powershell
# В папке проекта
.venv\Scripts\python -m pip install -r requirements.txt
```

### 2. Настроить .env файл

```
TELEGRAM_BOT_TOKEN=ваш_токен
TELEGRAM_ALLOWED_CHAT_ID=ваш_chat_id
ANTHROPIC_API_KEY=sk-ant-...
PERPLEXITY_API_KEY=pplx-...   # опционально
OPENAI_API_KEY=sk-...          # опционально
```

### 3. Установить службы Windows (требует прав администратора)

```powershell
# Запусти PowerShell как администратор
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
.\scripts\install_service.ps1
```

Установятся три службы:
- **JarvisBackend** — uvicorn API на порту 8010
- **JarvisBot** — Telegram бот
- **JarvisWatchdog** — мониторинг и автовосстановление

### 4. Настроить запрет сна ПК

```powershell
.\scripts\nosleep_setup.ps1
```

### 5. (Опционально) Cloudflare Tunnel для доступа из интернета

```powershell
.\scripts\setup_cloudflare_tunnel.ps1 -Domain "jarvis.твой-домен.com"
```

Подробности: [CLOUDFLARE_TUNNEL_SETUP.md](CLOUDFLARE_TUNNEL_SETUP.md)

### 6. (Опционально) Ежедневные бэкапы через Task Scheduler

```powershell
.\scripts\daily_backup.ps1 -RegisterTask
```

---

## Управление

### Статус

```powershell
Get-Service JarvisBackend, JarvisBot, JarvisWatchdog
```

### Перезапуск

```powershell
# Один сервис
Restart-Service JarvisBot

# Все сервисы
.\scripts\restart_services.ps1

# Только backend
.\scripts\restart_services.ps1 -Service backend
```

### Логи

```powershell
# Backend
Get-Content state\backend.log -Tail 50

# Бот
Get-Content state\bot.log -Tail 50

# Watchdog
Get-Content state\watchdog.log -Tail 50

# Через Telegram
/logs bot 50
/logs errors 30
/selfcheck
```

### Из Telegram

| Команда | Что делает |
|---------|-----------|
| `/selfcheck` | Полная диагностика системы |
| `/logs bot 50` | Последние 50 строк лога бота |
| `/logs errors 30` | Последние ошибки |
| `/errors recent` | Ошибки за последний час |
| `/remind через 1 час встреча` | Напоминание |
| `/brief on` | Включить утренний бриф в 9:00 |
| `/schedule list` | Активные задачи |
| `/improve stats` | Статистика AI решений |

---

## Удаление

```powershell
.\scripts\uninstall_service.ps1
```

---

## Troubleshooting

| Проблема | Решение |
|---------|---------|
| Служба не запускается | `Get-Content state\backend.err.log -Tail 20` |
| Бот не отвечает | `Restart-Service JarvisBot` |
| Ошибка 404 | Backend не запущен — `Restart-Service JarvisBackend` |
| Нет места на диске | `/selfcheck` — watchdog сам почистит логи |
| ПК уходит в сон | Повторно запусти `.\scripts\nosleep_setup.ps1` |

---

## Архитектура

```
Windows Boot
    └─ Windows Service Manager
        ├─ JarvisBackend (uvicorn :8010)
        │   └─ app/main.py — FastAPI + все роутеры
        ├─ JarvisBot (telegram polling)
        │   └─ tools/jarvis_smart_telegram_control.py
        └─ JarvisWatchdog (60s health checks)
            └─ app/services/system_watchdog.py

Cloudflare Tunnel (опционально)
    └─ cloudflared → JarvisBackend :8010
        └─ /telegram/webhook (incoming updates)

Scheduled Tasks
    └─ JarvisDailyBackup (3:00 AM)
        └─ state/ → state_backups/YYYY-MM-DD/
```
