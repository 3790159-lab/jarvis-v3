# -*- coding: utf-8 -*-
"""Telegram handlers for persona creation and LoRA training commands."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path
from typing import Callable

import os

from app.services.audit import cost_tracker as _user_cost
from app.services.auth.access_control import check_limit


def _env_usd(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _me_swap_est() -> float:
    """Консервативная оценка для пре-гейта me_swap (фактическая стоимость
    пишется в леджер после успеха отдельно)."""
    return _env_usd("ME_SWAP_USD", 0.04)


# Оценки для пре-гейта T6 (записываем ФАКТ на успешном завершении — вариант B).
_CREATE_PERSONA_SEED_COUNT = 20


def _train_lora_est() -> float:
    return _env_usd("TRAIN_LORA_USD", 2.00)


def _create_persona_est() -> float:
    return _env_usd("CREATE_PERSONA_SEED_USD", 0.04) * _CREATE_PERSONA_SEED_COUNT


# Оценки пре-гейта для персона-генераций (money-consolidation, дыра a).
# Факт-стоимость по-прежнему пишется на успехе через _record_user_cost —
# пре-гейт только проверяет лимит ДО платного вызова (без двойного списания).
def _persona_photo_est() -> float:
    return _env_usd("PERSONA_PHOTO_USD", 0.05)


def _persona_redo_est() -> float:
    return _env_usd("PERSONA_REDO_USD", 0.05)


def _persona_batch_est(count: int) -> float:
    return _env_usd("PERSONA_BATCH_USD", 0.05) * count


def _train_me_lora_est() -> float:
    # Подтверждено Daniil: /me_done квотируется ~$5 (не ~$2).
    return _env_usd("TRAIN_ME_LORA_USD", 5.00)
from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.logging_setup import get_logger, setup_block_m_logging
from app.services.block_m_common.persona_storage import PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from app.services.block_m_common.video_queue import VideoQueue
from app.services.block_m1_persona.lora_trainer import LoRATrainer
from app.services.block_m1_persona.persona_creator import PersonaCreator
from app.services.block_m1_persona.persona_dialog import (
    PersonaDialog,
    STATUS_COLLECTING,
    STATUS_CONFIRMING,
    STATUS_GENERATING,
    STATUS_WAITING_SELECTION,
)
from app.services.error_translator import translate_exception

logger = get_logger("persona_handler")


def _record_user_cost(chat_id, amount) -> None:
    """Per-user ledger write (best-effort) so the friend daily-limit gate sees persona spend."""
    try:
        _user_cost.record_cost(chat_id, None, float(amount))
    except Exception as _e:  # noqa: BLE001 - billing must not break generation
        logger.warning("cost: per-user record failed: %s", _e)

_send: Callable | None = None
_send_photo: Callable | None = None
_send_video: Callable | None = None

# chat_id → persona_id awaiting LoRA training confirmation
_lora_pending_confirm: dict[int, str] = {}

# chat_id → None awaiting Me-LoRA training confirmation after /me_done
_me_pending_train_confirm: dict[int, None] = {}

_ENGINE_PREFS_FILE = Path(__file__).resolve().parent.parent.parent / "state" / "video_prefs.json"

_seed_collector = None  # lazy SeedCollector singleton


def _get_seed_collector():
    global _seed_collector
    if _seed_collector is None:
        from app.services.block_m22_fun.seed_collector import SeedCollector
        _seed_collector = SeedCollector()
    return _seed_collector


def init_bot(
    send_fn: Callable,
    send_photo_fn: Callable,
    send_video_fn: Callable | None = None,
) -> None:
    """Inject bot I/O callables.  Must be called before any handler is used.

    Args:
        send_fn: send(chat_id, text) — sends a text message.
        send_photo_fn: send_photo(chat_id, url, caption) — sends a photo by URL.
        send_video_fn: send_video(chat_id, url, caption) — sends a video by URL.
                       Optional; falls back to a text link if not provided.
    """
    global _send, _send_photo, _send_video
    _send = send_fn
    _send_photo = send_photo_fn
    _send_video = send_video_fn


def _safe_send(chat_id: int, text: str) -> None:
    """Send a text message, logging any Telegram API error instead of crashing."""
    try:
        _send(chat_id, text)
    except Exception as exc:
        logger.error("_send failed (chat=%s): %s", chat_id, exc)


def _safe_send_photo(chat_id: int, url: str, caption: str = "") -> None:
    """Send a photo, logging any error instead of crashing."""
    try:
        _send_photo(chat_id, url, caption)
    except Exception as exc:
        logger.error("_send_photo failed (chat=%s url=%.60s): %s", chat_id, url, exc)


def _safe_send_video(chat_id: int, url: str, caption: str = "") -> None:
    """Send a video, logging any error instead of crashing.

    Falls back to a text link if send_video_fn was not injected via init_bot.
    """
    if _send_video is None:
        logger.warning("_send_video not initialised, falling back to text link")
        _safe_send(chat_id, f"Видео готово: {url}\n{caption}")
        return
    try:
        _send_video(chat_id, url, caption)
    except Exception as exc:
        logger.error("_send_video failed (chat=%s url=%.60s): %s", chat_id, url, exc)


def _get_engine_pref(chat_id: int) -> str:
    """Return the stored video engine preference for a chat (default: kling_v21)."""
    try:
        data = json.loads(_ENGINE_PREFS_FILE.read_text(encoding="utf-8"))
        return data.get(str(chat_id), "kling_v21")
    except Exception:
        return "kling_v21"


def _set_engine_pref(chat_id: int, engine: str) -> None:
    """Persist the video engine preference for a chat."""
    try:
        data: dict = {}
        if _ENGINE_PREFS_FILE.exists():
            data = json.loads(_ENGINE_PREFS_FILE.read_text(encoding="utf-8"))
        data[str(chat_id)] = engine
        _ENGINE_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ENGINE_PREFS_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Failed to save engine pref for chat=%s: %s", chat_id, exc)


# ── command handlers ────────────────────────────────────────────────────────

def handle_create_persona(chat_id: int) -> None:
    """Start a new persona creation dialog for the given chat."""
    logger.info("handle_create_persona chat=%s", chat_id)
    existing = PersonaDialog.find_active(str(chat_id))
    if existing:
        _safe_send(
            chat_id,
            "У вас уже есть активная сессия создания персоны.\n"
            "Отправьте /cancel_persona для отмены.",
        )
        return
    dialog = PersonaDialog(user_id=str(chat_id), chat_id=str(chat_id))
    _safe_send(chat_id, "Создание новой AI-персоны.\n\n" + dialog.get_current_question())


def handle_cancel_persona(chat_id: int) -> None:
    """Cancel the active persona dialog for the given chat."""
    logger.info("handle_cancel_persona chat=%s", chat_id)
    dialog = PersonaDialog.find_active(str(chat_id))
    if not dialog:
        _safe_send(chat_id, "Нет активной сессии создания персоны.")
        return
    dialog.cancel()
    _safe_send(chat_id, "Создание персоны отменено.")


# ── FSM answer router ───────────────────────────────────────────────────────

def handle_persona_answer(chat_id: int, text: str) -> bool:
    """Route a free-text message to active dialog or LoRA confirmation if one exists.

    Returns:
        True if the message was consumed, False otherwise.
    """
    # ── Me-LoRA training confirmation (/me_done flow) ──────────────────────────
    if chat_id in _me_pending_train_confirm:
        low = text.strip().lower()
        if low in ("да", "yes", "y", "д", "+"):
            del _me_pending_train_confirm[chat_id]
            _do_train_me_lora(chat_id)
        elif low in ("нет", "no", "n", "н", "-"):
            del _me_pending_train_confirm[chat_id]
            _safe_send(chat_id, "Тренировка Me-Persona отменена.")
        else:
            _safe_send(chat_id, "Пожалуйста, ответьте \"да\" или \"нет\".")
        return True

    # ── LoRA training confirmation ──────────────────────────────────────────────
    if chat_id in _lora_pending_confirm:
        persona_id = _lora_pending_confirm[chat_id]
        low = text.strip().lower()
        if low in ("да", "yes", "y", "д", "+"):
            del _lora_pending_confirm[chat_id]
            _do_train_lora(chat_id, persona_id)
        elif low in ("нет", "no", "n", "н", "-"):
            del _lora_pending_confirm[chat_id]
            _safe_send(chat_id, "Тренировка отменена.")
        else:
            _safe_send(chat_id, "Пожалуйста, ответьте \"да\" или \"нет\".")
        return True

    dialog = PersonaDialog.find_active(str(chat_id))
    if not dialog:
        return False

    status = dialog.status
    logger.debug("handle_persona_answer chat=%s status=%s text=%.40r", chat_id, status, text)

    if status == STATUS_COLLECTING:
        done, next_q = dialog.advance(text)
        _safe_send(chat_id, next_q)
        return True

    if status == STATUS_CONFIRMING:
        low = text.strip().lower()
        if low in ("да", "yes", "y", "д", "+"):
            _start_generation(chat_id, dialog)
        else:
            dialog.cancel()
            _safe_send(chat_id, "Создание персоны отменено.")
        return True

    if status == STATUS_WAITING_SELECTION:
        try:
            idx = int(text.strip())
        except ValueError:
            _safe_send(chat_id, f"Введите номер фото (1–{len(dialog.photo_urls)}).")
            return True
        url = dialog.select_photo(idx)
        if url is None:
            _safe_send(
                chat_id,
                f"Неверный номер. Введите число от 1 до {len(dialog.photo_urls)}.",
            )
            return True
        _safe_send_photo(chat_id, url, caption="Выбранное фото сохранено!")
        _safe_send(chat_id, f"Персона «{dialog.name}» создана. ID: {dialog.persona_id}")
        return True

    if status == STATUS_GENERATING:
        _safe_send(chat_id, "Идёт генерация фотографий, подождите...")
        return True

    return False


# ── background generation ───────────────────────────────────────────────────

def _start_generation(chat_id: int, dialog: PersonaDialog) -> None:
    """Transition dialog to GENERATING and launch a daemon background thread."""
    # Пре-гейт дневного лимита СТРОГО до старта платной seed-генерации (T6).
    _est = _create_persona_est()
    allowed, reason = check_limit(chat_id, estimated_usd=_est)
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return
    # Резерв оценки на старте (hole c); на успехе — дельта (факт−оценка).
    _record_user_cost(chat_id, _est)
    dialog.confirm()
    _safe_send(
        chat_id,
        "Начинаю генерацию 20 фотографий персоны...\n"
        "Это займёт несколько минут.",
    )
    logger.info("Starting background generation thread for chat=%s", chat_id)

    def _run() -> None:
        # Ensure the file logger is active inside the new thread's context
        setup_block_m_logging()
        logger.info("Background generation thread started chat=%s", chat_id)

        async def _async_run() -> list[str]:
            storage = PersonaStorage()
            persona = await storage.create_persona(
                dialog.name, dialog.description, dialog.style
            )
            dialog.set_persona_id(persona.persona_id)
            logger.info(
                "Persona created in storage: id=%s name=%s",
                persona.persona_id, persona.name,
            )

            client = ReplicateVideoClient()
            tracker = CostTracker()
            creator = PersonaCreator(client, storage, tracker)

            def progress_cb(done: int, total: int, url: str) -> None:
                logger.debug("Progress %d/%d url=%.60s", done, total, url)
                if done % 5 == 0:
                    _safe_send(chat_id, f"Генерация: {done}/{total} фото готово...")

            return await creator.generate_seed_photos(
                persona, count=20, progress_cb=progress_cb,
                # hole c: резерв записан на старте; на успехе — дельта (факт−оценка).
                on_success_cost=lambda amt: _record_user_cost(chat_id, amt - _est),
            )

        try:
            urls = asyncio.run(_async_run())
            logger.info(
                "Generation finished chat=%s total=%d", chat_id, len(urls)
            )
            dialog.set_generation_complete(urls)
            _safe_send(
                chat_id,
                f"Готово! Получено {len(urls)} фото.\n"
                f"Выберите лучшее — введите номер (1–{len(urls)}):",
            )
            for i, url in enumerate(urls, 1):
                _safe_send_photo(chat_id, url, caption=f"Фото #{i}")
        except DailyLimitExceeded as exc:
            logger.warning("DailyLimitExceeded during generation chat=%s: %s", chat_id, exc)
            dialog.cancel()
            _safe_send(chat_id, f"Превышен дневной лимит расходов. {translate_exception(exc)}")
        except Exception as exc:
            logger.error(
                "Persona generation failed chat=%s: %s: %s",
                chat_id, type(exc).__name__, exc, exc_info=True,
            )
            dialog.cancel()
            _safe_send(chat_id, "Ошибка генерации. Попробуйте позже.")

        logger.info("Background generation thread finished chat=%s", chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ── LoRA training helpers ────────────────────────────────────────────────────

def _run_async(coro):
    """Execute a coroutine synchronously via a dedicated thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result(timeout=30)


