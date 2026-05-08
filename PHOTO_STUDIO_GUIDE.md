# Photo Studio Ultimate — Complete Guide

## Overview

Photo Studio is a unified pipeline for professional photo generation and editing.
Built on Replicate FLUX 1.1 Pro + LoRA + Face Swap + GFPGAN.

---

## Commands Reference

### 🍽 Restaurant Pro

| Command | Description | Cost |
|---------|-------------|------|
| `/menu_photo <блюдо>` | Professional food photo | $0.04 |
| `/menu_photo <блюдо> --style modern` | Minimalist style | $0.04 |
| `/social_post <блюдо>` | Photo + Instagram caption | $0.04 |
| `/menu_book` | Series of photos for all dishes | $0.04 × N |
| `/dish_styles` | Show all available styles | free |

**Available food styles:**
- `rustic` — Warm wood background, Canon 5D, natural light
- `modern` — White marble, overhead, Michelin-star
- `dark` — Dramatic moody, black slate, steam
- `instagram` — Vibrant, shallow depth-of-field

**Examples:**
```
/menu_photo борщ
/menu_photo стейк рибай --style dark
/social_post тирамису
```

---

### 🎉 Party Pro

| Command | Description | Cost |
|---------|-------------|------|
| `/party_promo <тема>` | Vertical poster 9:16 + promo text | $0.06 |
| `/invite_card <имя> <событие> <дата>` | Personal invitation card 3:4 | $0.06 |
| `/event_photo <описание>` | Event venue/atmosphere photo | $0.06 |

**Available themes:** `nye`, `halloween`, `birthday`, `wedding`, `summer`, `corporate`, `masquerade`, `pool`

**Examples:**
```
/party_promo halloween
/party_promo nye
/invite_card Иван "Новый Год" "31 декабря 2026"
/event_photo банкетный зал с хрустальными люстрами
```

---

### 🔄 Face Swap Engine

| Command | Description | Cost |
|---------|-------------|------|
| `/faceswap` | Interactive: send 2 photos → swapped result | $0.005 |
| `/faceswap_hq` | Face swap + GFPGAN polish | $0.007 |
| `/enhance` | Enhance/restore faces in a photo | $0.002 |
| `/me_into <image_url>` | Put your face into target image | $0.005 |

**Flow for /faceswap:**
1. Send `/faceswap`
2. Upload **source** (your face)
3. Upload **target** (image where to put face)
4. Receive result

---

### 🧑 Personal Generation (requires LoRA)

| Command | Description | Cost |
|---------|-------------|------|
| `/me_as <роль>` | You as bodybuilder/chef/CEO/etc | $0.03 |
| `/me_in <место>` | You in Maldives/Paris/Dubai/etc | $0.03 |
| `/me_style <стиль>` | Your portrait in cyberpunk/anime/noir style | $0.03 |
| `/me_roles` | List available roles | free |

**Available roles:** `bodybuilder`, `businessman`, `chef`, `model`, `athlete`, `scientist`, `rockstar`, `astronaut`, `ceo`, `superhero`

**Available places:** `maldives`, `paris`, `dubai`, `tokyo`, `mountains`, `beach`, `casino`, `yacht`

**Available styles:** `cyberpunk`, `vintage`, `oil_painting`, `anime`, `noir`, `watercolor`, `pop_art`, `fantasy`

**Examples:**
```
/me_as bodybuilder
/me_in maldives
/me_style cyberpunk
```

---

### 🎓 LoRA Training (one-time setup)

| Command | Description | Cost |
|---------|-------------|------|
| `/lora_train` | Train custom LoRA on your photos | $10 one-time |
| `/lora_list` | List your trained models | free |
| `/lora_status <name>` | Check training progress | free |
| `/lora_delete <name>` | Delete a LoRA model | free |

**Training flow:**
1. Send `/lora_train`
2. Upload 15-20 clear photos of yourself
3. Enter model name (e.g., "Daniil")
4. Enter trigger word (default: "DANIIL")
5. Confirm $10 cost
6. Wait ~30 minutes
7. Receive notification when ready

**Requirements:**
- Minimum 10 photos (15-20 recommended)
- Clear face visible
- Various angles/lighting preferred

---

### 🎨 General Generation

| Command | Description | Cost |
|---------|-------------|------|
| `/photo <prompt>` | Free-form generation | $0.04–0.06 |
| `/photo_ultra <prompt>` | Premium FLUX Pro Ultra | $0.06 |

---

## Smart Auto-Detection

When you send a free-form message, the Smart Router automatically detects the best pipeline:

| Your message contains | Detected pipeline | Cost |
|----------------------|-------------------|------|
| "борщ", "еда", "меню", "food" | Restaurant | $0.04 |
| "вечеринка", "party", "halloween" | Party | $0.06 |
| "я", "меня", "me as", "me in" | Personal (LoRA) | $0.03 |
| "face swap", "фейсвап" | Face Swap | $0.005 |
| Image attached + "улучши" | Face Enhance | $0.002 |
| Image attached (general) | Image-to-Image | $0.025 |
| Anything else | General FLUX | $0.06 |

After detection you see:
```
Рекомендую: Restaurant Photo
💰 Стоимость: ~$0.040
📋 Detected food/restaurant keywords

[✅ Подтвердить] [🔄 Изменить] [❌ Отмена]
```

---

## Pipeline Costs Summary

| Pipeline | Model | Cost per generation |
|----------|-------|---------------------|
| Food photo | FLUX 1.1 Pro | $0.04 |
| Party poster | FLUX 1.1 Pro Ultra | $0.06 |
| Portrait/people | FLUX 1.1 Pro Ultra | $0.06 |
| Face Swap basic | omniedge face-swap | $0.005 |
| Face Swap + polish | face-swap + GFPGAN | $0.007 |
| Face Enhance (GFPGAN) | TencentARC GFPGAN | $0.002 |
| LoRA generation | lucataco/flux-dev-lora | $0.03 |
| LoRA + face swap + polish | Combined | $0.037 |
| Image-to-Image | FLUX Redux | $0.025 |
| LoRA Training (one-time) | ostris/flux-dev-lora-trainer | **$10.00** |

---

## When to use what

| Use case | Best command |
|----------|-------------|
| Instagram post for restaurant | `/social_post борщ` |
| Full menu photoshoot | `/menu_book` |
| Halloween party poster | `/party_promo halloween` |
| Personal invite for VIP | `/invite_card Иван "День рождения" "5 мая"` |
| Put your face in cool photo | `/faceswap` |
| Generate yourself as businessman | `/me_as businessman` (needs LoRA) |
| Dream vacation photo | `/me_in maldives` (needs LoRA) |
| Artistic portrait | `/me_style cyberpunk` (needs LoRA) |

---

## Architecture

```
PhotoStudio
├── restaurant_mode.py    — FOOD_TEMPLATES, generate_dish_photo, generate_social_post
├── party_mode.py         — PARTY_THEMES, generate_party_promo, generate_invite_card
├── face_swap.py          — face_swap_basic, enhance_face, face_swap_with_polish
├── lora_manager.py       — start_lora_training, check_lora_status, generate_with_lora
├── personal_mode.py      — generate_me_as, generate_me_in, generate_me_in_style
├── smart_photo_router.py — analyze_photo_request, composite_lora_plus_swap
├── replicate_models.py   — model catalog with costs and URLs
└── image_library.py      — persistent history + cost tracking
```

---

## Environment Variables

```env
REPLICATE_API_KEY=your_key_here   # Required for all generation
ANTHROPIC_API_KEY=your_key_here   # Optional — used for captions and promo text
```
