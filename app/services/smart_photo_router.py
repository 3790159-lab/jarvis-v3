"""Smart Photo Router — auto-detect pipeline from user request."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.replicate_image_gen import generate_images_replicate
from app.services.face_swap import enhance_face, face_swap_with_polish
from app.services.personal_mode import generate_me_as


# ── keyword sets ──────────────────────────────────────────────────────────────

_FOOD_KW: List[str] = [
    # Russian stems — short enough to match all inflections
    "блюд", "ед",  "ресторан", "меню", "борщ", "коктейл",
    "суши", "пицц", "шашлык", "стейк", "салат", "суп", "торт", "пирог", "блин",
    "food", "dish", "menu", "meal", "restaurant", "cook", "recipe", "dessert", "appetizer",
]

_PERSONAL_KW: List[str] = [
    " я ", "меня", " мой ", " моя ", " мне ", " себя", "сделай меня",
    "me as", "me in", "me with", "me into", "my photo",
    "myself as", "myself in",
]

_PARTY_KW: List[str] = [
    "вечеринк", "party", "event", "prom", "пригласить", "invite", "invitation",
    "postер", "poster", "promo", "промо", "корпоратив", "halloween", "хэллоуин",
    "birthday", "день рождения", "nye", "новый год", "wedding", "свадьба",
]

_FACESWAP_KW: List[str] = [
    "face swap", "фейсвап", "поменяй лицо", "вставь лицо", "своё лицо",
    "swap face", "replace face",
]

_ENHANCE_KW: List[str] = [
    "улучши", "enhance", "upscale", "retouch", "retouching", "quality",
    "sharpen", "restore face",
]


def _contains_any(text: str, keywords: List[str]) -> bool:
    """Check if any keyword appears as a substring in text (case-insensitive, stem matching)."""
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def analyze_photo_request(
    query: str,
    has_image: bool = False,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyse a natural-language photo request and return the best pipeline.

    Returns dict with keys:
      pipeline  – one of: restaurant | party | personal | face_swap | enhance | edit | general
      model     – recommended model name
      cost      – estimated cost in USD
      reason    – short human-readable explanation
      needs_lora – True if pipeline requires a trained LoRA
    """
    q = query.lower()

    # 1. Face-swap explicit request
    if _contains_any(q, _FACESWAP_KW):
        return {
            "pipeline": "face_swap",
            "model": "face_swap_basic",
            "cost": 0.005,
            "reason": "Detected face swap request",
            "needs_lora": False,
        }

    # 2. Enhancement of existing image
    if has_image and _contains_any(q, _ENHANCE_KW):
        return {
            "pipeline": "enhance",
            "model": "gfpgan",
            "cost": 0.002,
            "reason": "Image enhancement requested",
            "needs_lora": False,
        }

    # 3. Food / restaurant
    if _contains_any(q, _FOOD_KW):
        return {
            "pipeline": "restaurant",
            "model": "flux_pro",
            "cost": 0.04,
            "reason": "Detected food/restaurant keywords",
            "needs_lora": False,
        }

    # 4. Party / event
    if _contains_any(q, _PARTY_KW):
        return {
            "pipeline": "party",
            "model": "flux_pro_ultra",
            "cost": 0.06,
            "reason": "Detected party/event keywords",
            "needs_lora": False,
        }

    # 5. Personal generation (needs LoRA)
    if _contains_any(q, _PERSONAL_KW):
        has_lora = False
        if user_id:
            try:
                from app.services.lora_manager import list_loras
                loras = list_loras(user_id)
                has_lora = any(l.get("status") == "succeeded" for l in loras)
            except Exception:
                pass
        return {
            "pipeline": "personal",
            "model": "flux_lora" if has_lora else "flux_pro_ultra",
            "cost": 0.03 if has_lora else 0.06,
            "reason": "Personal generation detected",
            "needs_lora": not has_lora,
        }

    # 6. Image-to-image (has attachment, no specific keyword)
    if has_image:
        return {
            "pipeline": "edit",
            "model": "img2img",
            "cost": 0.025,
            "reason": "Image provided — using img2img",
            "needs_lora": False,
        }

    # 7. Default: general generation
    return {
        "pipeline": "general",
        "model": "flux_pro_ultra",
        "cost": 0.06,
        "reason": "General image generation",
        "needs_lora": False,
    }


def format_pipeline_suggestion(analysis: Dict[str, Any]) -> str:
    """Format pipeline analysis into user-friendly Telegram message."""
    pipeline_names = {
        "restaurant": "🍽 Restaurant Photo",
        "party": "🎉 Party Promo",
        "personal": "🧑 Personal (LoRA)",
        "face_swap": "🔄 Face Swap",
        "enhance": "✨ Face Enhance",
        "edit": "🖼 Image Edit",
        "general": "🎨 General Generation",
    }
    name = pipeline_names.get(analysis["pipeline"], analysis["pipeline"])
    cost = analysis["cost"]
    reason = analysis.get("reason", "")

    lines = [
        f"**Рекомендую:** {name}",
        f"💰 Стоимость: ~${cost:.3f}",
        f"📋 {reason}",
    ]
    if analysis.get("needs_lora"):
        lines.append("⚠️ Требуется обученная LoRA — используй /lora_train")

    lines.append("\n[✅ Подтвердить] [🔄 Изменить] [❌ Отмена]")
    return "\n".join(lines)


# ── Composite workflows ───────────────────────────────────────────────────────

def composite_lora_plus_swap(
    user_id: str,
    role: str,
    reference_face_url: str,
) -> str:
    """
    High-quality composite pipeline:
    1. Generate via LoRA (user as role)
    2. Face swap with reference photo for accuracy
    3. GFPGAN polish
    """
    lora_result = generate_me_as(user_id, role)
    final = face_swap_with_polish(reference_face_url, lora_result)
    return final


def composite_generate_and_enhance(prompt: str, aspect_ratio: str = "1:1") -> str:
    """
    Generate image then enhance faces:
    1. FLUX generation
    2. GFPGAN polish
    """
    urls = generate_images_replicate(prompt, num_images=1, aspect_ratio=aspect_ratio)
    if not urls:
        return ""
    return enhance_face(urls[0])


def estimate_composite_cost(workflow: str) -> float:
    """Estimate total cost for composite workflows."""
    costs: Dict[str, float] = {
        "lora_plus_swap": 0.03 + 0.005 + 0.002,  # lora + swap + gfpgan
        "generate_and_enhance": 0.04 + 0.002,     # flux_pro + gfpgan
        "generate_ultra_enhance": 0.06 + 0.002,   # flux_pro_ultra + gfpgan
    }
    return round(costs.get(workflow, 0.0), 4)
