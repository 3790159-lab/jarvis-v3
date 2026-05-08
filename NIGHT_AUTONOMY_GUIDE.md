# Night Autonomy Guide — Block H5

## Overview

Jarvis runs 5 automated phases every night, from 22:00 to 08:00.
You sleep — Jarvis works.

---

## Night Phases

| Phase | Hours | What happens |
|-------|-------|-------------|
| `winddown` | 22:00–23:00 | Daily recap, temp cleanup, state backup |
| `deep_work` | 23:00–02:00 | Content generation, trend analysis |
| `self_improve` | 02:00–04:00 | Error analysis, prompt optimization (A/B tested) |
| `morning_prep` | 04:00–06:00 | Fetch data, compose morning brief |
| `wakeup` | 06:00–08:00 | Send brief to Telegram, notify you |

---

## What Jarvis Does Each Night

### 22:00 — Wind-down
1. Generates **daily recap**: tasks, photos created, errors, feedback stats
2. Cleans temp files older than 24h
3. Backs up key state files to `state/backups/<date>/`

### 23:00 — Deep Work
1. Finds your **top 3 most requested dishes** from history
2. Generates Instagram-ready photos + captions for each
3. Schedules them at optimal times in `state/scheduled_posts/<tomorrow>.json`
4. Analyzes **food industry trends** via Perplexity
5. Saves trend insights to Obsidian

### 02:00 — Self-Improvement
1. Reviews all **👎 negative feedback** from the day
2. Groups by intent and identifies patterns via Claude
3. Generates improved prompts
4. **A/B tests**: runs both old and new prompt against sample queries
5. Applies if new prompt is >20% better
6. Saves versioned history in `state/optimized_prompts/<intent>.json`

### 04:00 — Morning Prep
1. Fetches morning data (recap + trends + scheduled posts)
2. Composes morning brief text
3. Saves to `state/night_workflows/morning_brief.txt`

### 06:00 — Wake-up
1. Sends morning brief to your Telegram
2. Sends navigation hints for checking night work

---

## Morning Brief Format

```
📊 Дневной отчёт — 2026-05-02

✅ Задач выполнено: 12
📸 Фото создано: 7
🔴 Ошибок: 1
👍 Удовлетворённость: 85%

🔥 Топ запросов: research, table, restaurant

🎯 Фокус завтра: Оптимизация контент-генерации

📈 Тренды сегодня:
🍽 seasonal ingredients | comfort food | fusion cuisine
🏷 #foodphotography #instafood #restaurant
```

---

## Autonomy Dashboard

**URL:** `http://localhost:8010/dashboard/autonomy/html`

Shows:
- Current active night phase
- Last daily recap stats
- Scheduled content posts
- Self-improvement metrics (how many prompts optimized)
- Latest food trends

**API:** `GET /dashboard/autonomy` — returns JSON

---

## Checking What Jarvis Did Last Night

### In Telegram:
- `/improve stats` — feedback statistics
- `/schedule list` — all scheduled tasks
- `/logs decisions 20` — last 20 decisions
- `/errors recent` — any errors

### Files to check:
```
state/daily_recaps/2026-05-02.json    — yesterday's recap
state/scheduled_posts/2026-05-03.json — tomorrow's posts
state/optimized_prompts/<intent>.json — improved prompts
state/trend_insights/2026-05-02.json  — today's trends
state/night_workflows/phase_log.jsonl — phase execution log
state/backups/2026-05-02/            — state backup
```

---

## Self-Improvement Rollback

If a prompt change caused worse behavior:

1. Find the intent: `ls state/optimized_prompts/`
2. View history: `cat state/optimized_prompts/research.json`
3. Roll back via Python:
```python
from app.services.self_improvement import SelfImprovementLoop
loop = SelfImprovementLoop()
loop.rollback_prompt("research")
```

Or delete the file entirely to revert to original behavior:
```bash
rm state/optimized_prompts/research.json
```

---

## Configuration

All phases are auto-scheduled via the JarvisScheduler on startup.
No manual configuration needed.

### Required environment variables:
```env
REPLICATE_API_KEY=...       # For photo generation
ANTHROPIC_API_KEY=...       # For prompt optimization (A/B test)
PERPLEXITY_API_KEY=...      # For trend analysis
JARVIS_OBSIDIAN_VAULT_PATH=... # For Obsidian saves (optional)
BACKEND_BASE_URL=http://127.0.0.1:8010  # Backend (default)
```

### Ensure backend runs 24/7:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

---

## Architecture

```
NightWorkflow (orchestrator)
├── winddown phase
│   ├── daily_recap.generate_daily_recap()
│   ├── cleanup_temp_files()
│   └── backup_state()
├── deep_work phase
│   ├── auto_content.generate_tomorrow_content()
│   └── trend_analyzer.analyze_industry_trends()
├── self_improve phase
│   └── self_improvement.SelfImprovementLoop.run_full_cycle()
│       ├── collect_feedback()
│       ├── analyze_negatives() via Claude
│       ├── optimize_prompt() via Claude
│       ├── ab_test() — 10 queries
│       └── apply_if_better() — >20% threshold
├── morning_prep phase
│   ├── fetch_morning_data()
│   └── generate_morning_brief()
└── wakeup phase
    ├── send_morning_brief() → Telegram
    └── notify_user()

CrossServiceCoordinator (event chains)
├── new_dish_added() — photos + obsidian + n8n + schedule
├── morning_routine() — recap + trends + posts brief
├── negative_feedback_received() — queue for analysis
└── party_event_planned() — poster + menu + reminders

SmartSchedule (patterns)
├── analyze_user_patterns() — when are you active?
├── detect_quiet_hours() — when do you sleep?
└── get_optimal_brief_time() — 30 min before wakeup
```