def _do_train_lora(chat_id: int, persona_id: str) -> None:
    """Start LoRA training and notify chat_id on completion."""
    # Пре-гейт дневного лимита СТРОГО до старта платной тренировки (T6, ~$2).
    _est = _train_lora_est()
    allowed, reason = check_limit(chat_id, estimated_usd=_est)
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return
    # Резерв оценки в леджер СРАЗУ (hole c): рестарт посреди daemon-тренировки
    # убивает поток до записи факта, поэтому резерв на старте гарантирует
    # видимость траты. На успехе пишем ДЕЛЬТУ (факт−оценка) → итог = факт;
    # при рестарте в леджере остаётся оценка (честно — заряд вероятно случился).
    _record_user_cost(chat_id, _est)
    _safe_send(
        chat_id,
        f"Тренировка запущена!\n"
        f"Я уведомлю вас когда будет готово (~20 минут).\n\n"
        f"Проверить статус: /lora_status {persona_id}",
    )
    logger.info("Launching LoRA training daemon thread: chat=%s persona=%s", chat_id, persona_id)

    def _run() -> None:
        setup_block_m_logging()

        async def _async() -> None:
            storage = PersonaStorage()
            client = ReplicateVideoClient()
            tracker = CostTracker()
            queue = VideoQueue()
            trainer = LoRATrainer(client, storage, tracker, queue)
            await trainer.start_training(
                persona_id,
                user_chat_id=chat_id,
                notify_fn=lambda cid, msg: _safe_send(cid, msg),
                # hole c: резерв уже записан на старте; на успехе пишем ДЕЛЬТУ
                # (факт−оценка), чтобы итог сошёлся к фактической стоимости.
                on_success_cost=lambda amt: _record_user_cost(chat_id, amt - _est),
            )

        try:
            asyncio.run(_async())
        except Exception as exc:
            logger.error("LoRA training launch failed chat=%s: %s", chat_id, exc, exc_info=True)
            _safe_send(chat_id, f"Ошибка запуска тренировки: {translate_exception(exc)}")

    threading.Thread(target=_run, daemon=True).start()


