# H4 Telegram Integration — Photo Studio Commands

## Overview

All Photo Studio modules are now connected to the Telegram bot.
Commands are handled by `tools/photo_studio_telegram.py`.

---

## Restaurant Commands

| Command | Description | Cost |
|---------|-------------|------|
| `/menu_photo <блюдо>` | Professional food photo | $0.04 |
| `/menu_photo <блюдо> --style <стиль>` | Custom style food photo | $0.04 |
| `/social_post <блюдо>` | Photo + caption + hashtags + buttons | $0.04 |
| `/menu_book <блюдо1, блюдо2, ...>` | Series of photos in one style | $0.04×N |
| `/dish_styles` | Show all available styles | free |

**Social post buttons:**
- `📋 В Obsidian` — save to Obsidian vault
- `📤 В Instagram via n8n` — trigger n8n autopost workflow
- `🔄 Перегенерировать` — regenerate the post

---

## Party Commands

| Command | Description | Cost |
|---------|-------------|------|
| `/party_promo <тема>` | Vertical poster 9:16 | $0.06 |
| `/party_promo <тема> <текст>` | Poster with custom text | $0.06 |
| `/invite_card <имя> "<событие>" "<дата>"` | Personal invitation 3:4 | $0.06 |
| `/event_photo <описание>` | Event atmosphere photo | $0.06 |
| `/party_themes` | List 8 available themes | free |

**Themes:** `nye` `halloween` `birthday` `wedding` `summer` `corporate` `masquerade` `pool`

---

## Face Swap (Multi-step)

### `/faceswap` — Interactive 3-step workflow:

1. Send `/faceswap`
2. **Upload source photo** (face to transplant)
3. **Upload target photo** (image where to put the face)
4. Choose quality: `[🔵 Basic $0.005]` or `[✨ Polished $0.007]`
5. Receive result

Your source face is automatically saved for future `/me_into` calls.

### `/enhance`
Upload any photo → GFPGAN face enhancement → restored photo ($0.002)

### `/me_into`
Puts your last saved face into a new target photo ($0.005)

---

## LoRA Training (Multi-step)

### `/lora_train` — 4-step workflow:

1. Send `/lora_train`
2. **Upload 15-20 photos** (your face, different angles)
3. Send `/lora_done` when finished
4. **Enter model name** (e.g. `Daniil`)
5. **Enter trigger word** (or `/skip` for default = `NAME`)
6. Confirm: `[$10 — Start Training]`
7. Training runs ~30 minutes
8. Automatic Telegram notification when done

### Management commands:

| Command | Description |
|---------|-------------|
| `/lora_list` | Your LoRA models with status |
| `/lora_status <name>` | Check training progress |
| `/lora_delete <name>` | Delete a model |

---

## Personal Mode (requires LoRA)

| Command | Description | Cost |
|---------|-------------|------|
| `/me_as <роль>` | You as bodybuilder/CEO/chef/etc | $0.03 |
| `/me_in <место>` | You in Maldives/Paris/Dubai/etc | $0.03 |
| `/me_with <предмет>` | You with Lambo/guitar/etc | $0.03 |
| `/me_style <стиль>` | Your portrait in cyberpunk/anime/etc | $0.03 |
| `/me_roles` | List 10 available roles | free |
| `/me_places` | List 8 available places | free |
| `/me_styles` | List 8 available styles | free |

> If you don't have a LoRA yet, you'll get a prompt to run `/lora_train` first.

---

## Smart Photo Router (H4.6)

When you send **free text** (no command), the Smart Router auto-detects photo intent:

| Message contains | Detected pipeline | Cost |
|-----------------|-------------------|------|
| борщ / еда / food | Restaurant photo | $0.04 |
| вечеринка / halloween / party | Party promo | $0.06 |
| я / меня / me as | Personal (if LoRA ready) | $0.03 |
| face swap / фейсвап | Face swap workflow | $0.005 |
| улучши + photo | Enhance | $0.002 |

After detection, you see a confirmation keyboard:
```
[✅ Подтвердить] [🔄 Изменить] [❌ Отмена]
```

---

## Conversation State

Multi-step flows store state in `state/conversations/<chat_id>.json`.

States used:
- `faceswap_source` → waiting for source photo
- `faceswap_target` → waiting for target photo
- `faceswap_confirm` → waiting for quality choice
- `lora_collecting` → collecting training photos
- `lora_name` → waiting for model name
- `lora_trigger` → waiting for trigger word
- `lora_confirm` → waiting for $10 confirmation
- `enhance_upload` → waiting for photo to enhance
- `meinto_target` → waiting for target photo
- `photo_router_confirm` → waiting for pipeline confirmation
