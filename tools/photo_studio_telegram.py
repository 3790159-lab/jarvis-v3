"""Photo Studio Telegram handlers — Block H4.
Phases H4.1–H4.6: Restaurant, Party, Face Swap, LoRA, Personal, Smart Router.

All handlers accept (chat_id, send_fn, send_photo_fn) to avoid circular imports.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── path helpers ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_CONV_DIR = _ROOT / "state" / "conversations"


def _add_root() -> None:
    r = str(_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


# ── conversation state ────────────────────────────────────────────────────────

def _conv_path(chat_id: str) -> Path:
    _CONV_DIR.mkdir(parents=True, exist_ok=True)
    return _CONV_DIR / f"{chat_id}.json"


def load_conv(chat_id: str) -> Dict[str, Any]:
    p = _conv_path(chat_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_conv(chat_id: str, data: Dict[str, Any]) -> None:
    _conv_path(chat_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_conv(chat_id: str) -> None:
    p = _conv_path(chat_id)
    if p.exists():
        p.unlink()


# ── inline keyboard builders ──────────────────────────────────────────────────

def _kb(rows: List[List[Dict]]) -> Dict:
    return {"inline_keyboard": rows}


def _social_post_keyboard(dish: str) -> Dict:
    return _kb([
        [
            {"text": "📋 В Obsidian", "callback_data": f"sp:obsidian:{dish[:40]}"},
            {"text": "📤 В Instagram via n8n", "callback_data": f"sp:instagram:{dish[:40]}"},
            {"text": "🔄 Перегенерировать", "callback_data": f"sp:regen:{dish[:40]}"},
        ]
    ])


def _confirm_faceswap_keyboard() -> Dict:
    # Single tier until GPEN lands — two identical tiers would mislead the user.
    # The dormant "polish" branch stays in handle_faceswap_callback for that step.
    return _kb([
        [{"text": "🔄 Сделать swap (~$0.005)", "callback_data": "fs:exec:basic"}],
        [{"text": "❌ Отмена", "callback_data": "fs:cancel"}],
    ])


def _lora_confirm_keyboard() -> Dict:
    return _kb([
        [
            {"text": "✅ Да, запустить обучение", "callback_data": "lora:confirm"},
            {"text": "❌ Отмена", "callback_data": "lora:cancel"},
        ]
    ])


def _photo_router_keyboard(pipeline: str) -> Dict:
    return _kb([
        [
            {"text": "✅ Подтвердить", "callback_data": f"pr:confirm:{pipeline}"},
            {"text": "🔄 Изменить", "callback_data": "pr:change"},
            {"text": "❌ Отмена", "callback_data": "pr:cancel"},
        ]
    ])


# ── H4.1 Restaurant ────────────────────────────────────────────────────────────

def handle_menu_photo(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /menu_photo <блюдо> [--style <стиль>]"""
    _add_root()
    if not query:
        send_fn(chat_id, (
            "Использование: /menu_photo <блюдо> [--style rustic|modern|dark|instagram]\n\n"
            "Пример: /menu_photo борщ --style dark"
        ))
        return

    style = "rustic"
    dish = query
    if "--style" in query:
        parts = query.split("--style")
        dish = parts[0].strip()
        style = parts[1].strip().split()[0].lower()

    send_fn(chat_id, f"📸 Генерирую фото «{dish}» в стиле {style}...")
    try:
        from app.services.restaurant_mode import generate_dish_photo
        photo_url = generate_dish_photo(dish, style)
        if not photo_url:
            send_fn(chat_id, "❌ Не удалось сгенерировать фото")
            return
        send_photo_fn(chat_id, photo_url, caption=dish)
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка генерации: {exc}")