# ── LoRA training command handlers ──────────────────────────────────────────

def handle_train_lora(chat_id: int, args: str) -> None:
    """/train_lora <persona_id> — start LoRA training for a persona."""
    persona_id = args.strip()
    if not persona_id:
        _safe_send(chat_id, "Укажите ID персоны: /train_lora <persona_id>")
        return

    logger.info("handle_train_lora chat=%s persona=%s", chat_id, persona_id)

    try:
        persona = _run_async(PersonaStorage().get_persona(persona_id))
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка при поиске персоны: {translate_exception(exc)}")
        return

    if not persona:
        _safe_send(chat_id, f"Персона {persona_id!r} не найдена.")
        return

    n_photos = len(persona.seed_photos)
    from app.services.block_m1_persona.lora_trainer import MIN_PHOTOS
    if n_photos < MIN_PHOTOS:
        _safe_send(
            chat_id,
            f"Недостаточно seed-фото: {n_photos}/{MIN_PHOTOS} минимум.\n"
            f"Сначала сгенерируйте фото: /create_persona",
        )
        return

    already_trained = bool(persona.lora_weights_url)
    _lora_pending_confirm[chat_id] = persona_id
    _safe_send(
        chat_id,
        f"Персона: {persona.name} ({persona_id})\n"
        f"Seed-фото: {n_photos}\n"
        f"Стоимость: ~$2.00\n"
        f"Время: 15-25 минут\n"
        + (f"ВНИМАНИЕ: LoRA уже натренирована — перетренировать?\n" if already_trained else "")
        + "\nЗапустить тренировку? Ответьте \"да\" / \"нет\"",
    )


