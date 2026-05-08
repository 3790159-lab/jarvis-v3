# SESSION_SUMMARY_BLOCK_G.md — Block G Complete

Date: 2026-05-01  
Tests: 807 → 934 (+127)  
Commits: 8

---

## ЧАСТЬ 1: Критичные фиксы (Phase 33.1–33.4)

### Phase 33.1 — /remind parser полностью переписан

**Было:** dateparser-only → не работал ни русский ни английский  
**Стало:** regex-first → детерминированный, быстрый, надёжный

Паттерны:
- `через X минут/часов/дней <текст>`
- `завтра/сегодня в HH:MM <текст>`  
- `каждый день (в) HH:MM <текст>`
- `in X minutes/hours/days <text>`
- `tomorrow at HH:MM <text>`
- `every day at HH:MM <text>`
- dateparser как fallback для экзотических форматов

+23 теста

### Phase 33.2 — Image generation routing fix

**Было:** "Сделай 4 фото девушки на пляже" → brain planning  
**Стало:** → generate intent (правильно!)

Strong image patterns проверяются ДО compound_task detection:
- `\bсделай\s+\d*\s*фот`, `\bнарисуй`, `\bсоздай\s+картинк`
- `\bgenerate\s+(?:\w+\s+){0,3}(?:photo|image|picture)`
- `\bdraw\s+`

+20 тестов

### Phase 33.3 — Vision auto-rotate

`_auto_rotate()` читает EXIF Orientation tag и поворачивает 90/180/270°.  
Graceful fallback: нет EXIF / плохой файл → возвращает оригинал.  
+8 тестов

### Phase 33.4 — Empty file messages

`/logs decisions` → "📊 Лог решений пуст. Используй Jarvis больше..."  
`/logs errors` → "✅ Лог ошибок пуст — всё работает чисто!"  
`/logs tasks` → "📭 Нет активных задач. Создай командой /remind"  
+10 тестов

---

## ЧАСТЬ 2: Decision Log (Phase 34)

`run_intent` теперь автоматически логирует каждое routing решение:
- `_try_log_decision()` — быстрый, не блокирует, silent fail
- `send_with_feedback()` — отправляет 👍/👎 keyboard
- research / simple_question → показывают feedback buttons
- `state["last_decision_id"]` — доступен для всего цикла

+15 тестов

---

## ЧАСТЬ 3: Home Server Autonomy (Phase 35)

### Windows Services via NSSM

`scripts/install_service.ps1`:
- Скачивает NSSM 2.24 автоматически
- Устанавливает JarvisBackend + JarvisBot + JarvisWatchdog
- Auto-restart on failure (5 sec delay)
- Log rotation (10MB per file)
- Service dependencies (Bot ждёт Backend)

`scripts/uninstall_service.ps1` — чистое удаление  
`scripts/restart_services.ps1` — graceful restart с ordering

### NoSleep

`scripts/nosleep_setup.ps1` — отключает сон/гибернацию на AC power

### System Watchdog

`app/services/system_watchdog.py` — проверяет каждые 60 секунд:
- Backend `/health` endpoint
- Disk space > 1GB  
- Memory < 90%

На проблему:
- Перезапускает JarvisBackend через `Restart-Service`
- Чистит лог-файлы при нехватке места
- Telegram alert администратору

+24 теста

---

## ЧАСТЬ 4: Self-Healing (Phase 36)

`app/services/self_healing.py` — полная реализация:

```python
healer = SelfHealing()
report = healer.check_and_heal()
# Проверяет диск, память, архивирует старые решения, делает бэкап
```

- `cleanup_old_logs()` — обрезает файлы > 50MB до 1000 строк
- `archive_old_decisions()` — перемещает 30+ дней в архив
- `create_backup()` / `cleanup_old_backups()` — daily backup + ротация 7 дней
- `scripts/daily_backup.ps1` — можно запустить через Task Scheduler в 3:00 AM

+27 тестов

---

## ЧАСТЬ 5: Cloudflare Tunnel (Phase 37)

`scripts/setup_cloudflare_tunnel.ps1`:
- Скачивает cloudflared
- Интерактивный логин через браузер
- Создаёт tunnel, настраивает DNS
- Устанавливает как Windows Service

`app/main.py`:
- `POST /telegram/webhook` — принимает Telegram updates
- `GET /telegram/webhook/status` — очередь входящих

`CLOUDFLARE_TUNNEL_SETUP.md` — пошаговая документация

---

## ЧАСТЬ 6: Production Docs

- `HOME_SERVER_SETUP.md` — полный setup guide (15 минут)
- `ВКЛЮЧИЛ-РАБОТАЕТ.md` — одна команда для быстрого старта
- `CLOUDFLARE_TUNNEL_SETUP.md` — tunnel инструкция

---

## Статистика

| Метрика | Значение |
|---------|----------|
| Тестов до | 807 |
| Тестов после | 934 |
| Добавлено | +127 |
| Коммитов | 8 |
| Новых файлов | 12 |

---

## Что РЕАЛЬНО работает после фиксов

✅ `/remind через 10 минут позвонить маме` — работает детерминированно  
✅ `/remind in 10 minutes test reminder` — работает  
✅ "Сделай 4 фото девушки на пляже" → generate (не brain)  
✅ Vision auto-rotate — фото с телефона не будут перевёрнутыми  
✅ `/logs decisions` — дружелюбное сообщение когда пусто  
✅ Каждый research/question → 👍/👎 feedback кнопки  
✅ Все решения автоматически логируются в decisions.jsonl  

---

## Готовность к домашнему развёртыванию

**Что нужно запустить:**

```powershell
# 1. Из PowerShell как администратор
.\scripts\install_service.ps1
.\scripts\nosleep_setup.ps1

# 2. Проверить
Get-Service Jarvis*    # все три Running

# 3. (опционально) доступ из интернета
.\scripts\setup_cloudflare_tunnel.ps1 -Domain "jarvis.твой-домен.com"
```

**После этого Jarvis:**
- Запускается при старте Windows
- Перезапускается при сбоях автоматически
- Watchdog следит каждые 60 секунд
- Бэкапы каждую ночь в 3:00
- Доступен из любой точки мира через Cloudflare

---

## Известные ограничения

- **Watchdog**: `Restart-Service` требует прав администратора — служба должна запускаться из admin account
- **Cloudflare Tunnel**: нужен домен для постоянного webhook (без домена — временный URL)
- **psutil**: не установлен в проекте — memory check работает через graceful fallback
- **Webhook mode**: требует активации через `setWebhook` API вызов после установки Cloudflare Tunnel