def handle_social_post(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /social_post <блюдо>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /social_post <блюдо>\nПример: /social_post тирамису")
        return

    dish = query.strip()
    send_fn(chat_id, f"📱 Создаю Instagram-пост для «{dish}»...")
    try:
        from app.services.restaurant_mode import generate_social_post
        result = generate_social_post(dish)
        url = result.get("url") or result.get("image_url")
        caption = result.get("caption", dish)
        hashtags = result.get("hashtags", "")
        full_caption = f"{caption}\n\n{hashtags}" if hashtags else caption
        if url:
            from tools.jarvis_smart_telegram_control import tg_call
            tg_call("sendPhoto", {
                "chat_id": chat_id,
                "photo": url,
                "caption": full_caption[:1024],
                "reply_markup": _social_post_keyboard(dish),
            })
        else:
            send_fn(chat_id, f"❌ Не удалось получить фото: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_menu_book(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /menu_book <блюдо1, блюдо2, ...>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /menu_book борщ, стейк, тирамису")
        return

    dishes = [d.strip() for d in query.split(",") if d.strip()]
    if not dishes:
        send_fn(chat_id, "Укажи блюда через запятую.")
        return

    send_fn(chat_id, f"📚 Создаю серию фото для {len(dishes)} блюд...")
    try:
        from app.services.restaurant_mode import generate_menu_series
        results = generate_menu_series(dishes)
        ok = 0
        for r in results:
            url = r.get("url") or r.get("image_url")
            dish = r.get("dish", "")
            if url:
                send_photo_fn(chat_id, url, caption=dish)
                ok += 1
        send_fn(chat_id, f"✅ Готово: {ok}/{len(dishes)} фото создано.")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_dish_styles(chat_id: str, send_fn: Callable) -> None:
    """Handle /dish_styles"""
    _add_root()
    try:
        from app.services.restaurant_mode import list_styles, FOOD_TEMPLATES
        styles = list_styles()
        lines = ["🎨 Стили фото блюд:\n"]
        descriptions = {
            "rustic": "Тёплый фон из дерева, натуральный свет, Canon 5D",
            "modern": "Белый мрамор, вид сверху, Michelin-star",
            "dark": "Драматичный moody, чёрный сланец, пар",
            "instagram": "Яркие цвета, неглубокая глубина резкости",
        }
        for s in styles:
            lines.append(f"• <b>{s}</b> — {descriptions.get(s, '')}")
        lines.append("\nИспользование: /menu_photo борщ --style dark")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


# ── H4.2 Party ────────────────────────────────────────────────────────────────

def handle_party_promo(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /party_promo <тема> [текст]"""
    _add_root()
    if not query:
        send_fn(chat_id, (
            "Использование: /party_promo <тема> [текст]\n\n"
            "Темы: nye, halloween, birthday, wedding, summer, corporate, masquerade, pool\n"
            "Пример: /party_promo halloween"
        ))
        return

    parts = query.split(None, 1)
    theme = parts[0].lower()
    extra_text = parts[1] if len(parts) > 1 else ""

    send_fn(chat_id, f"🎉 Создаю постер для {theme}...")
    try:
        from app.services.party_mode import generate_party_promo
        result = generate_party_promo(theme, extra_text or "")
        poster_url = result.get("poster_url", "").strip()
        promo_text = result.get("promo_text", "").strip()
        if not poster_url:
            send_fn(chat_id, "❌ Не удалось сгенерировать постер")
            return
        send_photo_fn(chat_id, poster_url, caption=promo_text[:1024] if promo_text else theme)
        if len(promo_text) > 1024:
            send_fn(chat_id, "📝 Полный текст:\n\n" + promo_text)
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_invite_card(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /invite_card <имя> <событие> <дата>"""
    _add_root()
    if not query:
        send_fn(chat_id, (
            'Использование: /invite_card <имя> "<событие>" "<дата>"\n\n'
            'Пример: /invite_card Иван "День рождения" "5 мая 2026"'
        ))
        return

    import shlex
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()

    if len(tokens) < 3:
        send_fn(chat_id, 'Нужны 3 параметра: имя, событие, дата.\nПример: /invite_card Иван "День рождения" "5 мая"')
        return

    name = tokens[0]
    event = tokens[1]
    date_str = tokens[2]

    send_fn(chat_id, f"💌 Создаю приглашение для {name}...")
    try:
        from app.services.party_mode import generate_invite_card
        result = generate_invite_card(name, event, date_str)

        card_url = result.get("card_url", "").strip()
        personal_text = result.get("personal_text", "").strip()

        if not card_url:
            send_fn(chat_id, "❌ Не удалось сгенерировать приглашение")
            return

        send_photo_fn(chat_id, card_url, caption=personal_text[:1024])

        if len(personal_text) > 1024:
            send_fn(chat_id, "📝 Полный текст:\n\n" + personal_text)
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_event_photo(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /event_photo <описание>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /event_photo банкетный зал с хрустальными люстрами")
        return

    send_fn(chat_id, f"📸 Создаю фото события...")
    try:
        from app.services.party_mode import generate_event_photo
        result = generate_event_photo(query)
        url = result.get("url") or result.get("image_url")
        if url:
            send_photo_fn(chat_id, url, caption=query[:200])
        else:
            send_fn(chat_id, f"❌ Не удалось создать фото: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_party_themes(chat_id: str, send_fn: Callable) -> None:
    """Handle /party_themes"""
    _add_root()
    try:
        from app.services.party_mode import PARTY_THEMES
        lines = ["🎭 Доступные темы вечеринок:\n"]
        icons = {
            "nye": "🎆", "halloween": "🎃", "birthday": "🎂",
            "wedding": "💒", "summer": "☀️", "corporate": "💼",
            "masquerade": "🎭", "pool": "🏊",
        }
        for theme, desc in PARTY_THEMES.items():
            icon = icons.get(theme, "🎉")
            lines.append(f"{icon} <b>{theme}</b>")
        lines.append("\nИспользование: /party_promo halloween")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


# ── H4.3 Face Swap ────────────────────────────────────────────────────────────

def handle_faceswap_start(chat_id: str, send_fn: Callable) -> None:
    """Step 1 of /faceswap — ask for source photo."""
    save_conv(chat_id, {"step": "faceswap_source", "data": {}})
    send_fn(chat_id, (
        "🔄 Face Swap — Шаг 1/3\n\n"
        "Загрузи source фото (твоё лицо).\n"
        "Это фото, чьё лицо будет вставлено."
    ))


def handle_enhance_start(chat_id: str, send_fn: Callable) -> None:
    """Start /enhance — ask for photo."""
    save_conv(chat_id, {"step": "enhance_upload", "data": {}})
    send_fn(chat_id, (
        "✨ Face Enhance — GFPGAN\n\n"
        "Загрузи фото, которое нужно улучшить.\n"
        "Стоимость: $0.002"
    ))


def handle_me_into_start(chat_id: str, send_fn: Callable) -> None:
    """Start /me_into — ask for target photo."""
    save_conv(chat_id, {"step": "meinto_target", "data": {}})
    send_fn(chat_id, (
        "🔄 Me Into — вставлю твоё лицо в это фото\n\n"
        "Загрузи target фото (куда вставить лицо).\n"
        "Буду использовать твоё последнее загруженное лицо."
    ))


def handle_faceswap_photo_step(
    chat_id: str,
    photo_url: str,
    send_fn: Callable,
    send_photo_fn: Callable,
) -> bool:
    """Called when a photo arrives and conversation state is active.
    Returns True if this photo was consumed by a multi-step flow."""
    _add_root()
    conv = load_conv(chat_id)
    step = conv.get("step")

    if step == "faceswap_source":
        conv["data"]["source_url"] = photo_url
        conv["step"] = "faceswap_target"
        save_conv(chat_id, conv)
        send_fn(chat_id, (
            "✅ Source фото получено!\n\n"
            "🔄 Face Swap — Шаг 2/3\n"
            "Теперь загрузи target фото (куда вставить лицо)."
        ))
        return True

    if step == "faceswap_target":
        conv["data"]["target_url"] = photo_url
        conv["step"] = "faceswap_confirm"
        save_conv(chat_id, conv)
        from tools.jarvis_smart_telegram_control import tg_call
        tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": (
                "✅ Target фото получено!\n\n"
                "🔄 Face Swap — Шаг 3/3\n"
                "Выбери качество:"
            ),
            "reply_markup": _confirm_faceswap_keyboard(),
        })
        return True

    if step == "enhance_upload":
        clear_conv(chat_id)
        send_fn(chat_id, "✨ Улучшаю фото через GFPGAN...")
        try:
            from app.services.face_swap import enhance_face
            result_url = enhance_face(photo_url)
            send_photo_fn(chat_id, result_url, caption="✨ Улучшенное фото")
        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка GFPGAN: {exc}")
        return True

    if step == "meinto_target":
        clear_conv(chat_id)
        # Use last known source face from lora training or prior faceswap
        # For now we need a source — ask them to use /faceswap instead
        send_fn(chat_id, "⏳ Вставляю твоё лицо в target фото...")
        try:
            # Try to get saved source from state/my_face.txt
            face_path = _ROOT / "state" / "my_face_url.txt"
            if face_path.exists():
                source_url = face_path.read_text(encoding="utf-8").strip()
                from app.services.face_swap import face_swap_basic
                result_url = face_swap_basic(source_url, photo_url)
                send_photo_fn(chat_id, result_url, caption="🔄 Твоё лицо вставлено")
            else:
                send_fn(chat_id, (
                    "❌ Нет сохранённого лица.\n"
                    "Используй /faceswap и загрузи своё лицо как source.\n"
                    "Оно сохранится для будущих /me_into."
                ))
        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка: {exc}")
        return True

    if step == "lora_collecting":
        photos = conv.get("data", {}).get("photos", [])
        photos.append(photo_url)
        conv["data"]["photos"] = photos
        save_conv(chat_id, conv)
        send_fn(chat_id, (
            f"✅ Фото {len(photos)} получено. "
            f"{'Можно продолжать загружать или напиши /lora_done' if len(photos) < 15 else 'Рекомендуемое количество достигнуто! /lora_done — завершить'}"
        ))
        return True

    return False


def handle_faceswap_callback(
    chat_id: str, data: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> bool:
    """Handle face swap callback buttons. Returns True if handled."""
    _add_root()
    if not data.startswith("fs:"):
        return False

    conv = load_conv(chat_id)

    if data == "fs:cancel":
        clear_conv(chat_id)
        send_fn(chat_id, "❌ Face swap отменён.")
        return True

    if data.startswith("fs:exec:"):
        quality = data.split(":")[-1]  # "basic" or "polish"
        source = conv.get("data", {}).get("source_url", "")
        target = conv.get("data", {}).get("target_url", "")
        clear_conv(chat_id)

        if not source or not target:
            send_fn(chat_id, "❌ Не найдены фото. Начни заново с /faceswap")
            return True

        cost = "$0.005"  # lucataco/faceswap stopgap (uncensored); ComfyUI graph in reserve
        send_fn(chat_id, f"🔄 Запускаю face swap ({cost})...")
        try:
            if quality == "basic":
                from app.services.face_swap import face_swap_basic
                result_url = face_swap_basic(source, target)
            else:
                from app.services.face_swap import face_swap_with_polish
                result_url = face_swap_with_polish(source, target)

            send_photo_fn(chat_id, result_url, caption="🔄 Face Swap готов!")

            # Save source as "my face" for future /me_into
            face_path = _ROOT / "state" / "my_face_url.txt"
            face_path.write_text(source, encoding="utf-8")

        except Exception as exc:
            from app.services.face_swap import NSFWFiltered
            if isinstance(exc, NSFWFiltered):
                send_fn(chat_id, (
                    "⚠️ Фото отклонено NSFW-фильтром модели "
                    "(он бывает ложно срабатывает на обычных фото). "
                    "Попробуй другое фото."
                ))
            else:
                send_fn(chat_id, f"❌ Ошибка face swap: {exc}")
        return True

    return False


# ── H4.4 LoRA Training ─────────────────────────────────────────────────────────

def handle_lora_train_start(chat_id: str, send_fn: Callable) -> None:
    """Start /lora_train — begin collecting photos."""
    save_conv(chat_id, {"step": "lora_collecting", "data": {"photos": []}})
    send_fn(chat_id, (
        "🎓 Обучение LoRA — Шаг 1\n\n"
        "Загружай свои фото (15-20 штук, можно по одной).\n"
        "Когда закончишь — напиши /lora_done\n\n"
        "📋 Требования:\n"
        "• Чёткое лицо\n"
        "• Разные ракурсы и освещение\n"
        "• Минимум 10 фото"
    ))


def handle_lora_done(chat_id: str, send_fn: Callable) -> None:
    """Handle /lora_done — finish photo collection, ask for model name."""
    conv = load_conv(chat_id)
    if conv.get("step") != "lora_collecting":
        send_fn(chat_id, "Нет активной загрузки LoRA. Начни с /lora_train")
        return

    photos = conv.get("data", {}).get("photos", [])
    if len(photos) < 10:
        send_fn(chat_id, f"❌ Загружено только {len(photos)} фото. Нужно минимум 10.")
        return

    conv["step"] = "lora_name"
    save_conv(chat_id, conv)
    send_fn(chat_id, (
        f"✅ Получено {len(photos)} фото!\n\n"
        "🎓 Обучение LoRA — Шаг 2\n"
        "Как назвать модель? (например: Daniil)"
    ))


def handle_lora_name_text(chat_id: str, text: str, send_fn: Callable) -> bool:
    """Handle free text when waiting for LoRA name. Returns True if consumed."""
    conv = load_conv(chat_id)
    if conv.get("step") != "lora_name":
        return False
    name = text.strip()
    if not name or len(name) > 50:
        send_fn(chat_id, "Введи имя модели (до 50 символов).")
        return True
    conv["data"]["name"] = name
    conv["step"] = "lora_trigger"
    save_conv(chat_id, conv)
    send_fn(chat_id, (
        f"✅ Имя: {name}\n\n"
        "🎓 Обучение LoRA — Шаг 3\n"
        f"Trigger word? (по умолчанию: {name.upper()})\n"
        "Напиши слово или /skip для дефолтного."
    ))
    return True


def handle_lora_trigger_text(chat_id: str, text: str, send_fn: Callable) -> bool:
    """Handle free text when waiting for trigger word. Returns True if consumed."""
    conv = load_conv(chat_id)
    if conv.get("step") != "lora_trigger":
        return False
    trigger = text.strip()
    if text.strip().lower() == "/skip":
        trigger = conv["data"].get("name", "PERSON").upper()
    conv["data"]["trigger_word"] = trigger
    conv["step"] = "lora_confirm"
    photos_count = len(conv["data"].get("photos", []))
    save_conv(chat_id, conv)
    from tools.jarvis_smart_telegram_control import tg_call
    tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"🎓 Обучение LoRA — Шаг 4 (последний)\n\n"
            f"📋 Параметры:\n"
            f"• Имя: {conv['data']['name']}\n"
            f"• Trigger: {trigger}\n"
            f"• Фото: {photos_count}\n"
            f"• Стоимость: $10.00\n\n"
            "Подтвердить запуск обучения?"
        ),
        "reply_markup": _lora_confirm_keyboard(),
    })
    return True


