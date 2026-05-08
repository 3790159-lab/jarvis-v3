# Figma MCP Guide — Block L.1

## How to use /design

1. Send `/design <description>` in Telegram
2. Claude AI generates a detailed design brief (colors, typography, components)
3. Brief is saved to `state/figma_queue/`
4. You get a confirmation message with a queue ID

Example prompts:
- `/design landing page for family restaurant with warm aesthetic`
- `/design mobile fitness tracker app, modern dark theme`
- `/design SaaS analytics dashboard, business style`
- `/design portfolio site for photographer, minimal luxury`

## How to process the queue in Claude Code

1. Open Claude Code in the project directory
2. Paste the prompt from `CLAUDE_CODE_FIGMA_PROMPT.md`
3. Claude Code reads the queue, calls figma-remote MCP, creates real Figma design
4. Design appears in your Figma workspace

## Figma MCP Commands reference

- `/figma_queue` — show pending designs
- `/figma_status` — show last 5 designs with status
- `/figma_clear` — delete completed designs older than 7 days

## Troubleshooting

**MCP not connected**
- Open Claude Code
- Run `/mcp` to see connected servers
- If figma-remote is missing: Settings > MCP > add HTTP server

**Rate limit (free Figma)**
- Free plan: ~6 file API calls/month
- Professional plan: unlimited
- Workaround: use the JSON wireframe from `state/designs/` instead

**Empty or broken design**
- Check the brief JSON in `state/figma_queue/`
- Run `/design` again with more specific prompt
- Be explicit: mention industry, audience, specific sections

**"figma_url": null after processing**
- Check Claude Code output for errors
- Ensure figma-remote MCP is authenticated
- Try `/mcp` in Claude Code to verify connection

## Tips for better designs

- **Be specific**: "restaurant landing for families" beats "website"
- **Mention industry**: healthcare, fintech, food, education, etc.
- **Mention vibe**: luxury, playful, minimal, dark, warm, corporate
- **Mention audience**: young adults, seniors, B2B, families
- **Iterate**: start with `/design`, refine with another `/design`

## Queue file format

`state/figma_queue/<YYYYMMDD_HHMMSS>.json`:
```json
{
  "id": "20260504_143022",
  "brief": {
    "project_name": "Cafe Lapin",
    "project_type": "landing",
    "design_style": "warm",
    "color_palette": {"primary": "#D4622A", ...},
    "components": [...],
    "desktop_frame": {...},
    "mobile_frame": {...}
  },
  "status": "pending",
  "created_at": "2026-05-04T14:30:22",
  "processed_at": null,
  "figma_url": null
}
```
