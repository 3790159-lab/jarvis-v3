# Cloudflare Tunnel Setup для Jarvis

## Что это и зачем

Cloudflare Tunnel позволяет сделать твой домашний Jarvis доступным из интернета **без открытия портов на роутере** и **без статического IP**.

```
Telegram → Cloudflare CDN → Tunnel → твой ПК дома → Jarvis
```

**Бесплатно** при наличии домена (даже бесплатного через Cloudflare Registrar).

---

## Предварительные требования

1. **Аккаунт Cloudflare** — зарегистрируйся на cloudflare.com (бесплатно)
2. **Домен** добавлен в Cloudflare (например, `yourdomain.com`)
3. **Jarvis запущен** как Windows Service (через `install_service.ps1`)

---

## Установка (15 минут)

### Шаг 1 — Запусти скрипт

```powershell
# Запускай из папки проекта, с правами администратора
.\scripts\setup_cloudflare_tunnel.ps1 -Domain "jarvis.yourdomain.com"
```

Скрипт:
1. Скачает `cloudflared.exe`
2. Откроет браузер для логина в Cloudflare
3. Создаст тоннель
4. Настроит DNS
5. Установит как Windows Service

### Шаг 2 — Проверь

```powershell
# Статус туннеля
.\scripts\setup_cloudflare_tunnel.ps1 -Status

# Открой в браузере
# https://jarvis.yourdomain.com/health
```

---

## Webhook mode для Telegram бота

После настройки туннеля можно переключить бот с polling на webhook — это быстрее.

### Установить webhook

```powershell
$TOKEN = $env:TELEGRAM_BOT_TOKEN
$URL = "https://jarvis.yourdomain.com/telegram/webhook"
Invoke-RestMethod "https://api.telegram.org/bot$TOKEN/setWebhook?url=$URL"
```

### Проверить webhook

```powershell
Invoke-RestMethod "https://api.telegram.org/bot$TOKEN/getWebhookInfo"
```

### Откатиться на polling

```powershell
Invoke-RestMethod "https://api.telegram.org/bot$TOKEN/deleteWebhook"
```

---

## Управление

```powershell
# Статус
.\scripts\setup_cloudflare_tunnel.ps1 -Status

# Логи туннеля
Get-Content state\cloudflared.log -Tail 50

# Удалить
.\scripts\setup_cloudflare_tunnel.ps1 -Uninstall
```

---

## Troubleshooting

| Проблема | Решение |
|---------|---------|
| Браузер не открылся | Открой ссылку вручную из консоли |
| DNS не распространился | Подожди 1-5 минут |
| 502 Bad Gateway | Backend не запущен (`Get-Service JarvisBackend`) |
| Webhook не работает | Проверь `getWebhookInfo` — смотри поле `last_error_message` |

---

## Без своего домена

Если домена нет — cloudflared умеет создавать временный URL:

```powershell
# Запустить туннель без домена (временный URL)
.\cloudflared\cloudflared.exe tunnel --url http://127.0.0.1:8010
# Выведет что-то вроде: https://random-words.trycloudflare.com
```

Такой URL меняется при каждом перезапуске, поэтому для постоянного webhook нужен собственный домен.
