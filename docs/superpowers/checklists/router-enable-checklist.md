# Router enable checklist (run BEFORE setting JARVIS_ROUTER_ENABLED=1)

Prereqs: backend up (uvicorn :8010), ANTHROPIC_API_KEY set, API keys present
(PERPLEXITY_API_KEY, TAVILY_API_KEY, REPLICATE_API_KEY).

## Automated
- [ ] `python -m pytest tests/test_tools/ tests/test_router_prompt.py -q` → all green.
- [ ] `python -m py_compile tools/jarvis_smart_telegram_control.py` → ok.

## Live NL smoke (temporary: set JARVIS_ROUTER_ENABLED=1 in a TEST shell only)
Send each message to the bot and confirm the RIGHT tool fires (watch jarvis_bot.log
for the tool name) and the answer is real:
- [ ] "сделай ресёрч по ценам на GPU аренду" → web_research → text answer.
- [ ] "сделай таблицу топ 5 AI видеогенераторов" → build_table → XLSX delivered.
- [ ] "сгенери картинку: кот-астронавт" → generate_image → photo.
- [ ] upload a PDF, then "что в этом файле?" → answer_about_file → summary.
- [ ] "сколько я потратил?" → get_user_stats.
- [ ] face-swap flow still works (swap_batch_* tools) + a готовое-видео swap.
- [ ] voice note in → transcribed; "ответь голосом" → reply_with_voice.
- [ ] negative: "привет, как дела" → plain text, NO tool, no hallucinated capability.
- [ ] honesty: "сделай глубокий инженерный анализ X" → offers /engineer, does NOT claim a full autonomous analysis.
- [ ] fallback: stop the backend, send "сделай ресёрч …" → graceful error, bot still alive.

## Rollback
- Set JARVIS_ROUTER_ENABLED=0 (or remove it) and restart the bot → legacy path resumes.
