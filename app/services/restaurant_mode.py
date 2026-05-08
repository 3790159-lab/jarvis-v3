"""Restaurant Pro — professional menu photo and social post generation."""
from __future__ import annotations

from typing import Dict, List, Optional

from app.services.replicate_image_gen import generate_images_replicate
from app.services.claude_helper import call_claude

FOOD_TEMPLATES: Dict[str, str] = {
    "rustic": (
        "rustic food photography, warm golden lighting, "
        "wooden background, natural shadows, "
        "shot on Canon 5D Mark IV, 50mm macro lens, f/2.8, "
        "magazine quality, high resolution"
    ),
    "modern": (
        "modern minimalist food photography, "
        "white marble background, soft natural daylight, "
        "professional plating, top-down overhead view, "
        "Michelin star restaurant style, 4k ultra sharp"
    ),
    "dark": (
        "dark moody food photography, dramatic chiaroscuro lighting, "
        "black slate background, steam rising from hot dish, "
        "magazine cover quality, Hasselblad medium format style, "
        "luxury restaurant atmosphere"
    ),
    "instagram": (
        "vibrant bright food photography for Instagram, "
        "vivid colors, beautiful attractive plating, "
        "shallow depth of field, bokeh background, "
        "professional food stylist composition, lifestyle aesthetic"
    ),
}

ASPECT_RATIOS: Dict[str, str] = {
    "rustic": "4:3",
    "modern": "1:1",
    "dark": "4:3",
    "instagram": "1:1",
}

SOCIAL_POST_TIMES: Dict[str, str] = {
    "rustic": "12:00",
    "modern": "18:00",
    "dark": "20:00",
    "instagram": "19:00",
}


RUSSIAN_DISHES_EN: Dict[str, str] = {
    "борщ": "borscht (traditional Ukrainian beet soup with cabbage, sour cream, fresh dill, served in deep ceramic bowl)",
    "вареники": "varenyky (Ukrainian dumplings filled with potato or cherry, with sour cream)",
    "пельмени": "pelmeni (Russian meat dumplings in broth, garnished with parsley)",
    "плов": "plov (Uzbek rice pilaf with lamb chunks and carrots, in cast iron)",
    "шашлык": "shashlik (grilled meat skewers with onions and herbs)",
    "блины": "blini (thin Russian pancakes stack with sour cream, jam, caviar)",
    "сырники": "syrniki (Russian cottage cheese pancakes with berries)",
    "оливье": "Olivier salad (Russian potato salad with vegetables, eggs, peas)",
    "холодец": "holodets (Russian meat aspic with herbs, served cold)",
    "пирожки": "pirozhki (golden Russian baked stuffed buns)",
    "котлеты": "kotleti (Russian meat patties with mashed potatoes)",
    "солянка": "solyanka (thick spicy Russian soup with sausage, olives, lemon)",
    "окрошка": "okroshka (cold Russian summer soup with kvass, vegetables)",
    "медовик": "medovik (Russian honey layer cake with cream)",
    "запеканка": "zapekanka (Russian cottage cheese casserole with raisins)",
    "голубцы": "golubtsy (cabbage rolls stuffed with meat and rice in tomato sauce)",
    "винегрет": "vinaigrette (Russian beet salad with vegetables)",
    "пироги": "pirogi (large Russian pies with various fillings)",
    "стейк": "premium beef steak with rosemary, medium-rare",
    "паста": "Italian pasta with parmesan and fresh basil",
}


def _translate_dish_name(dish: str) -> str:
    """Translate Russian dish names to English with cultural context."""
    dish_lower = dish.lower().strip()
    if dish_lower in RUSSIAN_DISHES_EN:
        return RUSSIAN_DISHES_EN[dish_lower]
    return dish


def build_food_prompt(dish_name: str, style: str = "rustic", extra_details: str = "") -> str:
    """Build a photorealistic food prompt for given dish and style."""
    template = FOOD_TEMPLATES.get(style, FOOD_TEMPLATES["rustic"])
    translated = _translate_dish_name(dish_name)
    parts = [translated]
    if extra_details:
        parts.append(extra_details.strip())
    parts.append(template)
    return ", ".join(parts)


def generate_dish_photo(
    dish_name: str,
    style: str = "rustic",
    extra_details: str = "",
) -> str:
    """Generate a professional food photo. Returns image URL."""
    prompt = build_food_prompt(dish_name, style, extra_details)
    aspect = ASPECT_RATIOS.get(style, "1:1")
    urls = generate_images_replicate(prompt, num_images=1, aspect_ratio=aspect, style="realistic")
    return urls[0] if urls else ""


def generate_menu_series(
    dishes: List[str],
    style: str = "rustic",
) -> List[Dict]:
    """Generate a matching series of food photos for multiple dishes."""
    results = []
    for dish in dishes:
        url = generate_dish_photo(dish, style=style)
        results.append({"dish": dish, "photo_url": url, "style": style})
    return results


def generate_social_post(dish_name: str, language: str = "ru") -> Dict:
    """Generate full Instagram post: photo + caption + hashtags."""
    photo_url = generate_dish_photo(dish_name, style="instagram")

    if language == "ru":
        caption_prompt = (
            f'Создай короткий привлекательный caption для Instagram поста ресторана '
            f'о блюде "{dish_name}". '
            f"Включи: 2-3 предложения, эмодзи, призыв посетить. "
            f"Не более 150 символов основного текста + хештеги отдельной строкой."
        )
    else:
        caption_prompt = (
            f'Write a short attractive Instagram caption for a restaurant post '
            f'about "{dish_name}". '
            f"Include: 2-3 sentences, emojis, call to visit. "
            f"Max 150 chars + hashtags on a separate line."
        )

    caption = call_claude(caption_prompt) or f"🍽 {dish_name} — попробуйте сегодня!"

    return {
        "photo_url": photo_url,
        "caption": caption,
        "dish": dish_name,
        "best_post_time": SOCIAL_POST_TIMES.get("instagram", "19:00"),
        "platforms": ["instagram", "telegram"],
    }


def list_styles() -> List[Dict]:
    """Return all available food photo styles with descriptions."""
    descriptions = {
        "rustic": "Тёплый деревенский стиль — дерево, мягкий свет",
        "modern": "Современный минимализм — белый фон, вид сверху",
        "dark": "Тёмный мудборд — драматическое освещение",
        "instagram": "Яркий Instagram-стиль — для максимального охвата",
    }
    return [
        {"style": s, "description": descriptions.get(s, s), "aspect_ratio": ASPECT_RATIOS.get(s, "1:1")}
        for s in FOOD_TEMPLATES
    ]
