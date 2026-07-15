# DEV-17: Uptime Kuma setup (мониторинг и алерты)

## Почему

15.07 backend умер в 03:14 и никто не узнал — единственный механизм, который
мог бы заметить, жил в heartbeat-цикле самого бота, а бот делит судьбу с тем,
за чем должен следить. Uptime Kuma — отдельный Docker-контейнер, который не
падает вместе с ботом/backend/cloudflared, и шлёт алерт в Telegram напрямую,
без участия бота.

## Что уже готово (код, TDD, замокано)

- `app/services/ops_monitor.py` — чистые проверки: диск, heartbeat бота,
  статус службы cloudflared, шторм рестартов бота (>N за час, парсит
  `state/logs/bot_guardian.stdout.log`, ничего в guardian-скриптах не трогает).
- `app/routers/jarvis_ops_health_router.py` — HTTP-эндпоинты для Kuma
  (200 = ок, 503 = плохо — то, что понимает обычный HTTP-монитор Kuma):
  - `GET /api/jarvis/ops/heartbeat` — свежесть `state/bot_heartbeat.txt`
  - `GET /api/jarvis/ops/cloudflared` — статус службы `cloudflared`
  - `GET /api/jarvis/ops/disk` — свободное место на диске
  - `GET /api/jarvis/ops/restarts` — рестарты бота за последний час
  - (backend сам по себе — существующий `GET /health`, ничего добавлять не нужно)
- `app/services/kuma_status.py` + `/smart_health` — сводка по Kuma-мониторам
  через публичный JSON API статус-страницы (`KUMA_STATUS_PAGE_URL`), честно
  деградирует в текст «не настроен»/«недоступен», если Kuma ещё не поднята.
- `infra/uptime-kuma/docker-compose.yml` + `scripts/setup_uptime_kuma.ps1` —
  сам контейнер (не поднят в этой сессии — нет живого Docker-демона в
  sandboxed worktree, и это в любом случае живая host-операция).
- `failed dev_task` и `failed IG publish` уже алертят в TG — существующий
  код (`_devtask_poll_active` в `tools/jarvis_smart_telegram_control.py`,
  `ig_schedule.process_due` + `scripts/ig_schedule_publisher.py`), с тестовым
  покрытием (`tests/test_devtask_wiring.py`, `tests/test_ig_schedule.py`).
  Ничего переделывать не пришлось.

## Что должен сделать Daniil (живая часть, ≤10 минут)

1. Убедиться, что Docker Desktop запущен.
2. `scripts\setup_uptime_kuma.ps1` — поднимет контейнер
   (`docker compose up -d` в `infra/uptime-kuma/`), откроется на
   `http://127.0.0.1:3001`.
3. Первый вход в Kuma UI: создать admin-логин (локально, не через CC).
4. Добавить 5 HTTP(s)-мониторов (Monitor Type: HTTP(s), Accepted Status
   Codes: 200-299), интервал проверки — 30-60 сек:
   - Backend: `http://host.docker.internal:8010/health`
   - Bot heartbeat: `http://host.docker.internal:8010/api/jarvis/ops/heartbeat`
   - Cloudflared: `http://host.docker.internal:8010/api/jarvis/ops/cloudflared`
   - Disk: `http://host.docker.internal:8010/api/jarvis/ops/disk`
   - Bot restarts: `http://host.docker.internal:8010/api/jarvis/ops/restarts`
5. Notifications → Telegram: вставить `TELEGRAM_BOT_TOKEN` и
   `TELEGRAM_ALLOWED_CHAT_ID` (те же значения, что в `.env` — **вводит
   Daniil сам, CC их не видит и не трогает**, дисциплина секрет-канала DEV-2).
   Привязать это уведомление к каждому из 5 мониторов.
6. (опционально, для пункта 3 приёмки) Status Page → создать публичную
   страницу, добавить туда все 5 мониторов, скопировать её API URL
   (`.../api/status-page/<slug>`) в `.env` как `KUMA_STATUS_PAGE_URL` —
   тогда `/smart_health` в боте покажет сводку по Kuma.

## Живой тест приёмки (после шага 5)

1. Убить backend (`Stop-Process` по PID `python.exe`, слушающему :8010, или
   через диспетчер задач) → в Kuma монитор "Backend" должен уйти в Down и
   прийти алерт в Telegram **в течение ~1 минуты** (интервал проверки).
2. `/infra_restart backend` в боте → backend поднимается → Kuma видит Up →
   приходит алерт о восстановлении.
3. (аналогично можно проверить `/infra_restart cloudflared`.)

## Известные ограничения

- Restart-storm монитор (`/api/jarvis/ops/restarts`) читает
  `state/logs/bot_guardian.stdout.log` — если этот путь/формат строки
  изменится в `bot_guardian_detached.ps1`, счётчик надо будет свериться
  (сам .ps1 не трогали и не должны — красная линия worktree).
- `KUMA_STATUS_PAGE_URL` для `/smart_health` — опционален; без него команда
  просто пишет «Kuma: не настроен», ничего не падает.
