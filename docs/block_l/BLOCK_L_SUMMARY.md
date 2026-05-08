# Block L MEGA — Summary

## Overview

Block L adds four AI-powered creative generation capabilities to Jarvis V3 Supervisor:

| Sub-block | Feature | Command(s) |
|---|---|---|
| L.0 | Shared infrastructure | (internal) |
| L.1 | Figma MCP Bridge | `/design` |
| L.2 | bolt.diy App Builder | `/create_app`, `/create_simple` |
| L.3 | Smart Photo Prompts | `/smart_photo`, `/pro_food` |
| L.4 | Landing Brief 2.0 | `/landing_brief`, `/landing_demo` |

---

## Quick Command Reference

### Design Studio (L.1)
```
/design <description>    — Generate design brief + queue for Figma MCP
/figma_queue             — List pending Figma design requests
/figma_status <id>       — Check status of specific design request
/figma_clear             — Clear completed requests
```

### AI App Builder (L.2)
```
/create_app <description>    — Full app spec + bolt.diy prompt
/create_simple <description> — Simplified 3-feature spec
/bolt_status                 — Check if bolt.diy is running
/bolt_open                   — Open bolt.diy in browser
/bolt_queue                  — List pending app build requests
```

### Smart Photo Prompts (L.3)
```
/smart_photo <description>   — Auto-detect category + enhance to professional level
/pro_food <dish>             — Food photography specialist (Jonathan Lovekin style)
```

### Landing Brief 2.0 (L.4)
```
/landing_brief               — Start 8-step landing brief interview
/landing_demo                — See example briefs
/cancel                      — Cancel active brief session
/landing <topic>             — Quick landing without brief (legacy)
```

---

## Workflow Diagrams

### L.1 Figma MCP Workflow
```
User: /design "SaaS dashboard"
        |
        v
Claude generates design brief
(project_name, color_palette, components, etc.)
        |
        v
FigmaQueue.add() -> state/figma_queue/<id>.json [pending]
        |
        v
User copies queue ID
        |
        v
Paste into Claude Code with Figma MCP
        |
        v
Claude Code calls figma-remote MCP tools
        |
        v
FigmaQueue.mark_completed() -> status: completed
```

### L.2 bolt.diy Workflow
```
User: /create_app "todo app with AI"
        |
        v
Claude generates AppSpec
(features, tech_stack, bolt_diy_prompt, etc.)
        |
        v
BoltQueue.add() -> state/bolt_queue/<id>.json [pending]
        |
        v
User opens bolt.diy (localhost:5173)
        |
        v
User pastes bolt_diy_prompt into bolt.diy
        |
        v
bolt.diy generates full application code
```

### L.3 Smart Photo Workflow
```
User: /smart_photo "autumn park at sunset"
        |
        v
detect_category() -> "place"
        |
        v
smart_enhance() with place-specific system prompt
  [cache check: state/smart_prompts_cache/<hash>.json]
        |
        v
Enhanced prompt: "Golden hour photography, 
Canon EOS R5, 24-70mm f/2.8L, ..."
        |
        v
generate_images_replicate(enhanced_prompt)
        |
        v
Image sent to Telegram
```

### L.4 Landing Brief Workflow
```
User: /landing_brief
        |
        v
LandingBriefSession created (state/landing_briefs/<uid>/<id>.json)
        |
        v
Step 1/8: Business name?
Step 2/8: Target audience?
...
Step 8/8: Contacts?
        |
        v
generate_landing_content() -> hero, features, testimonials, FAQ
        |
        v
generate_landing_v2() -> complete HTML (5 style templates)
        |
        v
save_landing_v2() -> state/landings/landing_v2_<biz>_<ts>.html
        |
        v
HTML sent as Telegram document
```

---

## Example Sessions

### Design Brief
```
/design minimalist e-commerce app for handmade jewelry

Response:
Design Brief #20260503_142315_123 created!
Project: Artisan Jewels
Style: minimalist / luxury
Colors: #1A1A1A, #C9A96E, #F5F5F0
Components: Product card, Cart overlay, Checkout flow
Queue ID: 20260503_142315_123
Status: pending (queued for Figma MCP)
```