def handle_lora_callback(
    chat_id: str, data: str,
    send_fn: Callable,
) -> bool:
    """Handle LoRA callback buttons. Returns True if handled."""
    _add_root()
    if not data.startswith("lora:"):
        return False

    conv = load_conv(chat_id)

    if data == "lora:cancel":
        clear_conv(chat_id)
        send_fn(chat_id, "❌ Обучение отменено.")
        return True

    if data == "lora:confirm":
        photos = conv.get("data", {}).get("photos", [])
        name = conv.get("data", {}).get("name", "MyModel")
        trigger = conv.get("data", {}).get("trigger_word", name.upper())
        clear_conv(chat_id)

        send_fn(chat_id, f"🚀 Запускаю обучение LoRA «{name}»...")
        try:
            from app.services.lora_manager import start_lora_training
            training = start_lora_training(
                user_id=chat_id,
                name=name,
                trigger_word=trigger,
                image_urls=photos,
            )
            training_id = training.get("training_id") or training.get("id") or "?"
            send_fn(chat_id, (
                f"✅ Обучение запущено!\n"
                f"ID: {training_id[:16]}\n"
                f"Имя: {name}\n"
                f"Примерное время: ~30 минут\n\n"
                f"Уведомлю когда готово. Проверить: /lora_status {name}"
            ))

            # Start background poll
            _start_lora_poll(chat_id, training_id, name, send_fn)

        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка запуска обучения: {exc}")
        return True

    return False