def handle_lora_status(chat_id: int, args: str) -> None:
    """/lora_status <persona_id> — show training status."""
    persona_id = args.strip()
    if not persona_id:
        _safe_send(chat_id, "Укажите ID персоны: /lora_status <persona_id>")
        return

    logger.info("handle_lora_status chat=%s persona=%s", chat_id, persona_id)

    try:
        storage = PersonaStorage()
        queue = VideoQueue()
        from app.services.block_m_common.cost_tracker import CostTracker
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        trainer = LoRATrainer(ReplicateVideoClient.__new__(ReplicateVideoClient), storage, CostTracker(), queue)

        async def _check():
            return await trainer.check_status(persona_id)

        status = _run_async(_check())
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка проверки статуса: {translate_exception(exc)}")
        return

    if not status:
        _safe_send(chat_id, f"Нет задачи тренировки для {persona_id!r}.")
        return

    label = {
        "pending": "Ожидает запуска",
        "training": "В процессе (~20 мин)",
        "done": "Готово",
        "failed": "Ошибка",
    }.get(status["status"], status["status"])

    parts = [
        f"Статус: {label}",
        f"Прогресс: {status['progress_pct']}%",
    ]
    if status.get("weights_url"):
        parts.append(f"Weights: {status['weights_url']}")
    if status.get("error"):
        parts.append(f"Ошибка: {status['error']}")

    _safe_send(chat_id, "\n".join(parts))


def handle_list_loras(chat_id: int) -> None:
    """/list_loras — list all personas with completed LoRA training."""
    logger.info("handle_list_loras chat=%s", chat_id)

    try:
        from app.services.block_m_common.cost_tracker import CostTracker
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        storage = PersonaStorage()
        queue = VideoQueue()
        trainer = LoRATrainer(ReplicateVideoClient.__new__(ReplicateVideoClient), storage, CostTracker(), queue)

        trained = _run_async(trainer.list_trained())
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка: {translate_exception(exc)}")
        return

    if not trained:
        _safe_send(chat_id, "Нет натренированных персон.\nЗапустите: /train_lora <persona_id>")
        return

    lines = ["Натренированные персоны:"]
    for i, p in enumerate(trained, 1):
        lines.append(f"{i}. {p['name']} ({p['persona_id']})")
        lines.append(f"   Trigger: {p['trigger_word']}")
    _safe_send(chat_id, "\n".join(lines))


def handle_persona_photo(chat_id: int, args: str) -> None:
    """/persona_photo <persona_id> <prompt> — generate a photo with trained LoRA."""
    parts = args.strip().split(None, 1)
    if not parts or not parts[0]:
        _safe_send(chat_id, "Укажите ID персоны и промпт: /persona_photo <persona_id> <prompt>")
        return

    persona_id = parts[0]
    if len(parts) < 2 or not parts[1].strip():
        _safe_send(chat_id, "Укажите промпт: /persona_photo <persona_id> <prompt>")
        return
    prompt = parts[1].strip()

    # Пре-гейт дневного лимита СТРОГО до старта платной генерации (дыра a).
    allowed, reason = check_limit(chat_id, estimated_usd=_persona_photo_est())
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    logger.info("handle_persona_photo chat=%s persona=%s", chat_id, persona_id)
    _safe_send(chat_id, "Генерирую фото, подождите...")

    def _run() -> None:
        setup_block_m_logging()

        from app.services.block_m1_persona.photo_generator import PhotoGenerator

        async def _async() -> dict:
            storage = PersonaStorage()
            client = ReplicateVideoClient()
            tracker = CostTracker()
            gen = PhotoGenerator(client, storage, tracker)
            return await gen.generate_photo(persona_id, prompt)

        try:
            result = asyncio.run(_async())
        except DailyLimitExceeded as exc:
            _safe_send(chat_id, f"Превышен дневной лимит: {translate_exception(exc)}")
            return
        except ValueError as exc:
            _safe_send(chat_id, str(exc))
            return
        except Exception as exc:
            logger.error(
                "handle_persona_photo failed chat=%s: %s", chat_id, exc, exc_info=True
            )
            _safe_send(chat_id, "Ошибка генерации. Попробуйте позже.")
            return

        _record_user_cost(chat_id, result["cost_usd"])
        caption = (
            f"Готово! Стоимость: ${result['cost_usd']:.4f}\n"
            f"Промпт: {result['full_prompt'][:120]}"
        )
        _safe_send_photo(chat_id, result["image_url"], caption=caption)

    threading.Thread(target=_run, daemon=True).start()


