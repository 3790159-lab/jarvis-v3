"""Party Pro — promo posters, invitation cards, and event photo generation."""
from __future__ import annotations

from typing import Dict, List, Optional

from app.services.replicate_image_gen import generate_images_replicate
from app.services.claude_helper import call_claude

PARTY_THEMES: Dict[str, str] = {
    "nye": (
        "elegant New Year Eve party, gold and black decorations, "
        "champagne glasses, fireworks in background, luxurious ballroom, "
        "sparkling confetti, sophisticated atmosphere"
    ),
    "halloween": (
        "spooky Halloween party, carved jack-o-lanterns, "
        "dark mysterious atmosphere, fog machine effect, "
        "orange and black decorations, costume party, candles"
    ),
    "birthday": (
        "festive birthday party, colorful balloons and streamers, "
        "beautiful tiered cake with candles, warm fairy lights, "
        "celebration atmosphere, confetti, joyful"
    ),
    "wedding": (
        "elegant wedding reception, white roses and peonies, "
        "candelabras, soft candlelight, romantic atmosphere, "
        "fine dining tables, luxury venue, timeless elegance"
    ),
    "summer": (
        "summer beach party, tropical palm trees, colorful cocktails, "
        "golden sunset over ocean, relaxed festive vibes, "
        "tiki torches, outdoor summer celebration"
    ),
    "corporate": (
        "professional corporate event, sleek modern decor, "
        "business networking setup, elegant lighting, "
        "branded stage backdrop, professional atmosphere, "
        "conference gala dinner style"
    ),
    "masquerade": (
        "glamorous masquerade ball, ornate masks, candlelit ballroom, "
        "Venetian style, gold and burgundy, mystery and elegance"
    ),
    "pool": (
        "luxury pool party, resort swimming pool, colorful inflatables, "
        "tropical drinks, beautiful outdoor setting, summer vibes"
    ),
}

POSTER_ASPECT = "9:16"   # Stories / vertical poster
CARD_ASPECT = "3:4"      # Invitation card


def build_party_prompt(theme: str, extra: str = "", poster: bool = True) -> str:
    """Build a high-quality party promo prompt."""
    base = PARTY_THEMES.get(theme, theme)
    suffix = "professional poster design, magazine quality, vibrant colors, photorealistic"
    if not poster:
        suffix = "elegant design, premium quality, photorealistic"
    parts = [base]
    if extra:
        parts.append(extra.strip())
    parts.append(suffix)
    return ", ".join(parts)


def generate_party_promo(
    theme: str,
    custom_text: str = "",
) -> Dict:
    """Generate a party promo poster + suggested promo text."""
    prompt = build_party_prompt(theme, custom_text, poster=True)
    urls = generate_images_replicate(
        prompt,
        num_images=1,
        aspect_ratio=POSTER_ASPECT,
        style="realistic",
    )

    text_prompt = (
        f'Создай текст для промо-поста вечеринки "{theme}".\n'
        "Формат:\n"
        "🎉 Название\n"
        "📅 Дата: [укажи дату]\n"
        "📍 Место: [укажи место]\n"
        "🍷 Что ожидать\n"
        "✨ Особенности\n\n"
        "Не более 100 слов. Пиши по-русски."
    )
    promo_text = call_claude(text_prompt) or f"🎉 {theme.upper()} PARTY\n📅 Дата\n📍 Место\n✨ Незабываемый вечер!"

    return {
        "poster_url": urls[0] if urls else "",
        "promo_text": promo_text,
        "theme": theme,
        "format": "9:16 (Stories)",
    }


def generate_invite_card(
    guest_name: str,
    event: str,
    date: str,
) -> Dict:
    """Generate a personalised invitation card."""
    prompt = (
        f"elegant invitation card for {event}, "
        "sophisticated typographic design, gold foil accents, "
        "premium paper texture, luxury stationery style, "
        "ornate border, formal invitation"
    )
    urls = generate_images_replicate(
        prompt,
        num_images=1,
        aspect_ratio=CARD_ASPECT,
        style="realistic",
    )
    personal_text = (
        f"Дорогой {guest_name}!\n\n"
        f"Мы рады пригласить тебя на {event}.\n"
        f"📅 {date}\n\n"
        "Ждём тебя!"
    )
    return {
        "card_url": urls[0] if urls else "",
        "personal_text": personal_text,
        "guest_name": guest_name,
        "event": event,
        "date": date,
    }


def generate_event_photo(venue_description: str) -> str:
    """Generate a photo of an event venue/atmosphere."""
    prompt = (
        f"{venue_description}, "
        "professional event photography, stunning venue, "
        "beautiful atmospheric lighting, wide angle shot"
    )
    urls = generate_images_replicate(
        prompt,
        num_images=1,
        aspect_ratio="16:9",
        style="realistic",
    )
    return urls[0] if urls else ""


def list_themes() -> List[Dict]:
    """Return all available party themes with descriptions."""
    short_desc = {
        "nye": "Новый год — элегантность и шампанское",
        "halloween": "Хэллоуин — мистика и тыквы",
        "birthday": "День рождения — шары и торт",
        "wedding": "Свадьба — романтика и цветы",
        "summer": "Летняя вечеринка — пляж и коктейли",
        "corporate": "Корпоратив — профессионально и стильно",
        "masquerade": "Маскарад — тайна и гламур",
        "pool": "Pool Party — бассейн и лето",
    }
    return [
        {"theme": t, "description": short_desc.get(t, t)}
        for t in PARTY_THEMES
    ]
