# bolt.diy Guide — Block L.2

## How to start bolt.diy

1. Open PowerShell
2. `cd C:\Users\Daniil Lapin\Downloads\bolt.diy`
3. `pnpm run dev`
4. Wait for `localhost:5173` to appear
5. Open http://localhost:5173 in Chrome

## How to generate apps

1. Send `/create_app <description>` in Telegram
2. Jarvis checks bolt.diy is running
3. Claude AI generates full app specification:
   - App name and tagline
   - Tech stack recommendation
   - Feature list with priorities
   - Component breakdown
   - Optimized prompt for bolt.diy
4. You get the prompt — paste it into bolt.diy chat
5. bolt.diy builds the app in 1-3 minutes

## Commands

- `/create_app <description>` — full spec (5-10 features)
- `/create_simple <description>` — minimal spec (3 features)
- `/bolt_status` — check if bolt.diy is running
- `/bolt_open` — get bolt.diy URL
- `/bolt_queue` — show pending app specs

## Example prompts

- `/create_app fitness tracker with workout logging and progress charts`
- `/create_app CRM for freelancers with client management and invoicing`
- `/create_app pomodoro timer with task list and statistics`
- `/create_app budget tracker with categories and monthly reports`

## Tips for better apps

- **Be specific**: mention key features explicitly
- **Mention the domain**: SaaS, e-commerce, productivity, health
- **Mention data**: what data does the app store?
- **Mention design**: dark/light, minimal/colorful
- **bolt.diy works best with**: React + Tailwind CSS stack

## Common issues

**"WebContainer не загружается"**
- Use Chrome (not Firefox or Edge)
- Disable browser extensions
- Check internet connection

**"API key invalid"**
- Check `C:\Users\Daniil Lapin\Downloads\bolt.diy\.env.local`
- Verify `ANTHROPIC_API_KEY=sk-ant-...`

**"Rate limit Anthropic"**
- Wait 1 minute and retry
- Or switch model in bolt.diy settings to Groq (free)

**bolt.diy crashes**
- Restart: Ctrl+C in PowerShell, then `pnpm run dev`
- Clear browser cache

## Queue file format

`state/bolt_queue/<YYYYMMDD_HHMMSS>.json`:
```json
{
  "id": "20260504_143022_123",
  "spec": {
    "app_name": "TrackFit",
    "tech_stack": "React + Tailwind",
    "features": [...],
    "bolt_diy_prompt": "Build TrackFit..."
  },
  "status": "pending",
  "created_at": "2026-05-04T14:30:22",
  "processed_at": null,
  "app_url": null
}
```
