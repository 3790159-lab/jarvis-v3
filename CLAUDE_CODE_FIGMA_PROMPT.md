# Process Figma Queue — Claude Code Instructions

Paste this prompt in Claude Code to process the Figma design queue:

---

Process figma queue.

Steps:
1. Read state/figma_queue/ — find the newest item with status="pending"
2. Mark it as "processing" by editing the JSON file: set "status": "processing"
3. Use figma-remote MCP tools to create the design:
   - Call `generate_figma_design` or `create_new_file` with the brief data
   - Add a desktop frame (1440x900) with components from brief.components
   - Add a mobile frame (375x812) with mobile sections from brief.mobile_frame.sections
   - Apply color_palette from the brief (primary, secondary, accent, bg, text colors)
   - Apply typography from the brief (heading_font, body_font)
4. Once done, get the Figma file URL from the MCP response
5. Mark the queue item as "completed":
   - Set "status": "completed"
   - Set "figma_url": "<url from step 4>"
   - Set "processed_at": "<current ISO timestamp>"
6. Append a line to state/figma_queue/log.jsonl:
   {"ts": "<timestamp>", "action": "completed", "id": "<queue_id>", "figma_url": "<url>"}
7. Print: "Design complete: <project_name> — <figma_url>"

---

Brief structure reference:
- brief.project_name — file name to use in Figma
- brief.color_palette.primary/secondary/accent/bg/text — hex colors
- brief.typography.heading_font/body_font — font names
- brief.components — list of {name, content, type} sections
- brief.desktop_frame.sections — ordered list of section names for desktop
- brief.mobile_frame.sections — ordered list for mobile
