# Instagram Graph API — публикация контента (проверено 2026-07-09)

Источник истины: developers.facebook.com/docs/instagram-platform/content-publishing/
При расхождении этого файла с живой докой — верить доке и обновить этот файл.

## Что можно публиковать через API
Фото, видео, карусели (до 10 элементов), Reels, Stories — только для
**Instagram professional (Business) аккаунтов**, привязанных к Facebook Page.
Личные аккаунты через API не публикуют.

## Контейнерная модель (3 шага)
1. `POST /{ig-user-id}/media` → создаёт контейнер, возвращает container_id
   - фото: `image_url`; Reels: `media_type=REELS` + `video_url`;
     Stories: `media_type=STORIES`; карусель: `media_type=CAROUSEL` + `children`
2. `GET /{container-id}?fields=status_code` → поллить до `FINISHED`
   (фото почти мгновенно, видео — до минут; экспоненциальный backoff)
3. `POST /{ig-user-id}/media_publish` c `creation_id` → публикация

## Жёсткие требования к медиа
- **Медиа должно лежать на публично доступном URL в момент попытки** —
  Meta сама скачивает файл (cURL). Наш хостинг: Cloudflare R2 (litterbox НЕ годится).
- Reels: 9:16, 5-90 сек для попадания во вкладку Reels, H.264/HEVC, MP4.
  Видео вне этих рамок публикуется как обычный видео-пост.
  Обложка: `cover_url` 1080×1920; профиль-сетка режет центр в 1:1.
- Карусель: обрезка всех элементов по первому (default 1:1).
- Загрузка видео Reels — через `rupload.facebook.com` (resumable), не graph-host.

## Лимиты
- **50 API-публикаций на аккаунт за скользящие 24ч** (карусель = 1 пост).
  Лимит на media_publish, не на создание контейнеров. Некоторые источники
  называют 25 — при планировании закладываться консервативно, читать
  заголовки X-Business-Use-Case-Usage.
- ~200 запросов/час на аккаунт (base). Поллинг заменять webhooks где возможно.
- Ошибка лимита = код 4 + subcode → exponential backoff.

## Права и ревью (для собственного приложения)
- Разрешения: `instagram_business_basic` + `instagram_business_content_publish`
  (старые instagram_basic/instagram_content_publish deprecated с 27.01.2025).
- Каждое разрешение = отдельный app review со скринкастом полного user flow.
  Срок ревью: 2-4 недели. Требует живых действий Daniil.
- До ревью работаем через n8n-workflow или обёртку (Upload-Post и аналоги —
  их приложения уже прошли ревью; scheduled_date даёт отложенный постинг).

## Планирование публикаций
Graph API сам НЕ хранит расписание — планировщик наш (n8n cron / APScheduler):
хранить очередь в state, по времени дёргать шаги 1-3. Ретраи: транзиентные
ошибки контейнера → повтор; терминальные → пометить пост failed, уведомить, НЕ биллить повторно.

## Money-safe правила для автопостинга
- Публикация бесплатна, но генерация медиа — нет: генерить ТОЛЬКО после
  утверждения контент-плана.
- Неудачная публикация ≠ повторная генерация. Медиа уже в R2 — ретраить публикацию.
- Каждый шаг цепочки логировать в леджер операций (пост-ID, аккаунт, статус).

## Мультиаккаунт credentials (проверено 2026-07-12)
Несколько IG-аккаунтов (напр. `jtest_lab_`, `vera_ai_ua`) хранятся в
`state/ig_accounts.json` (`app.services.ig_accounts`; не в git, `state/` в
`.gitignore` целиком) — ключ `account_key` → `{ig_user_id, username,
access_token, token_refreshed_at}`. `/ig_stats`, `/ig_post`, `/ig_gen`
принимают опциональный префикс `@<account_key>` (напр. `/ig_gen @vera_ai_ua
тема`); `/ig_gen`/`/ig_caption` без явного `@` берут account_key из
`client=<name>` → `clients/<name>/brand.md` (`account_key`-поле, иначе имя
клиента как account_key). Без аргумента — дефолт: env `IG_DEFAULT_ACCOUNT`,
иначе `jtest_lab_`. Пока json не создан — легаси `.env` `IG_ACCESS_TOKEN`/
`IG_USER_ID` работают как раньше (авто-миграция в json при первом резолве
credentials). Auto-refresh (`scripts/ig_token_refresh.py`, ежедневная задача)
обходит все аккаунты из json независимо, свой age-gate и алярм с именем
аккаунта на каждый.

## Read-only: профиль и метрики (проверено 2026-07-12, /ig_stats)
- Профиль: `GET /me?fields=username,followers_count,media_count` —
  followers_count/media_count требуют только `instagram_business_basic`
  (не insights).
- Медиа-лист: `GET /{ig-user-id}/media?fields=...&limit=N` — like_count/
  comments_count живут на самом media-узле, тоже без insights-разрешения.
- Per-media insights: `GET /{media-id}/insights?metric=reach,total_interactions`
  — требует `instagram_business_manage_insights`. **impressions задеприкейчен
  для медиа, созданных после 2024-07-02** — не запрашивать, честный постоянный
  сбой. `reach` доступен для FEED/REELS/STORY (Story истекает за 24ч).
- Fail-closed по-честному: сбой профиля/списка постов — вся карточка не
  показывается (нечего показать честно); сбой insights ОДНОГО поста — его
  строка деградирует до «н/д», карточка не падает целиком.