def handle_persona_video(chat_id: int, args: str) -> None:
    """/persona_video <persona_id> <prompt> — generate a photo+video with LoRA."""
    parts = args.strip().split(None, 1)
    if not parts or not parts[0]:
        _safe_send(chat_id, "Укажите ID персоны и промпт: /persona_video <persona_id> <prompt>")
        return
    persona_id = parts[0]
    if len(parts) < 2 or not parts[1].strip():
        _safe_send(chat_id, "Укажите промпт: /persona_video <persona_id> <prompt>")
        return
    prompt = parts[1].strip()

    engine = _get_engine_pref(chat_id)
    logger.info("handle_persona_video chat=%s persona=%s engine=%s", chat_id, persona_id, engine)
    _safe_send(chat_id, "Генерирую видео...")

    def _run() -> None:
        setup_block_m_logging()

        from app.services.block_m1_persona.photo_generator import PhotoGenerator
        from app.services.block_m2_video.generation_history import GenerationHistory
        from app.services.block_m2_video.video_client_extras import VideoClientExtras
        from app.services.block_m2_video.video_generator import VideoGenerator
        from app.services.block_m2_video.video_storage import VideoStorage

        def _progress(status: str) -> None:
            if status.startswith("photo_ready:"):
                url = status[len("photo_ready:"):]
                _safe_send_photo(chat_id, url, caption="Фото готово! Анимирую...")
            else:
                _safe_send(chat_id, status)

        async def _async() -> dict:
            storage = PersonaStorage()
            client = ReplicateVideoClient()
            tracker = CostTracker()
            photo_gen = PhotoGenerator(client, storage, tracker)
            video_extras = VideoClientExtras(client)
            video_storage = VideoStorage()
            history = GenerationHistory()
            gen = VideoGenerator(photo_gen, video_extras, video_storage, history, tracker)
            return await gen.generate_video(
                persona_id, prompt, engine=engine, progress_cb=_progress
            )

        try:
            result = asyncio.run(_async())
        except DailyLimitExceeded as exc:
            _safe_send(chat_id, f"Превышен дневной лимит: {translate_exception(exc)}")
            return
        except ValueError as exc:
            _safe_send(chat_id, str(exc))
            return
        except Exception as exc:
            logger.error(
                "handle_persona_video failed chat=%s: %s", chat_id, exc, exc_info=True
            )
            _safe_send(chat_id, "Ошибка генерации видео. Попробуйте позже.")
            return

        _record_user_cost(chat_id, result["total_cost_usd"])
        caption = (
            f"{result['prompt']}\n"
            f"Engine: {result['engine']}\n"
            f"Cost: ${result['total_cost_usd']:.4f}\n"
            f"Record ID: {result['record_id']} (для /persona_redo)"
        )
        _safe_send_video(chat_id, result["video_url"], caption=caption)

    threading.Thread(target=_run, daemon=True).start()


def handle_persona_redo(chat_id: int, args: str) -> None:
    """/persona_redo <record_id> [new_prompt] — redo a previous video generation."""
    parts = args.strip().split(None, 1)
    if not parts or not parts[0]:
        _safe_send(chat_id, "Укажите ID записи: /persona_redo <record_id> [new_prompt]")
        return

    record_id = parts[0]
    new_prompt = parts[1].strip() if len(parts) > 1 else None

    # Пре-гейт дневного лимита СТРОГО до старта платной перегенерации (дыра a).
    allowed, reason = check_limit(chat_id, estimated_usd=_persona_redo_est())
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    logger.info("handle_persona_redo chat=%s record=%s", chat_id, record_id)
    _safe_send(chat_id, f"Перегенерирую видео по записи {record_id}...")

    def _run() -> None:
        setup_block_m_logging()

        from app.services.block_m1_persona.photo_generator import PhotoGenerator
        from app.services.block_m2_video.generation_history import GenerationHistory
        from app.services.block_m2_video.video_client_extras import VideoClientExtras
        from app.services.block_m2_video.video_generator import VideoGenerator
        from app.services.block_m2_video.video_storage import VideoStorage

        async def _async() -> dict:
            storage = PersonaStorage()
            client = ReplicateVideoClient()
            tracker = CostTracker()
            photo_gen = PhotoGenerator(client, storage, tracker)
            video_extras = VideoClientExtras(client)
            video_storage = VideoStorage()
            history = GenerationHistory()
            gen = VideoGenerator(photo_gen, video_extras, video_storage, history, tracker)
            return await gen.redo_video(record_id, new_prompt=new_prompt)

        try:
            result = asyncio.run(_async())
        except ValueError as exc:
            _safe_send(chat_id, str(exc))
            return
        except DailyLimitExceeded as exc:
            _safe_send(chat_id, f"Превышен дневной лимит: {translate_exception(exc)}")
            return
        except Exception as exc:
            logger.error(
                "handle_persona_redo failed chat=%s: %s", chat_id, exc, exc_info=True
            )
            _safe_send(chat_id, "Ошибка при перегенерации. Попробуйте позже.")
            return

        _record_user_cost(chat_id, result["total_cost_usd"])
        caption = (
            f"{result['prompt']}\n"
            f"Engine: {result['engine']}\n"
            f"Cost: ${result['total_cost_usd']:.4f}\n"
            f"Record ID: {result['record_id']}"
        )
        _safe_send_video(chat_id, result["video_url"], caption=caption)

    threading.Thread(target=_run, daemon=True).start()


def handle_persona_engine(chat_id: int, args: str) -> None:
    """/persona_engine [kling|wan22] — set or show the default video engine."""
    arg = args.strip().lower()
    if not arg:
        current = _get_engine_pref(chat_id)
        _safe_send(
            chat_id,
            f"Текущий engine: {current}\n"
            "Доступно:\n"
            "  kling  → kling_v21  (качество, ~$0.28)\n"
            "  wan22  → wan22_fast (быстрее и дешевле, ~$0.10)",
        )
        return

    engine_map = {"kling": "kling_v21", "wan22": "wan22_fast"}
    engine = engine_map.get(arg)
    if not engine:
        _safe_send(chat_id, f"Неизвестный engine: {arg!r}\nДоступно: kling, wan22")
        return

    _set_engine_pref(chat_id, engine)
    _safe_send(chat_id, f"Engine установлен: {engine}")