def _start_lora_poll(
    chat_id: str, training_id: str, name: str, send_fn: Callable,
) -> None:
    """Poll LoRA training status in background thread."""
    def _poll() -> None:
        _add_root()
        from app.services.lora_manager import check_lora_status
        max_attempts = 60  # 60 × 60s = 60 minutes
        for _ in range(max_attempts):
            time.sleep(60)
            try:
                status = check_lora_status(chat_id, name)
                state = status.get("status", "")
                if state == "succeeded":
                    send_fn(chat_id, f"🎉 LoRA «{name}» обучена!\n\nТеперь используй:\n/me_as bodybuilder\n/me_in maldives")
                    return
                if state in ("failed", "canceled"):
                    send_fn(chat_id, f"❌ Обучение LoRA «{name}» провалилось: {status.get('error', '?')}")
                    return
            except Exception:
                pass
        send_fn(chat_id, f"⏰ Обучение LoRA «{name}» ещё идёт. Проверь: /lora_status {name}")

    t = threading.Thread(target=_poll, daemon=True)
    t.start()


def handle_lora_list(chat_id: str, send_fn: Callable) -> None:
    """Handle /lora_list"""
    _add_root()
    try:
        from app.services.lora_manager import list_loras
        loras = list_loras(chat_id)
        if not loras:
            send_fn(chat_id, (
                "У тебя нет обученных LoRA моделей.\n\n"
                "Создай с /lora_train ($10 одноразово)"
            ))
            return
        lines = ["🧬 Твои LoRA модели:\n"]
        status_icons = {"succeeded": "✅", "starting": "⏳", "processing": "🔄",
                        "failed": "❌", "canceled": "❌"}
        for l in loras:
            icon = status_icons.get(l.get("status", ""), "❓")
            lines.append(f"{icon} {l['name']} — {l.get('trigger_word', '?')} — {l.get('status', '?')}")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_lora_status(chat_id: str, query: str, send_fn: Callable) -> None:
    """Handle /lora_status <name>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /lora_status <имя>")
        return
    name = query.strip()
    try:
        from app.services.lora_manager import check_lora_status
        status = check_lora_status(chat_id, name)
        if not status:
            send_fn(chat_id, f"LoRA «{name}» не найдена.")
            return
        lines = [
            f"🔍 LoRA «{name}»:",
            f"  Статус: {status.get('status', '?')}",
            f"  ID: {status.get('training_id', '?')[:16]}",
            f"  Trigger: {status.get('trigger_word', '?')}",
        ]
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_lora_delete(chat_id: str, query: str, send_fn: Callable) -> None:
    """Handle /lora_delete <name>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /lora_delete <имя>")
        return
    name = query.strip()
    try:
        from app.services.lora_manager import delete_lora
        ok = delete_lora(chat_id, name)
        if ok:
            send_fn(chat_id, f"✅ LoRA «{name}» удалена.")
        else:
            send_fn(chat_id, f"❌ LoRA «{name}» не найдена.")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


