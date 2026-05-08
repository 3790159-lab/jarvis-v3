# BLOCK_I_PLAN.md — Next: Multi-User + Advanced Agents

## Приоритеты

### ЧАСТЬ 1: Multi-User Support
- Убрать жёсткий `ALLOWED_CHAT_ID` → поддержка нескольких пользователей
- Per-user state store: `state/users/<chat_id>/state.json`
- Per-user schedules и история
- User registration flow через бот
- Admin chat_id список

### ЧАСТЬ 2: Advanced Agent Capabilities
- Auto-research on schedule (daily digests)
- Task dependency chains
- Cross-session memory ("помни что я сказал вчера")
- Proactive alerts (stock price, news, weather)

### ЧАСТЬ 3: Dashboard Enhancements
- Telegram Login Widget (OAuth)
- Mobile PWA (offline capable)
- Push notifications через Telegram
- Dark/light theme switch

### ЧАСТЬ 4: Integrations
- Notion integration
- Google Calendar sync
- Slack/Discord bridge
- Email digest

### ЧАСТЬ 5: AI Improvements
- Fine-tuned prompt caching
- Context window optimization
- Multi-turn conversation memory
- Tool chaining (research → summarize → save)