def handle_cancel_lora(chat_id: int, args: str) -> None:
    """/cancel_lora <persona_id> — cancel in-progress LoRA training."""
    persona_id = args.strip()
    if not persona_id:
        _safe_send(chat_id, "Укажите ID персоны: /cancel_lora <persona_id>")
        return

    logger.info("handle_cancel_lora chat=%s persona=%s", chat_id, persona_id)

    # Also clear any pending confirmation for this persona
    if _lora_pending_confirm.get(chat_id) == persona_id:
        del _lora_pending_confirm[chat_id]

    try:
        from app.services.block_m_common.cost_tracker import CostTracker
        from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
        storage = PersonaStorage()
        queue = VideoQueue()
        trainer = LoRATrainer(ReplicateVideoClient.__new__(ReplicateVideoClient), storage, CostTracker(), queue)

        cancelled = _run_async(trainer.cancel_training(persona_id))
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка отмены: {translate_exception(exc)}")
        return

    if cancelled:
        _safe_send(chat_id, f"Тренировка для {persona_id!r} отменена.")
    else:
        _safe_send(chat_id, f"Нет активной тренировки для {persona_id!r}.")


# ── Fun Mode (Me-Persona / face-swap) ──────────────────────────────────────────

_ME_MIN_PHOTOS = 3


def handle_me_seed(chat_id: int) -> None:
    """/me_seed — start a seed-photo collection session for the owner's Me-Persona."""
    logger.info("handle_me_seed chat=%s", chat_id)
    collector = _get_seed_collector()
    session = collector.start_session(chat_id, max_photos=10)
    _safe_send(
        chat_id,
        f"Начинаю сбор фото для вашей Me-Persona.\n"
        f"Отправьте до {session.max_photos} фото вашего лица.\n"
        f"Когда закончите — введите /me_done",
    )


def handle_me_done(chat_id: int) -> None:
    """/me_done — finalise the seed session and prompt for LoRA training."""
    logger.info("handle_me_done chat=%s", chat_id)
    collector = _get_seed_collector()
    session = collector.get_session(chat_id)

    if session is None:
        _safe_send(chat_id, "Нет активной сессии. Сначала введите /me_seed.")
        return

    if session.count < _ME_MIN_PHOTOS:
        _safe_send(
            chat_id,
            f"Недостаточно фото: {session.count}/{_ME_MIN_PHOTOS} минимум.\n"
            "Отправьте ещё фото или введите /me_seed заново.",
        )
        return

    photos = list(session.photo_urls)
    collector.end_session(chat_id)

    async def _save() -> None:
        from app.services.block_m22_fun.me_persona import MePersonaData, MePersonaManager
        from datetime import datetime
        manager = MePersonaManager()
        data = MePersonaData(
            chat_id=chat_id,
            persona_id=f"me_persona_{chat_id}",
            trigger_word=f"me{chat_id}",
            lora_weights_url="",
            seed_photos=photos,
            created_at=datetime.utcnow(),
        )
        await manager.save(data)

    try:
        _run_async(_save())
    except Exception as exc:
        logger.error("handle_me_done save failed chat=%s: %s", chat_id, exc, exc_info=True)
        _safe_send(chat_id, f"Ошибка сохранения: {translate_exception(exc)}")
        return

    _me_pending_train_confirm[chat_id] = None
    _safe_send(
        chat_id,
        f"Собрано {len(photos)} фото.\n"
        f"Запустить тренировку LoRA для вашей Me-Persona? (~$5.00, ~20 мин)\n"
        "Ответьте \"да\" / \"нет\".",
    )


def _do_train_me_lora(chat_id: int) -> None:
    """Start Me-Persona LoRA training in a background thread."""
    # Пре-гейт дневного лимита СТРОГО до старта платной тренировки (~$5, дыра a).
    _est = _train_me_lora_est()
    allowed, reason = check_limit(chat_id, estimated_usd=_est)
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    # Резерв оценки на старте (hole c). me_done раньше не писался НИ в один
    # леджер; train_flux_lora не возвращает гранулярную стоимость, поэтому
    # дельты нет — резерв оценки и есть запись траты (restart-safe).
    _record_user_cost(chat_id, _est)

    _safe_send(
        chat_id,
        "Тренировка Me-Persona запущена (~20 минут). "
        "Я уведомлю вас когда будет готово.",
    )
    logger.info("Launching Me-LoRA training thread chat=%s", chat_id)

    def _run() -> None:
        setup_block_m_logging()

        async def _async() -> None:
            from app.services.block_m22_fun.me_persona import MePersonaManager
            manager = MePersonaManager()
            data = await manager.get(chat_id)
            if data is None:
                _safe_send(chat_id, "Me-Persona не найдена. Начните заново: /me_seed")
                return

            client = ReplicateVideoClient()
            result = await client.train_flux_lora(
                images=data.seed_photos,
                trigger_word=data.trigger_word,
            )
            data.lora_weights_url = result["weights_url"]
            await manager.save(data)
            _safe_send(
                chat_id,
                f"Me-Persona обучена! Теперь можно использовать:\n"
                f"  /me_swap_photo <промпт>\n"
                f"  /me_swap_video <url_видео>",
            )

        try:
            asyncio.run(_async())
        except Exception as exc:
            logger.error("Me-LoRA training failed chat=%s: %s", chat_id, exc, exc_info=True)
            _safe_send(chat_id, f"Ошибка тренировки Me-Persona: {translate_exception(exc)}")

    threading.Thread(target=_run, daemon=True).start()


