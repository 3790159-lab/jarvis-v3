"""Personal Generation Mode — generate user in different roles, places, styles."""
from __future__ import annotations

from typing import Dict, List, Optional

from app.services.lora_manager import generate_with_lora, list_loras

ROLES_TEMPLATES: Dict[str, str] = {
    "bodybuilder": (
        "muscular professional bodybuilder on competition stage, "
        "oiled skin, dramatic stage lighting, trophy pose, "
        "sports arena background"
    ),
    "businessman": (
        "successful businessman in expensive Italian suit, "
        "modern glass office with city view, confident power pose, "
        "professional headshot quality"
    ),
    "chef": (
        "professional executive chef in white chef coat and toque, "
        "luxury restaurant kitchen, plating a gourmet dish, "
        "culinary magazine quality"
    ),
    "model": (
        "fashion model on Milan runway, designer haute couture clothing, "
        "fashion week atmosphere, editorial magazine photoshoot, "
        "dramatic lighting"
    ),
    "athlete": (
        "professional athlete in competition gear, "
        "dynamic action pose at sports venue, "
        "sports magazine cover quality"
    ),
    "scientist": (
        "scientist in modern laboratory, white lab coat, "
        "surrounded by advanced equipment, focused expression, "
        "research institute setting"
    ),
    "rockstar": (
        "rock star on massive concert stage, "
        "crowd of thousands in background, spotlight, "
        "electric guitar, epic concert photography"
    ),
    "astronaut": (
        "astronaut in NASA spacesuit, space station interior, "
        "Earth visible through window, professional space photography"
    ),
    "ceo": (
        "Fortune 500 CEO in boardroom, "
        "floor-to-ceiling windows with city skyline, "
        "commanding presence, professional executive portrait"
    ),
    "superhero": (
        "superhero in dramatic costume, "
        "cinematic lighting, city rooftop, "
        "action movie poster quality"
    ),
}

PLACES_TEMPLATES: Dict[str, str] = {
    "maldives": "luxury overwater bungalow in Maldives, crystal blue lagoon, tropical paradise",
    "paris": "romantic Paris street, Eiffel Tower background, golden sunset, French cafe",
    "dubai": "Dubai skyline, Burj Khalifa, luxury penthouse terrace, night city lights",
    "tokyo": "vibrant Tokyo street, neon signs, Shibuya crossing, night photography",
    "mountains": "epic mountain landscape, alpine meadow, dramatic clouds, adventure photography",
    "beach": "pristine white sand beach, sunset over ocean, golden hour lighting",
    "casino": "luxury casino interior, Monte Carlo style, roulette table, elegant atmosphere",
    "yacht": "luxury superyacht deck, mediterranean sea, summer vacation, lifestyle photography",
}

STYLE_TEMPLATES: Dict[str, str] = {
    "cyberpunk": "cyberpunk aesthetic, neon lights, futuristic city, blade runner style, rain reflections",
    "vintage": "vintage 1950s style, film grain, retro color palette, classic Americana",
    "oil_painting": "classical oil painting style, renaissance portraiture, museum quality",
    "anime": "high-quality anime style, Studio Ghibli aesthetic, vibrant colors",
    "noir": "black and white film noir, dramatic shadows, detective movie atmosphere",
    "watercolor": "delicate watercolor painting, soft colors, artistic brushstrokes",
    "pop_art": "pop art style, Andy Warhol aesthetic, bold colors, graphic design",
    "fantasy": "epic fantasy art, magical world, dramatic lighting, high detail digital art",
}


def _get_primary_lora(user_id: str) -> Optional[Dict]:
    """Get first ready LoRA for user."""
    loras = list_loras(user_id)
    ready = [l for l in loras if l.get("status") == "succeeded"]
    return ready[0] if ready else None


def generate_me_as(
    user_id: str,
    role: str,
    extra_details: str = "",
) -> str:
    """Generate user in a specific role using their LoRA."""
    lora = _get_primary_lora(user_id)
    if not lora:
        raise ValueError("Сначала обучи LoRA через /lora_train")

    role_prompt = ROLES_TEMPLATES.get(role, role)
    parts = [f"as {role_prompt}"]
    if extra_details:
        parts.append(extra_details.strip())
    full_prompt = ", ".join(parts)

    return generate_with_lora(lora["name"], full_prompt, user_id)


def generate_me_in(
    user_id: str,
    place: str,
    extra_details: str = "",
) -> str:
    """Generate user in a specific location using their LoRA."""
    lora = _get_primary_lora(user_id)
    if not lora:
        raise ValueError("Сначала обучи LoRA через /lora_train")

    place_prompt = PLACES_TEMPLATES.get(place, place)
    parts = [f"standing in {place_prompt}"]
    if extra_details:
        parts.append(extra_details.strip())
    full_prompt = ", ".join(parts)

    return generate_with_lora(lora["name"], full_prompt, user_id)


def generate_me_in_style(
    user_id: str,
    style: str,
    extra_details: str = "",
) -> str:
    """Generate user portrait in a specific artistic style using their LoRA."""
    lora = _get_primary_lora(user_id)
    if not lora:
        raise ValueError("Сначала обучи LoRA через /lora_train")

    style_prompt = STYLE_TEMPLATES.get(style, style)
    parts = [f"portrait in {style_prompt} style"]
    if extra_details:
        parts.append(extra_details.strip())
    full_prompt = ", ".join(parts)

    return generate_with_lora(lora["name"], full_prompt, user_id)


def list_roles() -> List[Dict]:
    """Return available roles with descriptions."""
    descriptions = {
        "bodybuilder": "Бодибилдер на сцене соревнований",
        "businessman": "Успешный бизнесмен в офисе",
        "chef": "Шеф-повар в ресторане",
        "model": "Модель на подиуме",
        "athlete": "Профессиональный спортсмен",
        "scientist": "Учёный в лаборатории",
        "rockstar": "Рок-звезда на сцене",
        "astronaut": "Астронавт на МКС",
        "ceo": "CEO в переговорной Fortune 500",
        "superhero": "Супергерой на крыше небоскрёба",
    }
    return [{"role": r, "description": descriptions.get(r, r)} for r in ROLES_TEMPLATES]


def list_places() -> List[Dict]:
    """Return available places with descriptions."""
    descriptions = {
        "maldives": "Мальдивы — бунгало над водой",
        "paris": "Париж — Эйфелева башня, закат",
        "dubai": "Дубай — Бурдж-Халифа, пентхаус",
        "tokyo": "Токио — неоновые огни, Сибуя",
        "mountains": "Горы — альпийский пейзаж",
        "beach": "Пляж — белый песок, закат",
        "casino": "Казино — Монте-Карло стиль",
        "yacht": "Яхта — Средиземное море",
    }
    return [{"place": p, "description": descriptions.get(p, p)} for p in PLACES_TEMPLATES]


def list_styles() -> List[Dict]:
    """Return available artistic styles."""
    descriptions = {
        "cyberpunk": "Киберпанк — неон и будущее",
        "vintage": "Винтаж — стиль 1950-х",
        "oil_painting": "Масляная живопись — ренессанс",
        "anime": "Аниме — Studio Ghibli стиль",
        "noir": "Нуар — чёрно-белое кино",
        "watercolor": "Акварель — мягкие тона",
        "pop_art": "Поп-арт — Andy Warhol",
        "fantasy": "Фэнтези — эпичная цифровая живопись",
    }
    return [{"style": s, "description": descriptions.get(s, s)} for s in STYLE_TEMPLATES]