### App Spec
```
/create_app recipe manager with grocery list

Response:
App Spec: RecipeMate
"Smart recipes, smarter shopping"
Stack: React + Node.js + SQLite
Features: Recipe CRUD, Ingredient parser, Auto grocery list
bolt.diy prompt: [200-word detailed spec]
Queue ID: 20260503_143001_456
```

### Smart Photo
```
/smart_photo bowl of ramen with soft lighting

Category: food
Enhanced: "Close-up food photography of ramen bowl, 
natural window light, Canon EOS 5D Mark IV, 
100mm macro f/2.8, shallow depth of field, 
steam wisps, wooden chopsticks, dark moody background..."
```

### Landing Brief (8 steps)
```
/landing_brief
1/8 Business name? -> Cafe Lapin
2/8 Target audience? -> Families 25-45
3/8 Main product? -> French cuisine
4/8 Key advantages? -> Quality, Ambiance, Service
5/8 Call to action? -> Book a table
6/8 Color scheme? -> warm
7/8 Style? -> luxury
8/8 Contacts? -> info@cafelapin.com

Result: landing_v2_Cafe_Lapin_20260503_144512.html (68KB)
```

---

## Architecture

### Shared Infrastructure (L.0)
- `app/services/block_l_common.py` — Claude API wrapper with retry/backoff, atomic JSON I/O, Telegram helpers
- All Block L services accept `claude_api_fn=None` (injectable for testing)
- API calls logged to `state/block_l_api_calls.jsonl`

### Queue Pattern (L.1, L.2)
- JSON files in `state/figma_queue/` and `state/bolt_queue/`
- Status lifecycle: `pending` → `processing` → `completed` / `failed`
- Log files: `state/figma_queue/log.jsonl`, `state/bolt_queue/log.jsonl`
- IDs use microsecond precision to avoid collisions

### Cache Pattern (L.3)
- SHA-256 hash of normalized prompt → 12-char hex filename
- TTL: 7 days
- Location: `state/smart_prompts_cache/`

### Session Pattern (L.4)
- Per-user JSON files in `state/landing_briefs/<user_id>/`
- `find_active(user_id)` returns most recent active session
- Free-text `handle()` intercepts answers before AI routing

---

## Known Limitations

1. **Figma MCP requires Claude Code** — The Figma queue is not auto-processed; user must paste IDs into Claude Code with Figma MCP server configured.

2. **bolt.diy requires local setup** — Must run `pnpm run dev` in the bolt.diy directory before `/create_app` can open it.

3. **Smart Photo categories** — 6 categories (food/design/people/place/object/abstract). Unusual subjects may be classified as "abstract" and use a general prompt.

4. **Landing Brief session per user** — Only one active session per user_id at a time. Starting a new one cancels the previous.

5. **Landing content is AI-generated** — Testimonials and FAQs are synthetic. Users should review before publishing.

6. **Prompt cache is shared** — All users share the same smart_prompts cache. Different users asking similar prompts will get the same enhancement.

---

## State Directory Layout

```
state/
  block_l_api_calls.jsonl     -- All Claude API calls log
  figma_queue/
    <id>.json                 -- Individual design requests
    log.jsonl                 -- Queue operation log
  bolt_queue/
    <id>.json                 -- Individual app specs
    log.jsonl
  smart_prompts_cache/
    <hash12>.json             -- Cached enhanced prompts (7-day TTL)
  landing_briefs/
    <user_id>/
      <id>.json               -- Session state (8 steps)
  landings/
    landing_v2_<biz>_<ts>.html  -- Generated landing pages
```

---

## Future Improvements (Block M Ideas)

1. **Auto Figma processing** — Background worker that polls figma_queue and calls MCP automatically
2. **bolt.diy webhook** — When bolt.diy finishes building, auto-notify via Telegram
3. **Smart Photo batch** — `/photo_batch <n> <description>` generates N variants
4. **Landing A/B** — Generate 2 landing variants with different styles, user picks
5. **Landing deploy** — Auto-deploy generated HTML to GitHub Pages or Netlify
6. **Design iteration** — `/design_refine <id> <feedback>` to iterate on a brief
7. **App to Figma** — After `/create_app`, auto-generate matching Figma wireframes
8. **Portfolio mode** — `/portfolio` lists all generated landings/apps/designs with previews
9. **Multi-language briefs** — Generate landing content in English, then translate to Russian
10. **Smart Photo styles** — Additional specialty styles (product photography, real estate, etc.)