def handle_me_swap_photo(chat_id: int, args: str) -> None:
    """/me_swap_photo <prompt> — generate a photo using the owner's Me-Persona LoRA."""
    prompt = args.strip()
    if not prompt:
        _safe_send(chat_id, "Укажите промпт: /me_swap_photo <prompt>")
        return

    # Пре-гейт дневного лимита СТРОГО до запуска платной генерации (T7).
    allowed, reason = check_limit(chat_id, estimated_usd=_me_swap_est())
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    logger.info("handle_me_swap_photo chat=%s", chat_id)
    _safe_send(chat_id, "Генерирую фото с вашей Me-Persona...")

    def _run() -> None:
        setup_block_m_logging()

        async def _async() -> dict:
            from app.services.block_m22_fun.me_persona import MePersonaManager
            manager = MePersonaManager()
            data = await manager.get(chat_id)
            if data is None:
                raise ValueError("Me-Persona не найдена. Начните с /me_seed")
            if not data.is_trained:
                raise ValueError("Me-Persona ещё не натренирована. Введите /me_done")

            client = ReplicateVideoClient()
            tracker = CostTracker()
            result = await client.generate_flux_with_lora(
                prompt=prompt,
                lora_url=data.lora_weights_url,
                trigger_word=data.trigger_word,
            )
            await tracker.log_expense("me_swap_photo", result["cost_usd"], data.persona_id)
            _record_user_cost(chat_id, result["cost_usd"])
            return result

        try:
            result = asyncio.run(_async())
        except (ValueError, DailyLimitExceeded) as exc:
            _safe_send(chat_id, str(exc))
            return
        except Exception as exc:
            logger.error("handle_me_swap_photo failed chat=%s: %s", chat_id, exc, exc_info=True)
            _safe_send(chat_id, "Ошибка генерации. Попробуйте позже.")
            return

        _safe_send_photo(
            chat_id, result["image_url"],
            caption=f"Me-Persona: {prompt[:120]}\nCost: ${result['cost_usd']:.4f}",
        )

    threading.Thread(target=_run, daemon=True).start()


def handle_me_swap_video(chat_id: int, args: str) -> None:
    """/me_swap_video <video_url> — swap owner's face into a video."""
    video_url = args.strip()
    if not video_url:
        _safe_send(chat_id, "Укажите URL видео: /me_swap_video <video_url>")
        return

    # Пре-гейт дневного лимита СТРОГО до запуска платного face-swap видео (T7).
    allowed, reason = check_limit(chat_id, estimated_usd=_me_swap_est())
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    logger.info("handle_me_swap_video chat=%s", chat_id)
    _safe_send(chat_id, "Запускаю face-swap видео...")

    def _run() -> None:
        setup_block_m_logging()

        async def _async() -> dict:
            from app.services.block_m22_fun.me_persona import MePersonaManager
            from app.services.block_m22_fun.video_face_swap import VideoFaceSwapPipeline
            manager = MePersonaManager()
            data = await manager.get(chat_id)
            if data is None:
                raise ValueError("Me-Persona не найдена. Начните с /me_seed")
            if not data.seed_photos:
                raise ValueError("Нет seed-фото. Начните с /me_seed")

            client = ReplicateVideoClient()
            tracker = CostTracker()
            pipeline = VideoFaceSwapPipeline(client)
            result = await pipeline.swap_video(chat_id, video_url, data)
            await tracker.log_expense("me_swap_video", result["cost_usd"], data.persona_id)
            _record_user_cost(chat_id, result["cost_usd"])
            return result

        try:
            result = asyncio.run(_async())
        except (ValueError, PermissionError, DailyLimitExceeded) as exc:
            _safe_send(chat_id, str(exc))
            return
        except Exception as exc:
            logger.error("handle_me_swap_video failed chat=%s: %s", chat_id, exc, exc_info=True)
            _safe_send(chat_id, "Ошибка face-swap. Попробуйте позже.")
            return

        _safe_send_video(
            chat_id, result["output_url"],
            caption=f"Face-swap готов! Cost: ${result['cost_usd']:.4f}",
        )

    threading.Thread(target=_run, daemon=True).start()


def handle_photo_message(chat_id: int, photo_url: str) -> bool:
    """Route an incoming photo to an active seed-collection session if one exists.

    Returns:
        True if the photo was consumed by the seed collector, False otherwise.
    """
    collector = _get_seed_collector()
    if not collector.has_session(chat_id):
        return False

    session = collector.add_photo(chat_id, photo_url)
    if session is None:
        return False

    if session.is_complete:
        _safe_send(
            chat_id,
            f"Получено {session.count} фото — лимит достигнут. "
            "Введите /me_done для продолжения.",
        )
    else:
        _safe_send(
            chat_id,
            f"Фото добавлено ({session.count}/{session.max_photos}). "
            "Отправьте ещё или введите /me_done.",
        )
    return True


# ── Polish — costs, history, batch ─────────────────────────────────────────────