# ── H4.5 Personal Mode ─────────────────────────────────────────────────────────

def _check_lora_available(chat_id: str) -> bool:
    """Check if user has at least one trained LoRA."""
    _add_root()
    try:
        from app.services.lora_manager import list_loras
        loras = list_loras(chat_id)
        return any(l.get("status") == "succeeded" for l in loras)
    except Exception:
        return False


def _lora_needed_msg() -> str:
    return (
        "🔒 Для этой команды нужна обученная LoRA модель.\n\n"
        "Запусти обучение: /lora_train\n"
        "Стоимость: $10 (одноразово)"
    )


def handle_me_as(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /me_as <роль>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /me_as <роль>\nПример: /me_as bodybuilder\n\nСписок: /me_roles")
        return
    if not _check_lora_available(chat_id):
        send_fn(chat_id, _lora_needed_msg())
        return
    role = query.strip().lower()
    send_fn(chat_id, f"🎭 Генерирую тебя как {role}...")
    try:
        from app.services.personal_mode import generate_me_as
        result = generate_me_as(chat_id, role)
        url = result.get("url") or result.get("image_url")
        if url:
            send_photo_fn(chat_id, url, caption=f"Ты как {role}")
        else:
            send_fn(chat_id, f"❌ Не удалось создать изображение: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_in(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /me_in <место>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /me_in <место>\nПример: /me_in maldives\n\nСписок: /me_places")
        return
    if not _check_lora_available(chat_id):
        send_fn(chat_id, _lora_needed_msg())
        return
    place = query.strip().lower()
    send_fn(chat_id, f"🌍 Помещаю тебя в {place}...")
    try:
        from app.services.personal_mode import generate_me_in
        result = generate_me_in(chat_id, place)
        url = result.get("url") or result.get("image_url")
        if url:
            send_photo_fn(chat_id, url, caption=f"Ты в {place}")
        else:
            send_fn(chat_id, f"❌ Не удалось создать: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_with(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /me_with <предмет>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /me_with <предмет>\nПример: /me_with Lambo")
        return
    if not _check_lora_available(chat_id):
        send_fn(chat_id, _lora_needed_msg())
        return
    item = query.strip()
    send_fn(chat_id, f"🤳 Генерирую тебя с {item}...")
    try:
        from app.services.personal_mode import generate_me_in
        result = generate_me_in(chat_id, f"{item} background, with {item}")
        url = result.get("url") or result.get("image_url")
        if url:
            send_photo_fn(chat_id, url, caption=f"Ты с {item}")
        else:
            send_fn(chat_id, f"❌ Не удалось создать: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_style(
    chat_id: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Handle /me_style <стиль>"""
    _add_root()
    if not query:
        send_fn(chat_id, "Использование: /me_style <стиль>\nПример: /me_style cyberpunk\n\nСписок: /me_styles")
        return
    if not _check_lora_available(chat_id):
        send_fn(chat_id, _lora_needed_msg())
        return
    style = query.strip().lower()
    send_fn(chat_id, f"🎨 Создаю тебя в стиле {style}...")
    try:
        from app.services.personal_mode import generate_me_in_style
        result = generate_me_in_style(chat_id, style)
        url = result.get("url") or result.get("image_url")
        if url:
            send_photo_fn(chat_id, url, caption=f"Ты в стиле {style}")
        else:
            send_fn(chat_id, f"❌ Не удалось создать: {result}")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_roles(chat_id: str, send_fn: Callable) -> None:
    """Handle /me_roles"""
    _add_root()
    try:
        from app.services.personal_mode import ROLES_TEMPLATES
        lines = ["🎭 Доступные роли:\n"]
        icons = {
            "bodybuilder": "💪", "businessman": "👔", "chef": "👨‍🍳",
            "model": "🕴", "athlete": "🏃", "scientist": "🔬",
            "rockstar": "🎸", "astronaut": "🚀", "ceo": "💼", "superhero": "🦸",
        }
        for role in ROLES_TEMPLATES:
            lines.append(f"{icons.get(role, '•')} {role}")
        lines.append("\nИспользование: /me_as bodybuilder")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_places(chat_id: str, send_fn: Callable) -> None:
    """Handle /me_places"""
    _add_root()
    try:
        from app.services.personal_mode import PLACES_TEMPLATES
        lines = ["🌍 Доступные места:\n"]
        icons = {
            "maldives": "🏝", "paris": "🗼", "dubai": "🏙",
            "tokyo": "⛩", "mountains": "🏔", "beach": "🏖",
            "casino": "🎰", "yacht": "⛵",
        }
        for place in PLACES_TEMPLATES:
            lines.append(f"{icons.get(place, '•')} {place}")
        lines.append("\nИспользование: /me_in maldives")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


def handle_me_styles(chat_id: str, send_fn: Callable) -> None:
    """Handle /me_styles"""
    _add_root()
    try:
        from app.services.personal_mode import STYLE_TEMPLATES
        lines = ["🎨 Доступные стили:\n"]
        icons = {
            "cyberpunk": "🤖", "vintage": "📷", "oil_painting": "🖼",
            "anime": "🎌", "noir": "🎬", "watercolor": "🎨",
            "pop_art": "🟡", "fantasy": "🧙",
        }
        for style in STYLE_TEMPLATES:
            lines.append(f"{icons.get(style, '•')} {style}")
        lines.append("\nИспользование: /me_style cyberpunk")
        send_fn(chat_id, "\n".join(lines))
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


# ── H4.6 Smart Photo Router ────────────────────────────────────────────────────

def handle_photo_router_text(
    chat_id: str, text: str,
    send_fn: Callable, send_photo_fn: Callable,
    has_image: bool = False,
) -> bool:
    """Auto-detect photo requests from free text. Returns True if handled."""
    _add_root()
    try:
        from app.services.smart_photo_router import analyze_photo_request, format_pipeline_suggestion
        result = analyze_photo_request(text, has_image=has_image)
        if not result.get("is_photo_request"):
            return False

        pipeline = result.get("pipeline", "general")
        suggestion_text = format_pipeline_suggestion(result)

        # Save pending query+pipeline in conv state
        save_conv(chat_id, {
            "step": "photo_router_confirm",
            "data": {"query": text, "pipeline": pipeline, "has_image": has_image},
        })

        from tools.jarvis_smart_telegram_control import tg_call
        tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": suggestion_text,
            "reply_markup": _photo_router_keyboard(pipeline),
        })
        return True
    except Exception:
        return False


def handle_photo_router_callback(
    chat_id: str, data: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> bool:
    """Handle photo router confirm/cancel callbacks. Returns True if handled."""
    if not data.startswith("pr:"):
        return False
    _add_root()

    if data == "pr:cancel":
        clear_conv(chat_id)
        send_fn(chat_id, "❌ Отменено.")
        return True

    if data == "pr:change":
        clear_conv(chat_id)
        send_fn(chat_id, (
            "Выбери команду вручную:\n"
            "• /menu_photo <блюдо> — еда\n"
            "• /party_promo <тема> — вечеринка\n"
            "• /me_as <роль> — личное (нужна LoRA)\n"
            "• /faceswap — подмена лица"
        ))
        return True

    if data.startswith("pr:confirm:"):
        pipeline = data.split(":")[-1]
        conv = load_conv(chat_id)
        query = conv.get("data", {}).get("query", "")
        clear_conv(chat_id)
        _execute_photo_pipeline(chat_id, pipeline, query, send_fn, send_photo_fn)
        return True

    return False


def _execute_photo_pipeline(
    chat_id: str, pipeline: str, query: str,
    send_fn: Callable, send_photo_fn: Callable,
) -> None:
    """Execute confirmed photo pipeline."""
    _add_root()
    send_fn(chat_id, f"🚀 Запускаю {pipeline} pipeline...")

    try:
        if pipeline == "restaurant":
            from app.services.restaurant_mode import generate_dish_photo
            url = generate_dish_photo(query)
        elif pipeline == "party":
            from app.services.party_mode import generate_party_promo
            party_result = generate_party_promo(query)
            url = party_result.get("poster_url", "").strip()
        elif pipeline == "personal":
            if not _check_lora_available(chat_id):
                send_fn(chat_id, _lora_needed_msg())
                return
            from app.services.personal_mode import generate_me_as
            me_result = generate_me_as(chat_id, query)
            url = me_result.get("url") or me_result.get("image_url") if isinstance(me_result, dict) else me_result
        elif pipeline == "face_swap":
            handle_faceswap_start(chat_id, send_fn)
            return
        elif pipeline == "enhance":
            handle_enhance_start(chat_id, send_fn)
            return
        else:
            from app.services.replicate_image_gen import generate_image
            gen_result = generate_image(query)
            url = gen_result.get("url") or gen_result.get("image_url") if isinstance(gen_result, dict) else gen_result

        if url:
            send_photo_fn(chat_id, url, caption=query[:200])
        else:
            send_fn(chat_id, "❌ Не удалось получить URL изображения")
    except Exception as exc:
        send_fn(chat_id, f"❌ Ошибка: {exc}")


# ── Social post callback ───────────────────────────────────────────────────────

def handle_social_post_callback(
    chat_id: str, data: str,
    send_fn: Callable,
) -> bool:
    """Handle social post buttons. Returns True if handled."""
    if not data.startswith("sp:"):
        return False
    _add_root()

    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    dish = parts[2] if len(parts) > 2 else ""

    if action == "obsidian":
        try:
            from tools.jarvis_smart_telegram_control import backend_post
            backend_post("/api/jarvis/tools/obsidian/save", {
                "content": f"# Социальный пост: {dish}\n\n(Сгенерировано Photo Studio)",
                "title": f"Social Post — {dish}",
            }, timeout=30)
            send_fn(chat_id, f"✅ Пост для «{dish}» сохранён в Obsidian.")
        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка Obsidian: {exc}")
        return True

    if action == "instagram":
        try:
            from tools.jarvis_smart_telegram_control import backend_post
            backend_post("/api/jarvis/n8n/trigger", {
                "workflow": "instagram_autopost",
                "dish": dish,
            }, timeout=30)
            send_fn(chat_id, f"📤 Пост для «{dish}» отправлен в n8n для Instagram.")
        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка n8n: {exc}")
        return True

    if action == "regen":
        send_fn(chat_id, f"🔄 Перегенерирую пост для «{dish}»...")
        try:
            from app.services.restaurant_mode import generate_social_post
            from tools.jarvis_smart_telegram_control import tg_call
            result = generate_social_post(dish)
            url = result.get("url") or result.get("image_url")
            caption = result.get("caption", dish)
            hashtags = result.get("hashtags", "")
            full_caption = f"{caption}\n\n{hashtags}" if hashtags else caption
            if url:
                tg_call("sendPhoto", {
                    "chat_id": chat_id,
                    "photo": url,
                    "caption": full_caption[:1024],
                    "reply_markup": _social_post_keyboard(dish),
                })
            else:
                send_fn(chat_id, "❌ Не удалось перегенерировать.")
        except Exception as exc:
            send_fn(chat_id, f"❌ Ошибка: {exc}")
        return True

    return False


# ── Telegram photo URL helper ─────────────────────────────────────────────────

def get_telegram_photo_url(file_id: str, bot_token: str) -> Optional[str]:
    """Get direct URL for a Telegram photo file_id."""
    import urllib.request
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        file_path = (data.get("result") or {}).get("file_path")
        if file_path:
            return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    except Exception:
        pass
    return None