def handle_costs(chat_id: int, args: str = "") -> None:
    """/costs [days] — сводка трат за N дней (дефолт 7): по дням + сравнение с
    предыдущим периодом такой же длины, из audit-леджера (тот же стор, куда
    пишет record_cost/guard_spend и читает /my_stats) — эти цифры точные.

    Плюс best-effort разбивка по категориям (LLM/Фото/Видео/Тренинг) и топ-5
    операций из block_m-леджера (state/personas/expenses.jsonl) — единственного
    места, где вообще есть имя операции. Он заморожен с 2026-05 (см.
    docs/superpowers/plans/2026-07-04-money-consolidation.md) — большая часть
    живых трат (LLM-роутер, face-swap, часть persona-операций) туда не попадает,
    поэтому раздел может быть пустым/неполным и подписан как таковой; «Итого»
    выше него — единственное точное число.
    """
    logger.info("handle_costs chat=%s args=%r", chat_id, args)
    days = 7
    parts = args.strip().split()
    if parts:
        try:
            n = int(parts[0])
            if n > 0:
                days = n
        except ValueError:
            pass

    try:
        from datetime import datetime, timedelta

        from app.services.costs_summary import build_summary, format_costs_message

        today = datetime.now(_user_cost.KYIV_TZ).date()
        prev_start = today - timedelta(days=2 * days - 1)
        audit_totals = _user_cost.get_costs_range(prev_start, today)

        tracker = CostTracker()
        entries = _run_async(tracker.get_all_entries())

        summary = build_summary(audit_totals, entries, days, today)
        _safe_send(chat_id, format_costs_message(summary))
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка получения статистики: {translate_exception(exc)}")


def handle_history(chat_id: int, args: str) -> None:
    """/history <persona_id> [limit] — show recent generation history."""
    parts = args.strip().split()
    if not parts:
        _safe_send(chat_id, "Укажите ID персоны: /history <persona_id> [limit]")
        return

    persona_id = parts[0]
    try:
        limit = int(parts[1]) if len(parts) > 1 else 10
        limit = max(1, min(limit, 50))
    except ValueError:
        limit = 10

    logger.info("handle_history chat=%s persona=%s limit=%d", chat_id, persona_id, limit)

    try:
        from app.services.block_m2_video.generation_history import GenerationHistory

        async def _async():
            history = GenerationHistory()
            return await history.list_recent(persona_id, limit=limit)

        records = _run_async(_async())
    except Exception as exc:
        _safe_send(chat_id, f"Ошибка получения истории: {translate_exception(exc)}")
        return

    if not records:
        _safe_send(chat_id, f"Нет записей для персоны {persona_id!r}.")
        return

    lines = [f"История персоны {persona_id} (последние {len(records)}):"]
    for r in records:
        ts = r.created_at.strftime("%m-%d %H:%M") if hasattr(r.created_at, "strftime") else str(r.created_at)
        lines.append(
            f"• [{ts}] {r.kind} | {r.engine} | ${r.cost_usd:.4f} | ID: {r.record_id}"
        )
        if r.prompt:
            lines.append(f"  Промпт: {r.prompt[:80]}")
    _safe_send(chat_id, "\n".join(lines))


def handle_persona_batch(chat_id: int, args: str) -> None:
    """/persona_batch <persona_id> <count> <prompt> — generate N photos in batch."""
    parts = args.strip().split(None, 2)
    if len(parts) < 3:
        _safe_send(
            chat_id,
            "Использование: /persona_batch <persona_id> <count> <prompt>",
        )
        return

    persona_id, count_str, prompt = parts[0], parts[1], parts[2]
    try:
        count = int(count_str)
        if count < 1 or count > 20:
            raise ValueError
    except ValueError:
        _safe_send(chat_id, "Количество должно быть числом от 1 до 20.")
        return

    # Пре-гейт дневного лимита СТРОГО до старта платной batch-генерации (дыра a).
    # Оценка масштабируется числом фото (est = per-photo × N).
    allowed, reason = check_limit(chat_id, estimated_usd=_persona_batch_est(count))
    if not allowed:
        _safe_send(chat_id, f"🚫 {reason}")
        return

    logger.info("handle_persona_batch chat=%s persona=%s count=%d", chat_id, persona_id, count)
    _safe_send(chat_id, f"Запускаю batch-генерацию: {count} фото...")

    def _run() -> None:
        setup_block_m_logging()

        from app.services.block_m1_persona.photo_generator import PhotoGenerator
        from app.services.block_m23_polish.batch_generator import BatchGenerator

        def _progress(done: int, total: int, url: str) -> None:
            _safe_send(chat_id, f"Batch: {done}/{total} готово")
            _safe_send_photo(chat_id, url, caption=f"#{done}")

        async def _async() -> list:
            storage = PersonaStorage()
            client = ReplicateVideoClient()
            tracker = CostTracker()
            photo_gen = PhotoGenerator(client, storage, tracker)
            batch = BatchGenerator(photo_gen)
            return await batch.generate_batch(
                persona_id, prompt, count, progress_cb=_progress
            )

        try:
            results = asyncio.run(_async())
        except Exception as exc:
            logger.error(
                "handle_persona_batch failed chat=%s: %s", chat_id, exc, exc_info=True
            )
            _safe_send(chat_id, f"Ошибка batch-генерации: {translate_exception(exc)}")
            return

        total_cost = sum(r.get("cost_usd", 0.0) for r in results)
        _record_user_cost(chat_id, total_cost)
        _safe_send(
            chat_id,
            f"Batch завершён: {len(results)}/{count} фото\n"
            f"Общая стоимость: ${total_cost:.4f}",
        )

    threading.Thread(target=_run, daemon=True).start()
