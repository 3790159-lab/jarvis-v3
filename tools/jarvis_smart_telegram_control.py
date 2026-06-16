from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# --- File logging (M.1.5 #2) ---
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from app.core.logging_setup import setup_app_logging
    setup_app_logging("jarvis_bot.log")
except Exception as _logging_exc:
    print(f"[bot] logging setup failed: {_logging_exc}", flush=True)

from app.services.error_translator import translate_exception
from app.services.auth import whitelist as _whitelist
from app.services.audit import audit_logger as _audit
from app.services.audit import cost_tracker as _cost

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = str(os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")).strip()

_HEARTBEAT_FILE = Path("state/bot_heartbeat.txt")
_PID_FILE = Path("state/bot.pid")


def _check_single_instance() -> bool:
    """Return True if no other bot instance is running."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
            if sys.platform == "win32":
                import subprocess
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(old_pid) in result.stdout:
                    print(f"❌ Bot already running with PID {old_pid}", flush=True)
                    print(f"   Kill it first: taskkill /F /PID {old_pid}", flush=True)
                    return False
            else:
                try:
                    os.kill(old_pid, 0)
                    print(f"❌ Bot already running with PID {old_pid}", flush=True)
                    return False
                except OSError:
                    pass
        except Exception:
            pass
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True
BACKEND = (
    os.environ.get("BACKEND_BASE_URL")
    or os.environ.get("TELEGRAM_BACKEND_URL")
    or "http://127.0.0.1:8010"
).rstrip("/")
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

ROOT = Path.cwd()
STATE_DIR = ROOT / "jarvis_stage3_artifacts" / "telegram_smart_control"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = STATE_DIR / "brain_v2_state.json"


def default_state() -> Dict[str, Any]:
    return {
        "mode": "auto",
        "language": "ru",
        "table_language": "ru",
        "keep_names_original": True,
        "last_topic": "",
        "last_table_query": "",
        "last_table_path": "",
        "pending": None,
        "pending_task_id": None,
        "last_uploaded_file": None,
        "last_plan": None,
        "preferences": {
            "answer_language": "ru",
            "tables_language": "ru",
            "names_original": True,
            "short_status": True,
        },
    }


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            base = default_state()
            base.update(state)
            base["preferences"].update(state.get("preferences") or {})
            return base
        except Exception:
            pass
    return default_state()


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 180) -> Dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except Exception as e:
        return {"ok": False, "_error": str(e), "_error_type": type(e).__name__}


def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return http_json("POST", f"{TG}/{method}", payload, timeout=60)


def send(chat_id: str, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
    if len(text) > 3900:
        text = text[:3900] + "\n\n...обрезано"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_call("sendMessage", payload)


def send_and_get_id(chat_id: str, text: str) -> Optional[int]:
    """Send message and return message_id for later editing."""
    if len(text) > 3900:
        text = text[:3900] + "\n\n...обрезано"
    resp = tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    })
    return (resp.get("result") or {}).get("message_id")


def edit_message(chat_id: str, message_id: Optional[int], text: str) -> None:
    """Edit a previously sent message by ID. No-op if message_id is None."""
    if not message_id:
        return
    if len(text) > 3900:
        text = text[:3900] + "\n\n...обрезано"
    tg_call("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    })


def send_with_feedback(
    chat_id: str,
    text: str,
    decision_id: Optional[str] = None,
    query: str = "",
) -> None:
    """Send message with quick-action keyboard: 👍 👎 🔄 📋."""
    if not decision_id:
        send(chat_id, text)
        return
    try:
        _r = str(Path(__file__).parent.parent)
        import sys as _sys_fb
        if _r not in _sys_fb.path:
            _sys_fb.path.insert(0, _r)
        from app.services.decision_log import make_feedback_keyboard
        kb = make_feedback_keyboard(decision_id)
        # Add quick-action row: Подробнее + В Obsidian
        quick_row = []
        if query:
            quick_row.append({"text": "🔄 Подробнее", "callback_data": f"qa:more:{decision_id}"})
        quick_row.append({"text": "📋 В Obsidian", "callback_data": f"qa:obsidian:{decision_id}"})
        kb["inline_keyboard"].append(quick_row)
        send(chat_id, text, reply_markup=kb)
    except Exception:
        send(chat_id, text)


def _compact_text(text: str, max_sentences: int = 2) -> str:
    """Trim response to max_sentences for mobile. Strips markdown headers/tables."""
    import re as _re
    # Remove markdown headers
    text = _re.sub(r"^#{1,6}\s+.+$", "", text, flags=_re.MULTILINE)
    # Remove table rows (lines with | ... |)
    text = _re.sub(r"^\|.*\|.*$", "", text, flags=_re.MULTILINE)
    # Collapse multiple blank lines
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    # Split into sentences and take first max_sentences
    sentences = _re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    return " ".join(sentences[:max_sentences]) if sentences else text


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Acknowledge a Telegram callback_query (required within 10s)."""
    try:
        result = tg_call("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text[:200],
        })
        if not result.get("ok", True):
            print(f"[CQ] answer rejected: {result}", flush=True)
    except Exception as e:
        print(f"[CQ] answer failed: {e}", flush=True)


def edit_message_with_keyboard(
    chat_id: str,
    message_id: int,
    text: str,
    inline_keyboard: list,
) -> None:
    """Edit message text and inline keyboard simultaneously."""
    if len(text) > 3900:
        text = text[:3900] + "\n\n...обрезано"
    tg_call("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": {"inline_keyboard": inline_keyboard},
        "disable_web_page_preview": True,
    })


def send_with_keyboard(chat_id: str, text: str, inline_keyboard: list) -> Optional[int]:
    """Send message with inline keyboard; returns message_id."""
    if len(text) > 3900:
        text = text[:3900] + "\n\n...обрезано"
    resp = tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": inline_keyboard},
        "disable_web_page_preview": True,
    })
    return (resp.get("result") or {}).get("message_id")


def _send_photo_url(chat_id: str, url: str, caption: str = "") -> None:
    """Send a photo by URL via Telegram sendPhoto."""
    tg_call("sendPhoto", {
        "chat_id": chat_id,
        "photo": url,
        "caption": caption[:1024] if caption else "",
    })


def _send_local_video(chat_id, path, caption: str = "") -> None:
    """Upload a local MP4 to Telegram via multipart sendVideo.

    Used by Block M.2 Phase A handlers which save videos to
    ``state/personas/videos/...``.
    """
    import requests as _req
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        send(str(chat_id), f"⚠️ Video file not found: {p}")
        return
    with p.open("rb") as fh:
        _req.post(
            f"{TG}/sendVideo",
            data={"chat_id": str(chat_id), "caption": caption[:1024] if caption else ""},
            files={"video": (p.name, fh, "video/mp4")},
            timeout=300,
        )


def _send_local_photo(chat_id, path, caption: str = "") -> None:
    """Upload a local image to Telegram via multipart sendPhoto.

    Used by Block M.2.5 face-swap to deliver swapped photos.
    """
    import requests as _req
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        send(str(chat_id), f"⚠️ Photo file not found: {p}")
        return
    with p.open("rb") as fh:
        _req.post(
            f"{TG}/sendPhoto",
            data={"chat_id": str(chat_id), "caption": caption[:1024] if caption else ""},
            files={"photo": (p.name, fh, "image/jpeg")},
            timeout=120,
        )


def _send_local_media_group(chat_id, paths, caption: str = "") -> None:
    """Upload up to 10 local images as a Telegram album via sendMediaGroup.

    The first photo carries the caption; the rest are uncaptioned. If more
    than 10 paths are supplied, chunks of 10 are sent sequentially.
    """
    import requests as _req
    from pathlib import Path as _Path
    files_kept: list = []
    for chunk_start in range(0, len(paths), 10):
        chunk = paths[chunk_start:chunk_start + 10]
        media: list = []
        files: dict = {}
        attach_names: list = []
        for idx, raw in enumerate(chunk):
            p = _Path(raw)
            if not p.exists():
                continue
            attach_name = f"file{idx}"
            fh = p.open("rb")
            files_kept.append(fh)  # keep open until requests finishes
            files[attach_name] = (p.name, fh, "image/jpeg")
            entry: Dict[str, Any] = {
                "type": "photo",
                "media": f"attach://{attach_name}",
            }
            if idx == 0 and chunk_start == 0 and caption:
                entry["caption"] = caption[:1024]
            media.append(entry)
            attach_names.append(attach_name)
        if not media:
            continue
        try:
            _req.post(
                f"{TG}/sendMediaGroup",
                data={
                    "chat_id": str(chat_id),
                    "media": json.dumps(media, ensure_ascii=False),
                },
                files=files,
                timeout=300,
            )
        finally:
            for fh in files_kept:
                try:
                    fh.close()
                except Exception:
                    pass
            files_kept.clear()


# ── Block M.2 Phase C: /persona_video dispatch ──────────────────────────────

_video_lock = None  # GenerationLock singleton; lazily created on first use


def _get_video_lock():
    global _video_lock
    if _video_lock is None:
        _r = str(Path(__file__).parent.parent)
        import sys as _sys
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.block_m2_video.generation_lock import GenerationLock
        from app.services.block_m2_video.swap_sentinel import (
            mark_swap_start,
            mark_swap_end,
        )
        # Mark state/swap_active for the duration of any video generation so the
        # backend watchdog does not mistake a long (3-12 min) swap for a hung
        # bot and kill it. Ref-counted by the lock: created on first acquire,
        # removed only when the last chat releases.
        _video_lock = GenerationLock(
            on_first_acquire=mark_swap_start,
            on_last_release=mark_swap_end,
        )
    return _video_lock


def _persona_video_dispatch(
    chat_id,
    query: str,
    *,
    input_photo_path: Optional[str] = None,
    command: str = "video",
) -> None:
    """Phase C entry point for ``/persona_video`` and ``/persona_video_redo``.

    Runs the generation in a daemon thread so the Telegram polling loop is
    never blocked for the ~5–12 min the engine takes. Holds a per-chat
    :class:`GenerationLock` for the duration; second concurrent invocations
    for the same chat get rejected with a friendly message.
    """
    import asyncio as _aio
    import threading
    from pathlib import Path as _Path

    _r = str(Path(__file__).parent.parent)
    import sys as _sys
    if _r not in _sys.path:
        _sys.path.insert(0, _r)

    from app.handlers.persona_video_handler import PersonaVideoHandler
    from app.services.block_m2_video.generation_lock import GenerationLockBusy

    handler = PersonaVideoHandler()
    chat_id_int = int(chat_id)
    chat_id_s = str(chat_id)
    lock = _get_video_lock()

    # Bare /persona_video → help reply (cheap, no lock).
    if command == "video" and not query.strip():
        try:
            help_text = _aio.run(handler.handle_help())
        except Exception as exc:
            help_text = f"❌ Не удалось получить справку: {exc}"
        send(chat_id_s, help_text)
        return

    try:
        token = lock.acquire(chat_id_int)
    except GenerationLockBusy:
        send(chat_id_s, "⏳ Уже идёт генерация. Дождитесь завершения.")
        return

    # Stage 1 — immediate ack (well under 1s, before the thread starts).
    persona_hint = query.split(None, 1)[0] if query.strip() else "?"
    send(chat_id_s, f"🎬 Принял задачу. Запускаю генерацию для «{persona_hint}»…")

    def _progress(stage: str, payload: dict) -> None:
        if stage == "persona_resolved":
            send(
                chat_id_s,
                f"✅ Найдена персона: {payload.get('persona_name')} "
                f"({payload.get('persona_id')})",
            )
        elif stage == "engine_selected":
            send(
                chat_id_s,
                f"🎥 Engine: {payload.get('engine_name')} "
                f"(mode={payload.get('mode')}). Генерирую видео (~5–12 мин)…",
            )

    def _run() -> None:
        photo_path_obj = _Path(input_photo_path) if input_photo_path else None
        try:
            if command == "redo":
                full_text = f"/persona_video_redo {query}".strip()
                result = _aio.run(handler.handle_redo(
                    full_text, chat_id_int,
                    progress_cb=_progress,
                    input_photo_path=photo_path_obj,
                ))
            else:
                full_text = f"/persona_video {query}".strip()
                result = _aio.run(handler.handle_video(
                    full_text, chat_id_int,
                    progress_cb=_progress,
                    input_photo_path=photo_path_obj,
                ))
            send(chat_id_s, result["summary"])
            _send_local_video(chat_id_s, result["output_path"])
        except Exception as exc:
            send(chat_id_s, f"❌ Ошибка: {translate_exception(exc)}")
        finally:
            lock.release(token)

    threading.Thread(
        target=_run, daemon=True, name=f"persona_video_{chat_id_int}"
    ).start()


def _persona_video_intercept(chat_id: str, msg: Dict[str, Any]) -> bool:
    """Route /persona_video with attached or replied-to photo.

    Returns True if the message was handled here. The caller must skip its
    normal dispatch when this returns True.
    """
    text = msg.get("text") or ""
    caption = msg.get("caption") or ""
    cmd = "/persona_video"

    # Case 1: photo attached, caption begins with /persona_video.
    if msg.get("photo") and caption.startswith(cmd):
        photo = msg["photo"][-1]
        file_id = photo.get("file_id")
        if not file_id:
            return False
        local = _download_telegram_file(
            file_id, f"persona_video_caption_{int(time.time())}.jpg"
        )
        if not local:
            send(chat_id, "❌ Не удалось скачать прикреплённое фото.")
            return True
        query = caption[len(cmd):].strip()
        _persona_video_dispatch(chat_id, query, input_photo_path=local)
        return True

    # Case 2: text command replying to a photo message.
    reply = msg.get("reply_to_message") or {}
    if text.startswith(cmd) and reply.get("photo"):
        photo = reply["photo"][-1]
        file_id = photo.get("file_id")
        if not file_id:
            return False
        local = _download_telegram_file(
            file_id, f"persona_video_reply_{int(time.time())}.jpg"
        )
        if not local:
            send(chat_id, "❌ Не удалось скачать фото из ответа.")
            return True
        query = text[len(cmd):].strip()
        _persona_video_dispatch(chat_id, query, input_photo_path=local)
        return True

    return False


# ── Block M.2.6 video face swap (готовое видео → замена лица → новое видео) ──

_VIDEO_SWAP_CMD = "/video_face_swap"


def _video_face_swap_dispatch(chat_id) -> None:
    """Router-tool entry point: explain how to send the video + face photo.

    The actual media arrive as a Telegram message handled by
    :func:`_video_face_swap_intercept`; this only nudges the user.
    """
    send(
        str(chat_id),
        "🎭 Замена лица в видео.\n"
        "Пришли видео, затем ответь на него фото с лицом и подписью "
        f"{_VIDEO_SWAP_CMD} (или наоборот: видео с подписью в ответ на фото).\n"
        "Лимиты: видео до 60 сек, до 1080p.",
    )


def _video_face_swap_run(chat_id, face_path, video_path) -> None:
    """Run the video swap in a worker thread, holding the shared video lock.

    Uses the same :func:`_get_video_lock` as animation / swapbatch so only one
    pod job runs at a time (один под — одна задача). The pod-side work is the
    untested boundary (verified manually on a live pod).
    """
    import asyncio as _aio
    import threading
    from pathlib import Path as _Path

    from app.services.block_m2_video.generation_lock import GenerationLockBusy
    from app.services.block_m2_face_swap.video_face_swap_engine import (
        VideoFaceSwapEngine,
        VideoTooLongError,
    )

    chat_id_int = int(chat_id)
    chat_id_s = str(chat_id)
    lock = _get_video_lock()
    try:
        token = lock.acquire(chat_id_int)
    except GenerationLockBusy:
        send(chat_id_s, "⏳ Уже идёт другая генерация. Дождитесь завершения.")
        return

    send(
        chat_id_s,
        "🎬 Принял видео и лицо. Заменяю лицо во всём видео "
        "(это займёт несколько минут)…",
    )

    def _progress(stage: str, payload: dict) -> None:
        if stage == "planned":
            send(
                chat_id_s,
                f"📊 Кадров: {payload.get('frames')} • оценка "
                f"~${payload.get('est_usd')} • ~{payload.get('est_minutes')} мин",
            )
        elif stage == "pod_ready":
            send(
                chat_id_s,
                f"✅ Pod готов ({'reused' if payload.get('reused') else 'fresh'}).",
            )

    def _run() -> None:
        try:
            engine = VideoFaceSwapEngine()
            out = _aio.run(
                engine.swap_video(
                    _Path(face_path), _Path(video_path), progress_cb=_progress,
                )
            )
            send(chat_id_s, "✅ Готово.")
            _send_local_video(chat_id_s, str(out))
        except VideoTooLongError as exc:
            send(chat_id_s, f"⚠️ Видео слишком длинное: {exc}")
        except Exception as exc:  # noqa: BLE001
            send(chat_id_s, f"❌ Ошибка: {translate_exception(exc)}")
        finally:
            lock.release(token)

    threading.Thread(
        target=_run, daemon=True, name=f"video_face_swap_{chat_id_int}",
    ).start()


def _video_face_swap_intercept(chat_id: str, msg: Dict[str, Any]) -> bool:
    """Route a ``/video_face_swap`` message (photo+video pair) into the flow.

    Two accepted shapes (both carry the caption ``/video_face_swap``):
      A) a photo replying to a video message, or
      B) a video replying to a photo message.
    Returns True if the message was consumed (caller must skip default
    handling); False for any non-trigger message (backward compat).
    """
    caption = msg.get("caption") or ""
    text = msg.get("text") or ""
    if not (caption.startswith(_VIDEO_SWAP_CMD) or text.startswith(_VIDEO_SWAP_CMD)):
        return False

    reply = msg.get("reply_to_message") or {}
    face_file_id: Optional[str] = None
    video_file_id: Optional[str] = None

    if msg.get("photo") and reply.get("video"):
        face_file_id = msg["photo"][-1].get("file_id")
        video_file_id = reply["video"].get("file_id")
    elif msg.get("video") and reply.get("photo"):
        video_file_id = msg["video"].get("file_id")
        face_file_id = reply["photo"][-1].get("file_id")
    else:
        send(
            chat_id,
            "Чтобы заменить лицо в видео: пришли фото-лицо в ответ на видео "
            f"с подписью {_VIDEO_SWAP_CMD} (или видео в ответ на фото).",
        )
        return True

    if not face_file_id or not video_file_id:
        send(chat_id, "Не удалось получить и видео, и фото-лицо.")
        return True

    ts = int(time.time())
    face_local = _download_telegram_file(face_file_id, f"vswap_face_{ts}.jpg")
    video_local = _download_telegram_file(video_file_id, f"vswap_vid_{ts}.mp4")
    if not face_local or not video_local:
        send(chat_id, "❌ Не удалось скачать видео или фото.")
        return True

    _video_face_swap_run(chat_id, face_local, video_local)
    return True


# ── Block M.2.5 /swapbatch dispatch ─────────────────────────────────────────


def _swapbatch_get_handler():
    """Lazy-init the FaceSwapHandler + orchestrator singletons.

    Returns ``(handler, orchestrator)`` or ``(None, None)`` if the
    block_m2_face_swap package is somehow not importable (broken install).
    """
    _r = str(Path(__file__).parent.parent)
    import sys as _sys
    if _r not in _sys.path:
        _sys.path.insert(0, _r)
    try:
        from app.handlers.face_swap_handler import FaceSwapHandler
        from app.services.block_m2_face_swap.batch_orchestrator import (
            get_orchestrator,
        )
        orch = get_orchestrator()
        return FaceSwapHandler(orchestrator=orch), orch
    except Exception as exc:  # noqa: BLE001
        print(f"[swapbatch] handler unavailable: {exc}", flush=True)
        return None, None


def _swapbatch_apply_reply(chat_id_s: str, reply) -> None:
    """Render a HandlerReply: numbered photos → text → album photos → videos."""
    if reply is None:
        return
    # Custom-prompts re-display: send each swapped photo individually with an
    # explicit 📸 N/M caption FIRST, then the instructions that reference them.
    numbered = getattr(reply, "numbered_photos", None)
    if numbered:
        total = len(numbered)
        for i, p in enumerate(numbered, start=1):
            try:
                _send_local_photo(chat_id_s, str(p), caption=f"📸 {i}/{total}")
            except Exception as exc:  # noqa: BLE001
                send(chat_id_s, f"⚠️ Не удалось отправить фото {i}/{total}: {exc}")
    if reply.text:
        send(chat_id_s, reply.text)
    if reply.photos:
        try:
            _send_local_media_group(chat_id_s, [str(p) for p in reply.photos])
        except Exception as exc:  # noqa: BLE001
            send(chat_id_s, f"⚠️ Не удалось отправить альбом: {exc}")
            for p in reply.photos:
                _send_local_photo(chat_id_s, str(p))
    if reply.videos:
        for v in reply.videos:
            _send_local_video(chat_id_s, str(v))


def _swapbatch_dispatch(chat_id, command: str) -> None:
    """Synchronous /swapbatch_* command router.

    Long-running phases (``go``, ``animate_yes``) spin up a worker thread
    that acquires the shared Phase C video-lock and runs the engine.
    """
    chat_id_s = str(chat_id)
    chat_id_int = int(chat_id)
    handler, orch = _swapbatch_get_handler()
    if handler is None:
        send(chat_id_s, "⚠️ Модуль face-swap недоступен.")
        return

    if command in ("help", ""):
        _swapbatch_apply_reply(chat_id_s, handler.handle_help())
        return
    if command == "source":
        _swapbatch_apply_reply(chat_id_s, handler.handle_source_intent(chat_id_int))
        return
    if command == "batch":
        _swapbatch_apply_reply(chat_id_s, handler.handle_batch_intent(chat_id_int))
        return
    if command == "status":
        _swapbatch_apply_reply(chat_id_s, handler.handle_status(chat_id_int))
        return
    if command == "cancel":
        _swapbatch_apply_reply(chat_id_s, handler.handle_cancel(chat_id_int))
        return
    if command == "animate_no":
        _swapbatch_apply_reply(chat_id_s, handler.handle_animate_no(chat_id_int))
        return
    if command == "no":
        _swapbatch_apply_reply(chat_id_s, handler.handle_no(chat_id_int))
        return
    if command == "animate_custom":
        _swapbatch_apply_reply(
            chat_id_s, handler.handle_animate_custom(chat_id_int)
        )
        return
    if command == "retry":
        _swapbatch_apply_reply(chat_id_s, handler.handle_retry(chat_id_int))
        return

    if command in ("go", "animate_yes", "confirm", "apply_partial", "apply_first"):
        _swapbatch_run_phase(chat_id_int, chat_id_s, command, handler)
        return

    send(chat_id_s, f"Неизвестная команда: /swapbatch_{command}")


def _swapbatch_run_phase(
    chat_id_int: int,
    chat_id_s: str,
    command: str,
    handler,
) -> None:
    """Acquire shared video lock, spawn worker thread, run swap/animate."""
    import asyncio as _aio
    import threading
    from pathlib import Path as _Path

    from app.services.block_m2_video.generation_lock import GenerationLockBusy

    lock = _get_video_lock()
    try:
        token = lock.acquire(chat_id_int)
    except GenerationLockBusy:
        send(chat_id_s, "⏳ Уже идёт другая генерация. Дождитесь завершения.")
        return

    if command == "go":
        send(chat_id_s, "🎭 Запускаю swap. Это займёт несколько минут…")
    else:
        send(chat_id_s, "🎬 Запускаю animate. Это займёт ~12 мин на видео…")

    def _progress(stage: str, payload: dict) -> None:
        if stage == "pod_ready":
            send(
                chat_id_s,
                f"✅ Pod готов ({'reused' if payload.get('reused') else 'fresh'}).",
            )
        elif stage == "swap_started":
            send(
                chat_id_s,
                f"⚙️ Swap {payload.get('index', 0) + 1}: {payload.get('filename')}",
            )
        elif stage == "swap_failed":
            send(
                chat_id_s,
                f"⚠️ Swap #{payload.get('index', 0) + 1} не удался: "
                f"{payload.get('error')}",
            )
        elif stage == "animate_step_done":
            send(chat_id_s, f"✅ Animate #{payload.get('index', 0) + 1} готов.")
        elif stage == "animate_step_failed":
            send(
                chat_id_s,
                f"⚠️ Animate #{payload.get('index', 0) + 1} не удался: "
                f"{payload.get('error')}",
            )

    def _run() -> None:
        try:
            if command == "go":
                from app.services.block_m2_face_swap.face_swap_engine import (
                    FaceSwapEngine,
                )
                engine = FaceSwapEngine()

                async def _swap_fn(
                    source: _Path,
                    targets: list,
                    cancel_check,
                ):
                    return await engine.swap_batch(
                        source, targets,
                        progress_cb=_progress, cancel_check=cancel_check,
                    )

                reply = _aio.run(
                    handler.run_swap_phase(
                        chat_id_int, _swap_fn, progress_cb=_progress,
                    )
                )
            else:  # animate_yes / confirm / apply_partial / apply_first
                from app.services.block_m2_video.engines.runpod_comfy_engine import (
                    RunpodComfyEngine,
                )
                from app.services.block_m2_video.engines.engine_protocol import (
                    VideoRequest,
                    new_generation_id,
                )

                video_engine = RunpodComfyEngine()
                # Default motion prompt — shared by /swapbatch_animate_yes and by
                # custom-flow photos that resolve to the default (None), so an
                # all-default custom run matches /swapbatch_animate_yes exactly.
                _DEFAULT_MOTION_PROMPT = "a cinematic portrait, soft natural light"

                # Task C: per-batch quality (duration + fps), read once from the
                # session. Defaults to the prior behavior (5s, 21fps native).
                _hq, _orch_q = _swapbatch_get_handler()
                _sess_q = _orch_q.get(chat_id_int) if _orch_q else None
                _duration = int(getattr(_sess_q, "duration_sec", 5) or 5)
                _fps = int(getattr(_sess_q, "fps", 21) or 21)

                if command == "animate_yes":
                    async def _animate_fn(swapped: _Path, idx: int, cancel_check):
                        req = VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}",
                            persona_name="swapbatch",
                            input_image_path=swapped,
                            prompt=_DEFAULT_MOTION_PROMPT,
                            seconds=_duration,
                            fps=_fps,
                            seed=None,
                            mode="hq",
                            generation_id=new_generation_id(),
                        )
                        result = await video_engine.generate(req)
                        return result.output_path

                    reply = _aio.run(
                        handler.run_animate_phase(
                            chat_id_int, _animate_fn, progress_cb=_progress,
                            user_id=chat_id_int,
                            username=_USERNAME_BY_CHAT.get(chat_id_s),
                        )
                    )
                else:  # confirm / apply_partial / apply_first → custom prompts
                    async def _animate_fn(
                        swapped: _Path, idx: int, prompt, cancel_check
                    ):
                        req = VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}",
                            persona_name="swapbatch",
                            input_image_path=swapped,
                            prompt=prompt or _DEFAULT_MOTION_PROMPT,
                            seconds=_duration,
                            fps=_fps,
                            seed=None,
                            mode="hq",
                            generation_id=new_generation_id(),
                        )
                        result = await video_engine.generate(req)
                        return result.output_path

                    reply = _aio.run(
                        handler.run_custom_animate_phase(
                            chat_id_int, _animate_fn, progress_cb=_progress,
                            user_id=chat_id_int,
                            username=_USERNAME_BY_CHAT.get(chat_id_s),
                        )
                    )
            _swapbatch_apply_reply(chat_id_s, reply)
        except Exception as exc:  # noqa: BLE001
            send(chat_id_s, f"❌ Ошибка: {translate_exception(exc)}")
        finally:
            lock.release(token)

    threading.Thread(
        target=_run, daemon=True, name=f"swapbatch_{command}_{chat_id_int}",
    ).start()


def _swapbatch_text_intercept(chat_id: str, text: str) -> bool:
    """Route a plain-text numbered-prompt message into the custom-prompts flow.

    Fires only when the chat is awaiting custom prompts AND the message is not a
    command (so /swapbatch_* commands still route normally). Returns True if the
    message was consumed.
    """
    if not text or text.lstrip().startswith("/"):
        return False
    handler, orch = _swapbatch_get_handler()
    if handler is None or orch is None:
        return False
    from app.services.block_m2_face_swap.batch_orchestrator import (
        STATE_AWAITING_CUSTOM_PROMPTS,
    )
    chat_id_int = int(chat_id)
    if orch.status(chat_id_int) != STATE_AWAITING_CUSTOM_PROMPTS:
        return False
    reply = handler.consume_custom_prompts_text(chat_id_int, text)
    if not getattr(reply, "consumed", True):
        return False
    _swapbatch_apply_reply(chat_id, reply)
    return True


def _swapbatch_photo_intercept(chat_id: str, msg: Dict[str, Any]) -> bool:
    """Single-photo intercept: route to swapbatch if session expects it.

    Returns True if we consumed the message (caller MUST skip default
    handling). Returns False to fall through to the legacy /faceswap path
    and ``_handle_file_message``.
    """
    handler, orch = _swapbatch_get_handler()
    if handler is None or orch is None:
        return False
    chat_id_int = int(chat_id)
    if not (
        orch.is_waiting_for_source(chat_id_int)
        or orch.is_waiting_for_targets(chat_id_int)
    ):
        return False
    photos = msg.get("photo")
    if not photos:
        return False
    largest = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
    file_id = largest.get("file_id")
    if not file_id:
        return False
    local = _download_telegram_file(
        file_id, f"swapbatch_{int(time.time())}_{file_id[:8]}.jpg"
    )
    if not local:
        send(chat_id, "❌ Не удалось скачать фото.")
        return True
    from pathlib import Path as _Path
    p = _Path(local)
    if orch.is_waiting_for_source(chat_id_int):
        _swapbatch_apply_reply(chat_id, handler.consume_source(chat_id_int, p))
    else:
        # Single-photo target treated as a 1-element album.
        _swapbatch_apply_reply(
            chat_id, handler.consume_targets_album(chat_id_int, [p])
        )
    return True


def _swapbatch_album_intercept(chat_id: str, msgs: list) -> bool:
    """Album-flush intercept: route media-group to swapbatch if waiting.

    Called by the long-poll loop right before its existing
    ``_handle_file_message`` loop. Returns True if consumed.
    """
    handler, orch = _swapbatch_get_handler()
    if handler is None or orch is None:
        return False
    chat_id_int = int(chat_id)
    if not orch.is_waiting_for_targets(chat_id_int):
        return False

    paths: list = []
    from pathlib import Path as _Path
    for m in msgs:
        photos = m.get("photo")
        if not photos:
            continue
        largest = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
        file_id = largest.get("file_id")
        if not file_id:
            continue
        local = _download_telegram_file(
            file_id,
            f"swapbatch_{int(time.time())}_{file_id[:8]}.jpg",
        )
        if local:
            paths.append(_Path(local))
    if not paths:
        send(chat_id, "❌ Не удалось скачать фото из альбома.")
        return True
    _swapbatch_apply_reply(
        chat_id, handler.consume_targets_album(chat_id_int, paths)
    )
    return True


def _largest_photo_unique_id(msg: Dict[str, Any]) -> Optional[str]:
    """Return the largest PhotoSize's ``file_unique_id`` for a photo message.

    ``file_unique_id`` is stable per physical media across redeliveries and
    across a differing ``file_id``, so it is the correct dedupe key (B-51).
    Returns ``None`` when the message carries no photo.
    """
    photos = msg.get("photo") or []
    if not photos:
        return None
    largest = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
    return largest.get("file_unique_id")


def _buffer_media_group_msg(
    media_group_buffer: Dict[str, Any], media_gid: str, msg: Dict[str, Any]
) -> None:
    """Append ``msg`` to the media_group buffer, deduping by file_unique_id.

    B-51 §5.1 primary fix: the Telegram transport can deliver the same album
    photo more than once (mid-flush re-flush, update redelivery, or the same
    media under a fresh ``file_id``). Keying on the stable ``file_unique_id``
    drops the duplicate at the source. A photo with no resolvable unique id is
    always kept (it cannot be deduped).
    """
    now = time.time()
    uid = _largest_photo_unique_id(msg)
    uid_str = uid if uid is not None else "(no file_unique_id)"
    chat_id = msg.get("chat", {}).get("id", "")

    # B-51 diagnostics. Capture transport-level transitions BEFORE mutating the
    # buffer so timing/gap reflect the pre-arrival state.
    if media_gid in media_group_buffer:
        # Point 4: inter-photo gap within the same album.
        gap = now - media_group_buffer[media_gid].get("last_seen", now)
        logger.info(
            "media_group: photo arrived chat=%s gap_since_last=%.2fs",
            chat_id, gap,
        )
    else:
        # Point 5: a new media_group_id while a previous group is still
        # buffered → Telegram may have split one album into multiple groups.
        others = [g for g in media_group_buffer if g != media_gid]
        if others:
            prev_size = sum(
                len(media_group_buffer[g].get("msgs", [])) for g in others
            )
            logger.info(
                "media_group: NEW media_group_id chat=%s previous=%s new=%s "
                "previous_size=%s",
                chat_id, ",".join(others), media_gid, prev_size,
            )

    buf = media_group_buffer.setdefault(
        media_gid,
        {"msgs": [], "seen_uids": set(), "first_seen": now, "last_seen": now},
    )
    if uid is None or uid not in buf["seen_uids"]:
        if uid is not None:
            buf["seen_uids"].add(uid)
        buf["msgs"].append(msg)
        # Point 2 (accepted).
        logger.info(
            "media_group: accepted photo chat=%s file_unique_id=%s (new)",
            chat_id, uid_str,
        )
    else:
        # Point 2 (rejected as duplicate).
        logger.info(
            "media_group: SKIPPED duplicate photo chat=%s file_unique_id=%s "
            "(already in buffer)",
            chat_id, uid_str,
        )
    buf["last_seen"] = now
    # Point 1: post-dedupe arrival summary.
    logger.info(
        "media_group: received photo chat=%s media_group_id=%s "
        "file_unique_id=%s buffer_size_after=%s",
        chat_id, media_gid, uid_str, len(buf["msgs"]),
    )


def _flush_media_group(media_group_buffer: Dict[str, Any], gid: str) -> None:
    """Pop one buffered media group and route it (album → swapbatch, else
    per-file).

    B-51 §5.2 hardening: the group is **popped before** processing, so a
    mid-flush exception (e.g. a download failure inside
    ``_swapbatch_album_intercept``) can no longer leave it buffered for a
    duplicate re-flush on the next poll-loop iteration.
    """
    entry = media_group_buffer.pop(gid)
    msgs = entry["msgs"]
    # Point 3: flush trigger. Timing fields are read defensively (.get) so a
    # hand-built buffer without first_seen/last_seen still logs cleanly.
    now = time.time()
    first_seen = entry.get("first_seen")
    last_seen = entry.get("last_seen")
    flush_chat = str(msgs[0].get("chat", {}).get("id", "")) if msgs else ""
    logger.info(
        "media_group: flushing chat=%s media_group_id=%s photo_count=%s "
        "elapsed_since_first=%.2fs elapsed_since_last=%.2fs",
        flush_chat, gid, len(msgs),
        (now - first_seen) if first_seen is not None else 0.0,
        (now - last_seen) if last_seen is not None else 0.0,
    )
    if not msgs:
        return
    chat_id = str(msgs[0].get("chat", {}).get("id", ""))
    if chat_id != ALLOWED_CHAT_ID:
        return
    # Block M.2.5: route to swapbatch if session waiting.
    if not _swapbatch_album_intercept(chat_id, msgs):
        state = load_state()
        caption = next((m.get("caption", "") for m in msgs if m.get("caption")), "")
        send(chat_id, f"📦 Получено {len(msgs)} файлов{' с подписью: ' + caption if caption else ''}. Обрабатываю...")
        for m in msgs:
            _handle_file_message(chat_id, m, state)


def backend_get(path: str, timeout: int = 60) -> Dict[str, Any]:
    return http_json("GET", BACKEND + path, timeout=timeout)


def backend_post(path: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    return http_json("POST", BACKEND + path, payload, timeout=timeout)


def pretty(obj: Any, limit: int = 3500) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text[:limit] + ("\n...обрезано" if len(text) > limit else "")


def norm(text: str) -> str:
    return (text or "").strip()


def low(text: str) -> str:
    return norm(text).lower()


def remember_preferences(text: str, state: Dict[str, Any]) -> Optional[str]:
    t = low(text)

    if any(w in t for w in ["запомни", "запомнить", "запиши", "сохрани правило", "всегда"]):
        changed = []

        if "русск" in t or "на русском" in t:
            state["language"] = "ru"
            state["table_language"] = "ru"
            state["preferences"]["answer_language"] = "ru"
            state["preferences"]["tables_language"] = "ru"
            changed.append("ответы и таблицы — на русском")

        if "назван" in t and ("не переводи" in t or "оригинал" in t or "кроме назван" in t):
            state["keep_names_original"] = True
            state["preferences"]["names_original"] = True
            changed.append("названия оставлять в оригинале")

        if "корот" in t:
            state["preferences"]["short_status"] = True
            changed.append("статусы делать краткими")

        save_state(state)

        if changed:
            return "✅ Запомнил правило: " + "; ".join(changed) + "."
        return "✅ Запомнил. Буду учитывать это в следующих задачах."

    return None


def apply_language(query: str, state: Dict[str, Any]) -> str:
    prefs = state.get("preferences") or {}
    lang = prefs.get("tables_language", "ru")
    names_original = prefs.get("names_original", True)

    additions = []
    if lang == "ru":
        additions.append("Ответ и таблицу сделай на русском языке")
    if names_original:
        additions.append("Названия сервисов, компаний, моделей и продуктов оставляй в оригинале")
    additions.append("Добавь источники/ссылки, если они доступны")
    additions.append("Сделай результат полезным для принятия решения")

    return query + ". " + ". ".join(additions)


def detect_command(text: str) -> Optional[Dict[str, Any]]:
    raw = norm(text)
    if not raw.startswith("/"):
        return None
    cmd = raw.split(" ", 1)[0].lower()
    rest = raw.split(" ", 1)[1].strip() if " " in raw else ""
    return {"intent": "command", "command": cmd, "query": rest}


# === Identity Guard (Phase 5) ===
# Mirror values from app/services/identity_core.py
# TODO Phase 8: import from identity_core if bot becomes part of main app
JARVIS_NAME = "Jarvis V3 Supervisor"
JARVIS_OWNER = "Daniil"
JARVIS_VERSION = "3.0"

IDENTITY_TRIGGERS = [
    # Вопросы об identity (прямые — не статус)
    "как тебя зовут", "как тебя называть", "твоё имя", "твое имя",
    "кто ты", "ты кто", "кто ты такой",
    "что ты за", "что ты такое",
    "представься",
    "ты jarvis", "ты джарвис",
    "ты perplexity", "ты gpt", "ты chatgpt", "ты claude",
    # English equivalents
    "who are you", "what are you", "your name",
    "introduce yourself",
    # Presence/status checks moved to _STATUS_PATTERNS in classify_message:
    # "ты тут", "ты онлайн", "ты работаешь", "живой" etc.
]


def identity_answer() -> str:
    return (
        f"Я — {JARVIS_NAME}, локальный AI-оператор {JARVIS_OWNER}.\n"
        f"Версия {JARVIS_VERSION}.\n\n"
        "Я НЕ Perplexity и НЕ Luxify — я твой собственный AI.\n\n"
        "Напиши «что ты умеешь?» — покажу полный список возможностей."
    )
# === /Identity Guard ===


# === Greeting & Small Talk (Phase 8) ===
GREETING_TRIGGERS = [
    # Russian greetings
    "привет", "приветствую", "хай", "здарова", "здравствуй", "здравствуйте",
    "доброе утро", "добрый день", "добрый вечер", "добрый", "доброй ночи",
    "йо", "ку", "куку", "хола", "алло", "алё",
    "что нового", "как сам", "как ты", "как дела",
    # English greetings
    "hi", "hello", "hey", "hola", "howdy",
    "good morning", "good afternoon", "good evening", "good day",
    "sup", "yo", "greetings",
    # Casual
    "ого", "ну привет",
]

SMALL_TALK_TRIGGERS = [
    # Russian thanks/ack
    "спасибо", "благодарю", "спс", "сенкс", "благодарен",
    "ок", "окей", "ладно", "понял", "понятно", "ясно", "ясненько",
    "отлично", "хорошо", "круто", "супер", "огонь", "класс",
    "извини", "прости", "извините",
    # English acks
    "thanks", "thx", "thank you", "ty",
    "ok", "okay", "got it", "understood", "cool", "great", "nice",
    "sorry", "my bad",
    # Dismissals
    "ничего", "всё нормально", "не важно", "забудь",
]

SMALL_TALK_MAP = {
    ("спасибо", "благодарю", "спс", "сенкс", "благодарен", "thanks", "thx", "thank you", "ty"): "Всегда пожалуйста! 😊",
    ("ок", "окей", "ok", "okay", "got it", "understood", "понял", "понятно", "ясно", "ясненько"): "✅",
    ("отлично", "хорошо", "круто", "супер", "огонь", "класс", "cool", "great", "nice"): "Рад стараться! 🚀",
    ("извини", "прости", "извините", "sorry", "my bad"): "Всё в порядке! Чем могу помочь?",
    ("ничего", "всё нормально", "не важно", "забудь"): "Понял, жду следующего запроса.",
}


def greeting_answer() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        prefix = "Доброе утро"
    elif 12 <= hour < 18:
        prefix = "Добрый день"
    elif 18 <= hour < 23:
        prefix = "Добрый вечер"
    else:
        prefix = "Привет"
    return (
        f"{prefix}, {JARVIS_OWNER}! Чем могу помочь?\n\n"
        "Напиши «что ты умеешь?» — покажу все возможности."
    )


def small_talk_answer(text: str) -> str:
    t = low(text)
    for triggers, reply in SMALL_TALK_MAP.items():
        if any(trigger in t for trigger in triggers):
            return reply
    return "✅"

# === /Greeting & Small Talk ===


# === Capability Registry (Phase 6) ===
# Mirrors what the backend actually supports.
# TODO Phase 8: replace AVAILABLE/PARTIAL with live env-var checks from identity_core
CAPABILITY_STATUS_AVAILABLE = "AVAILABLE"
CAPABILITY_STATUS_PARTIAL = "PARTIAL"
CAPABILITY_STATUS_UNAVAILABLE = "UNAVAILABLE"

CAPABILITIES = [
    # AI Providers
    {"id": "openai",        "category": "ai_provider", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "OpenAI (gpt-4.1 / gpt-4o-mini)", "note": "диалог, классификация, reasoning"},
    {"id": "anthropic",     "category": "ai_provider", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Anthropic Claude (claude-sonnet-4-5)", "note": "архитектура, код, engineering"},
    {"id": "ollama",        "category": "ai_provider", "status": CAPABILITY_STATUS_PARTIAL,
     "label": "Ollama (llama3.2)", "note": "локальный fallback — требует запущенный сервер"},
    {"id": "perplexity",    "category": "ai_provider", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Perplexity sonar-pro", "note": "интернет-исследование с цитатами"},
    {"id": "tavily",        "category": "ai_provider", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Tavily", "note": "веб-поиск, структурированные результаты"},
    # Tools
    {"id": "internet_research", "category": "tool", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "интернет-исследование", "note": "Perplexity → ответ + источники"},
    {"id": "table_excel",       "category": "tool", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "таблицы Excel/CSV", "note": "данные из поиска → файл прямо в Telegram"},
    {"id": "ai_engineer",       "category": "tool", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "AI-инженер", "note": "архитектура, риски, безопасный план внедрения"},
    {"id": "brain_plan",        "category": "tool", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "brain планирование", "note": "анализ задачи + выбор инструмента"},
    # Content generation
    {"id": "image_gen",     "category": "content", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "генерация изображений", "note": "Replicate FLUX 1.1 Pro, 9:16, до 4 в batch"},
    {"id": "video_gen",     "category": "content", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "генерация видео", "note": "Kling-3 через InfluencerStudio"},
    # Photo Studio (Block H3-H4)
    {"id": "restaurant_mode",  "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "фото блюд (/menu_photo)", "note": "4 стиля: rustic, modern, dark, instagram — FLUX Pro"},
    {"id": "party_mode",       "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "постеры вечеринок (/party_promo)", "note": "8 тем: NYE, Halloween, Birthday и др., 9:16"},
    {"id": "invite_cards",     "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "персональные приглашения (/invite_card)", "note": "имя гостя + событие + дата"},
    {"id": "face_swap",        "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "замена лиц (/faceswap)", "note": "cdingram basic + codeplugtech polish + GFPGAN"},
    {"id": "photo_enhance",    "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "улучшение фото (/enhance)", "note": "GFPGAN v1.4 — лица + детали"},
    {"id": "lora_training",    "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "личная LoRA (/lora_train)", "note": "обучение модели на твоих фото, $10 единоразово"},
    {"id": "personal_mode",    "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "личные сцены (/me_as, /me_in, /me_style)", "note": "себя в ролях / местах / стилях через LoRA"},
    {"id": "smart_photo_router", "category": "photo_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Smart Photo Router", "note": "auto-detect pipeline + composite workflows"},
    # Night Autonomy (Block H5)
    {"id": "night_autonomy",   "category": "autonomy", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "ночная автономия", "note": "5-фазный движок: recap + content + self-improvement + trends"},
    {"id": "self_improvement", "category": "autonomy", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "self-improvement", "note": "A/B тесты + обучение на ошибках через LoRA-эксперименты"},
    {"id": "auto_content",     "category": "autonomy", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "auto content", "note": "генерация завтрашних постов пока ты спишь"},
    {"id": "trend_analyzer",   "category": "autonomy", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Trend Analyzer", "note": "анализ трендов индустрии + Obsidian экспорт"},
    # Integrations
    {"id": "telegram",      "category": "integration", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Telegram", "note": "приём команд, отправка файлов"},
    {"id": "google_drive",  "category": "integration", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Google Drive", "note": "загрузка контента, ссылки на папки"},
    {"id": "google_sheets", "category": "integration", "status": CAPABILITY_STATUS_PARTIAL,
     "label": "Google Sheets / Workspace", "note": "OAuth настроен, требует запроса"},
    {"id": "obsidian",      "category": "integration", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Obsidian Vault", "note": "экспорт заметок миссий в Markdown"},
    {"id": "n8n_cloud",     "category": "integration", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "n8n Cloud", "note": "daniliyc.app.n8n.cloud, webhook + API"},
    {"id": "n8n_local",     "category": "integration", "status": CAPABILITY_STATUS_PARTIAL,
     "label": "n8n Self-hosted", "note": "localhost:5678, требует запущенный контейнер"},
    # Design Studio (Block F)
    {"id": "design",      "category": "design_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Figma design brief (/design)", "note": "Claude AI generates detailed brief + Figma MCP creates real design"},
    {"id": "landing",     "category": "design_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "HTML landing page (/landing)", "note": "Tailwind CSS, Header+Hero+Features+Pricing+Footer"},
    {"id": "landing_brief", "category": "design_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Landing Brief 2.0 (/landing_brief)", "note": "8-step brief + Claude generates unique content + 5 styles"},
    {"id": "simple_game", "category": "design_studio", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "HTML5 игры (/simple_game)", "note": "Snake, Tic-Tac-Toe, Memory, 2048 — готовы в браузере"},
    # AI App Builder (Block L)
    {"id": "create_app",  "category": "ai_builder", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "bolt.diy app spec (/create_app)", "note": "Claude AI generates full app spec + bolt.diy prompt"},
    {"id": "smart_photo", "category": "ai_photo", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Smart photo prompts (/smart_photo)", "note": "Claude AI enhances prompts to professional photographer level"},
    {"id": "pro_food",    "category": "ai_photo", "status": CAPABILITY_STATUS_AVAILABLE,
     "label": "Food photography (/pro_food)", "note": "Specialized food photography prompts by Jonathan Lovekin style"},
]

CAPABILITY_TRIGGERS = [
    # Questions about what Jarvis works with
    "с какими агентами", "с каким агентом", "какие агенты", "агентами работ",
    "твои агенты", "твоих агентов", "ваши агенты", "ваших агентов",
    "с каким ai", "с какими ai", "с каким ии", "с какими ии",
    "какие провайдеры", "с какими провайдерами",
    # Questions about content
    "какой контент", "что создаёшь", "что генерируешь",
    # Questions about integrations / tools
    "какие интеграции", "с чем интегрирован", "что подключено",
    "какие инструменты", "твои инструменты", "твоих инструментов",
    "у тебя инструменты", "у тебя есть инструменты",
    # General capability questions about Jarvis specifically
    "что есть у тебя", "что у тебя есть",
    "какие у тебя возможности", "твои возможности", "твои функции",
    # "ты умеешь" without specific object → capabilities overview
    # (specific object handled by CAN_YOU_TRIGGERS below)
    # English equivalents
    "what can you", "what do you do",
]

# Triggers for "can Jarvis do X?" — routes to can_you intent
CAN_YOU_TRIGGERS = [
    "ты умеешь", "ты можешь", "можешь ли", "умеешь ли",
    "сможешь ли", "способен ли",
    "can you", "are you able", "do you support", "do you handle",
]

# Keywords that map roughly to CAPABILITIES ids for semantic matching
_CAPABILITY_KEYWORDS: Dict[str, str] = {
    # table / excel
    "таблиц": "table_excel", "excel": "table_excel", "xlsx": "table_excel",
    "csv": "table_excel", "spreadsheet": "table_excel",
    # research
    "исследован": "internet_research", "поиск": "internet_research",
    "интернет": "internet_research", "research": "internet_research",
    # engineering
    "код": "ai_engineer", "архитектур": "ai_engineer",
    "интеграц": "ai_engineer", "пайплайн": "ai_engineer",
    # image / video
    "изображени": "image_gen", "картинк": "image_gen",
    "image": "image_gen",
    "видео": "video_gen", "video": "video_gen",
    # brain / planning
    "план": "brain_plan", "анализ": "brain_plan", "мыслить": "brain_plan",
    # google / drive
    "google": "google_drive", "drive": "google_drive",
    # n8n / automation
    "n8n": "n8n_cloud", "автомат": "n8n_cloud", "webhook": "n8n_cloud",
    # obsidian
    "obsidian": "obsidian", "заметк": "obsidian",
    # Photo Studio (Block H3-H4)
    "блюд": "restaurant_mode", "меню": "restaurant_mode", "ресторан": "restaurant_mode",
    "menu_photo": "restaurant_mode", "food photo": "restaurant_mode",
    "вечеринк": "party_mode", "постер": "party_mode", "party": "party_mode",
    "приглашени": "invite_cards", "invite": "invite_cards",
    "face swap": "face_swap", "faceswap": "face_swap", "замен": "face_swap",
    "face": "face_swap", "лицо": "face_swap",
    "улучши": "photo_enhance", "enhance": "photo_enhance",
    "lora": "lora_training", "лора": "lora_training", "обуч": "lora_training",
    "me as": "personal_mode", "me in": "personal_mode", "me style": "personal_mode",
    "личн": "personal_mode", "себя": "personal_mode",
    "фото": "restaurant_mode", "photo": "restaurant_mode",
    # Night Autonomy (Block H5)
    "ночн": "night_autonomy", "night": "night_autonomy", "автоном": "night_autonomy",
    "самоулучш": "self_improvement", "self-improv": "self_improvement",
    "самообуч": "self_improvement", "a/b": "self_improvement",
    "контент": "auto_content", "content": "auto_content",
    "тренд": "trend_analyzer", "trend": "trend_analyzer",
}


def can_you_answer(query: str) -> str:
    """Answer 'can you do X?' using only CAPABILITY_REGISTRY — never Perplexity."""
    t = low(query)

    # Strip the can-you trigger to get the "X" part
    for trigger in CAN_YOU_TRIGGERS:
        if trigger in t:
            subject = t.split(trigger, 1)[1].strip(" ?.,")
            break
    else:
        subject = t

    # If subject is empty or just punctuation → show capabilities overview
    if not subject or len(subject) < 2:
        return capabilities_text()

    # Check subject against capability keywords
    matched_cap_id = None
    for kw, cap_id in _CAPABILITY_KEYWORDS.items():
        if kw in subject:
            matched_cap_id = cap_id
            break

    if matched_cap_id:
        cap = next((c for c in CAPABILITIES if c["id"] == matched_cap_id), None)
        if cap:
            return (
                f"✅ Да, я умею: {cap['label']}\n\n"
                f"Детали: {cap['note']}\n\n"
                f"Напиши «что ты умеешь?» — покажу полный список возможностей."
            )

    # Subject didn't match any known capability
    # Special cases: web dev (not in registry)
    web_keywords = ["сайт", "website", "react", "html", "css", "wordpress",
                    "tilda", "фронтенд", "frontend", "верстк"]
    if any(kw in subject for kw in web_keywords):
        return (
            "❌ Нет, создание сайтов не входит в мои текущие возможности.\n\n"
            "Я умею:\n"
            "• Исследовать тему через интернет (Perplexity)\n"
            "• Создавать таблицы Excel/CSV\n"
            "• Запускать AI-инженера для архитектурных задач\n"
            "• Генерировать изображения и видео\n\n"
            "Напиши «что ты умеешь?» — покажу полный список."
        )

    # Generic unknown capability
    return (
        f"❓ «{subject}» — не уверен что это входит в мои текущие возможности.\n\n"
        f"Точно умею:\n"
        f"• Интернет-исследования и таблицы Excel\n"
        f"• AI-инженер: архитектура, код, планы\n"
        f"• Генерация изображений и видео\n"
        f"• Brain-планирование задач\n\n"
        "Напиши «что ты умеешь?» для полного списка."
    )

# === /Capability Registry ===


def classify_message(text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    raw = norm(text)
    t = low(raw)

    cmd = detect_command(raw)
    if cmd:
        return cmd

    if not raw:
        return {"intent": "empty"}

    pref_answer = remember_preferences(raw, state)
    if pref_answer:
        return {"intent": "preference_saved", "message": pref_answer}

    pending = state.get("pending")
    if pending and pending.get("type") == "table_clarification":
        return {
            "intent": "table",
            "query": pending.get("base_query", "") + " " + raw,
            "from_clarification": True,
        }

    # Greeting short-circuit (Phase 8): exact/prefix match, but only if no identity trigger present
    _has_identity = any(trigger in t for trigger in IDENTITY_TRIGGERS)
    if not _has_identity:
        if len(raw) <= 40 and any(
            t == trigger
            or t.startswith(trigger + " ")
            or t.startswith(trigger + ",")
            or t == trigger + "!"
            or t == trigger + "?"
            for trigger in GREETING_TRIGGERS
        ):
            return {"intent": "greeting"}
        if any(t == trigger for trigger in GREETING_TRIGGERS):
            return {"intent": "greeting"}

    # Small talk (Phase 8): no identity trigger, short message
    if not _has_identity and len(raw) <= 60 and any(
        t == trigger or t.startswith(trigger) for trigger in SMALL_TALK_TRIGGERS
    ):
        return {"intent": "small_talk"}

    # Continuation detection (Phase 10)
    _continuation_triggers = [
        "и что", "продолжай", "продолжи", "ещё", "еще", "детали",
        "расскажи больше", "подробнее", "а потом", "что дальше",
        "continue", "more details", "go on",
    ]
    if len(raw) <= 40 and any(t == tr or t.startswith(tr) for tr in _continuation_triggers):
        return {"intent": "continuation"}

    if any(x in t for x in ["что ты умеешь", "что умеешь", "твои возможности", "какие у тебя функции", "помощь", "расскажи что ты умеешь"]):
        return {"intent": "capabilities"}

    # Capability questions (Phase 6): what providers / content / integrations
    if any(trigger in t for trigger in CAPABILITY_TRIGGERS):
        return {"intent": "capabilities"}

    # "Can you do X?" / "Ты умеешь X?" — identity-safe capability check (Phase 6.5)
    # Must come BEFORE research catch-all to prevent Perplexity identity drift
    if any(trigger in t for trigger in CAN_YOU_TRIGGERS):
        return {"intent": "can_you", "query": raw}

    # File-related intents (Phase 13)
    file_triggers = {
        "summarize_file": [
            "суммируй файл", "кратко про файл", "что в файле", "summarize file",
            "о чём файл", "о чем файл", "перескажи файл", "расскажи про файл",
            "что за файл", "проанализируй файл", "прочитай файл",
        ],
        "extract_from_file": [
            "извлеки данные", "достань данные", "вытащи", "extract from",
            "счета из файла", "суммы из файла", "даты из файла",
            "извлеки из файла", "данные из файла",
        ],
        "ask_about_file": [
            "вопрос по файлу", "спроси у файла", "найди в файле",
            "в этом файле", "из этого файла", "содержит ли файл",
        ],
        "accounting": [
            "бухгалтер", "счёт-фактур", "накладная", "платёжка",
            "приход", "расход", "счета и расходы", "финансовый отчёт",
        ],
    }
    if state.get("last_uploaded_file"):
        for file_intent, ftriggers in file_triggers.items():
            if any(tr in t for tr in ftriggers):
                return {"intent": file_intent, "query": raw}

    if any(x in t for x in ["статус", "здоровье системы", "системы работают", "проверить системы"]):
        return {"intent": "health"}

    if re.search(r"\b(job_[0-9_]+)\b", raw):
        return {"intent": "job", "query": re.search(r"\b(job_[0-9_]+)\b", raw).group(1)}

    # Follow-up: translate/rebuild last table
    if any(x in t for x in ["переведи", "на русский", "новую таблицу", "пришли новую таблицу"]) and state.get("last_table_query"):
        return {
            "intent": "table",
            "query": state["last_table_query"] + " Пересобери таблицу на русском языке, названия оставь в оригинале.",
        }

    table_triggers = [
        "таблиц", "excel", "xlsx", "csv", "сравнительную таблицу",
        "оформи в таблицу", "создай таблицу", "сделай таблицу",
        "пришли таблицу", "файл таблиц"
    ]

    research_triggers = [
        "найди", "поищи", "кто такой", "что такое", "расскажи про",
        "из интернета", "актуальн", "форум", "отзывы", "обзор"
    ]

    compare_triggers = [
        "сравни", "топ", "лучшие", "рейтинг", "best", "compare",
        "какой лучше", "что лучше"
    ]

    # H8.2 / H9.2: Status questions — presence + operational checks
    _STATUS_PATTERNS = [
        "ты работа", "ты жив", "ты активн",
        "ты онлайн", "ты тут", "ты здесь",
        "живой", "джарвис тут",
        "как успехи самоулучшени", "как поживаеш",
        "are you working", "are you alive",
        "are you there", "are you online",
        "бот работает", "бот жив", "бот онлайн",
    ]
    if any(p in t for p in _STATUS_PATTERNS):
        return {"intent": "self_status"}

    # H8.2: Action commands — real system operations (always check, no identity ambiguity)
    _ACTION_PATTERNS = {
        "перезагрузи бэкенд": "restart_backend",
        "перезагрузи backend": "restart_backend",
        "перезапусти бэкенд": "restart_backend",
        "restart backend": "restart_backend",
        "перезагрузи бот": "restart_bot",
        "перезапусти бот": "restart_bot",
        "restart bot": "restart_bot",
        "перезагрузи себя": "restart_bot",
    }
    for pattern, action in _ACTION_PATTERNS.items():
        if pattern in t:
            return {"intent": "action", "action": action}

    # H8.2: Progress/meta questions — read from logs, not AI Engineer
    _PROGRESS_PATTERNS = [
        "что ты сделал", "что выполнил", "что сделано",
        "задачи выполнены", "self-improvement сделан",
        "успехи самоулучшения", "что за ночь", "ночной отчёт",
        "что за сегодня", "итоги дня",
    ]
    if any(p in t for p in _PROGRESS_PATTERNS):
        return {"intent": "progress_report"}

    engineer_triggers = [
        "интегр", "архитектур", "реализ", "пайплайн", "api",
        "ошибк", "исправ", "улучш", "доработ", "код", "блок"
    ]

    gen_triggers = [
        "сгенерируй", "создай картинку", "изображение", "photo",
        "image", "картинку", "нарисуй"
    ]

    # Strong image generation triggers — checked BEFORE compound_task/brain routing
    _strong_image_patterns = [
        r'\bсделай\s+\d*\s*фот',
        r'\bсделай\s+картинк',
        r'\bсделай\s+\d*\s*изображени',
        r'\bсоздай\s+\d*\s*фот',
        r'\bсоздай\s+картинк',
        r'\bсоздай\s+\d*\s*изображени',
        r'\bнарисуй',
        r'\bсгенерируй\s+(?:фот|картинк|изображен)',
        r'\bgenerate\s+(?:\w+\s+){0,3}(?:photo|image|picture)',
        r'\bcreate\s+(?:\w+\s+){0,3}(?:photo|image|picture)',
        r'\bmake\s+(?:\w+\s+){0,3}(?:photo|image|picture)',
        r'\bdraw\s+',
    ]
    for _pat in _strong_image_patterns:
        if re.search(_pat, t, re.IGNORECASE):
            return {"intent": "generate", "query": raw}

    # Compound task detection (Phase 14) — must run before single-intent triggers
    try:
        _root14 = str(Path(__file__).parent.parent)
        import sys as _sys14
        if _root14 not in _sys14.path:
            _sys14.path.insert(0, _root14)
        from app.services.task_planner import is_compound_task as _is_compound
        if _is_compound(raw):
            return {"intent": "compound_task", "query": raw}
    except Exception:
        pass

    if any(x in t for x in table_triggers):
        return {"intent": "table", "query": raw}

    if any(x in t for x in gen_triggers):
        return {"intent": "generate", "query": raw}

    if any(x in t for x in engineer_triggers):
        return {"intent": "engineer", "query": raw}

    # Phase 23: Simple factual questions → quick Claude Haiku answer (no Perplexity)
    try:
        _root23 = str(Path(__file__).parent.parent)
        import sys as _sys23
        if _root23 not in _sys23.path:
            _sys23.path.insert(0, _root23)
        from app.services.quick_answer import is_simple_question as _is_simple
        if _is_simple(raw):
            return {"intent": "simple_question", "query": raw}
    except Exception:
        pass

    if any(x in t for x in research_triggers):
        return {"intent": "research", "query": raw}

    if any(x in t for x in compare_triggers):
        return {"intent": "brain", "query": raw}

    # Identity short-circuit (Phase 5)
    if any(trigger in t for trigger in IDENTITY_TRIGGERS):
        return {"intent": "identity"}

    if raw.endswith("?") or t.startswith(("почему", "как ", "зачем", "можно ли", "правда ли")):
        # Phase 23: catch-all question → check simple first
        try:
            from app.services.quick_answer import is_simple_question as _is_simple2
            if _is_simple2(raw):
                return {"intent": "simple_question", "query": raw}
        except Exception:
            pass
        return {"intent": "research", "query": raw}

    if len(raw) > 70:
        return {"intent": "brain", "query": raw}

    return {"intent": "chat", "query": raw}


def needs_table_clarification(query: str) -> Optional[str]:
    t = low(query)
    vague = [
        "в каждой отрасли",
        "по отраслям",
        "лучших ai в каждой",
        "лучшие ai в каждой",
        "топ 10 лучших ai",
    ]
    if any(v in t for v in vague) and not any(x in t for x in [
        "генерация изображений", "видео", "голос", "агенты", "автоматизация",
        "маркетинг", "продажи", "недвижимость", "финансы", "туризм",
        "первой", "второй", "1", "2"
    ]):
        return (
            "Уточни, пожалуйста, какие отрасли брать.\n\n"
            "1) AI-сервисы: генерация изображений, видео, голос, агенты, автоматизация, дизайн, кодинг, маркетинг, аналитика, продажи\n"
            "2) Бизнес-ниши: маркетинг, продажи, недвижимость, финансы, туризм, e-commerce, образование, медицина, HR, производство\n"
            "3) Инвестиции/крипта/стартапы\n\n"
            "Можешь написать: «первая и вторая категории»."
        )
    return None


def expand_categories(query: str) -> str:
    t = low(query)
    if "перв" in t and "втор" in t:
        return (
            query +
            " Категории: AI-сервисы: генерация изображений, видео, голос, AI агенты, автоматизация, дизайн, кодинг, маркетинг, аналитика, продажи. "
            "Бизнес-ниши: маркетинг, продажи, недвижимость, финансы, туризм, e-commerce, образование, медицина, HR, производство."
        )
    return query


def get_health() -> Dict[str, Any]:
    data = {}
    for name, path in [
        ("root", "/health"),
        ("internet", "/api/jarvis/tools/internet/health"),
        ("brain", "/api/jarvis/brain/health"),
        ("ai_engineer", "/api/jarvis/ai-engineer/health"),
        ("telegram_tools", "/api/jarvis/telegram-tools/health"),
        ("content_async", "/api/jarvis/v5/content-factory/async-health"),
    ]:
        try:
            data[name] = backend_get(path)
        except Exception as e:
            data[name] = {"ok": False, "error": str(e)}
    return data


def human_health(data: Dict[str, Any]) -> str:
    def is_ok(k: str) -> str:
        v = data.get(k, {})
        return "OK" if v.get("ok") is True or v.get("status") == "healthy" else "ERR"

    return (
        "✅ Системы Jarvis:\n"
        f"- Backend: {data.get('root', {}).get('status', 'unknown')}\n"
        f"- Internet tools: {is_ok('internet')}\n"
        f"- Brain layer: {is_ok('brain')}\n"
        f"- AI Engineer: {is_ok('ai_engineer')}\n"
        f"- Таблицы/файлы Telegram: {is_ok('telegram_tools')}\n"
        f"- Async генерация: {is_ok('content_async')}\n\n"
        "Для полного JSON: /debug_health"
    )


def capabilities_text() -> str:
    cat_order = ["ai_provider", "tool", "content", "photo_studio", "autonomy", "integration"]
    cat_icons = {
        "ai_provider":   "🧠",
        "tool":          "🔧",
        "content":       "🎨",
        "photo_studio":  "📸",
        "autonomy":      "🌙",
        "integration":   "🔗",
    }
    cat_labels = {
        "ai_provider":   "AI провайдеры",
        "tool":          "Инструменты",
        "content":       "Генерация контента",
        "photo_studio":  "Photo Studio (H3-H4)",
        "autonomy":      "Night Autonomy (H5)",
        "integration":   "Интеграции",
    }
    status_icon = {
        CAPABILITY_STATUS_AVAILABLE:   "✅",
        CAPABILITY_STATUS_PARTIAL:     "⚠️",
        CAPABILITY_STATUS_UNAVAILABLE: "❌",
    }

    by_cat: Dict[str, list] = {cat: [] for cat in cat_order}
    for cap in CAPABILITIES:
        cat = cap["category"]
        if cat in by_cat:
            by_cat[cat].append(cap)

    lines = [f"Что умеет {JARVIS_NAME}:\n"]
    for cat in cat_order:
        caps = by_cat[cat]
        lines.append(f"{cat_icons[cat]} {cat_labels[cat]}:")
        for c in caps:
            icon = status_icon.get(c["status"], "?")
            lines.append(f"  {icon} {c['label']} — {c['note']}")
        lines.append("")

    lines.append("Примеры: «Сравни FAL и Replicate», «Создай таблицу топ AI»")
    lines.append("Для деталей: /smart_help")
    return "\n".join(lines)


def _handle_mesh_task(chat_id: str, query: str, state: Dict[str, Any]) -> None:
    """Execute compound task through Smart Router (Phase 16 Agent Mesh)."""
    _root = str(Path(__file__).parent.parent)
    import sys as _sys
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

    try:
        from app.services.smart_router import analyze_task, select_agents, build_execution_plan, execute_plan as mesh_execute

        send(chat_id, "🧠 Анализирую задачу...")
        req = analyze_task(query)
        agents = select_agents(req)
        plan = build_execution_plan(query, agents)

        # Save plan for /mesh debug
        state["last_mesh_plan"] = plan.summary()
        mesh_history = state.get("mesh_history", [])
        mesh_history.append({"query": query[:80], "agents": ", ".join(agents)})
        state["mesh_history"] = mesh_history[-20:]
        save_state(state)

        send(chat_id, plan.summary())
        send(chat_id, "🚀 Выполняю...")

        result = mesh_execute(plan, send_progress=lambda txt: send(chat_id, txt))
        final = result.get("plan_result") or "Выполнение завершено."
        send(chat_id, f"✨ Готово!\n\n{final}")

    except Exception as e:
        send(chat_id, f"❌ Smart Router ошибка: {translate_exception(e)}\nПробую стандартный планировщик...")
        _handle_compound_task(chat_id, query, state)


def _handle_compound_task(
    chat_id: str, query: str, state: Dict[str, Any], plan_only: bool = False
) -> None:
    """Decompose and execute (or just show) a multi-step task."""
    _root = str(Path(__file__).parent.parent)
    import sys as _sys
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from app.services.task_planner import decompose_task
    from app.services.telegram_task_executor import execute_plan

    plan = decompose_task(query)
    if not plan.steps:
        send(chat_id, "❌ Не смог разбить задачу на шаги.")
        return

    if plan_only:
        send(chat_id, plan.summary())
        return

    def _run_step(step):
        result_pack = {"intent": step.intent, "query": step.query}
        collected = []
        orig_send = globals().get("send")

        def _capture(cid, txt, **kw):
            collected.append(txt)
            send(cid, txt, **kw)

        import tools.jarvis_smart_telegram_control as _self
        orig = _self.send
        _self.send = _capture
        try:
            run_intent(chat_id, result_pack, state)
        finally:
            _self.send = orig
        return " ".join(collected)[:800] if collected else "(нет ответа)"

    execute_plan(
        plan,
        run_step=_run_step,
        send_progress=lambda txt: send(chat_id, txt),
    )
    send(chat_id, "✅ Все шаги выполнены!\n\n" + plan.results_summary())


def handle_self_status(chat_id: str) -> None:
    """H8.2: Real status check — no AI Engine, only actual measurements."""
    status = []

    # Backend health
    try:
        resp = urllib.request.urlopen(f"{BACKEND}/health", timeout=5)
        if resp.status == 200:
            status.append("✅ Backend: online")
        else:
            status.append(f"⚠️ Backend: HTTP {resp.status}")
    except Exception as exc:
        status.append(f"❌ Backend: offline ({type(exc).__name__})")

    # Bot heartbeat
    if _HEARTBEAT_FILE.exists():
        try:
            last = int(_HEARTBEAT_FILE.read_text(encoding="utf-8").strip())
            age = int(time.time() - last)
            status.append(f"✅ Bot: alive (heartbeat {age}s ago)")
        except Exception:
            status.append("⚠️ Bot: heartbeat file unreadable")
    else:
        status.append("⚠️ Bot: no heartbeat file yet")

    # Replicate API configured
    if os.getenv("REPLICATE_API_KEY"):
        status.append("✅ Replicate: configured")
    else:
        status.append("⚠️ Replicate: not configured")

    # Anthropic API configured
    if os.getenv("ANTHROPIC_API_KEY"):
        status.append("✅ Anthropic: configured")
    else:
        status.append("⚠️ Anthropic: not configured")

    # Self-improvement log
    try:
        log_file = Path("state") / "improvement_log.json"
        if log_file.exists():
            import json as _j
            data = _j.loads(log_file.read_text(encoding="utf-8"))
            if data:
                last_entry = data[-1]
                last_date = last_entry.get("date") or last_entry.get("ts", "?")
                status.append(f"✅ Self-improvement: последний цикл {last_date}")
            else:
                status.append("⚠️ Self-improvement: лог пуст")
        else:
            status.append("⚠️ Self-improvement: никогда не запускался")
    except Exception:
        status.append("⚠️ Self-improvement: ошибка чтения лога")

    send(chat_id, "📊 Реальный статус:\n\n" + "\n".join(status))


def handle_progress_report(chat_id: str) -> None:
    """H8.2: Report actual progress from decision_log + improvement_log."""
    lines = ["📊 Что я сделал:\n"]

    # Count decisions today
    try:
        _root = str(Path(__file__).parent.parent)
        import sys as _sys_pr
        if _root not in _sys_pr.path:
            _sys_pr.path.insert(0, _root)
        from app.services.decision_log import get_recent_decisions
        from datetime import date as _date
        decisions = get_recent_decisions()
        today_str = _date.today().isoformat()
        today_decisions = [d for d in decisions if (d.get("timestamp") or "").startswith(today_str)]
        lines.append(f"✅ Решений сегодня: {len(today_decisions)}")
    except Exception as exc:
        lines.append(f"⚠️ Решений: ошибка ({exc})")

    # Self-improvement cycles
    try:
        log_file = Path("state") / "improvement_log.json"
        if log_file.exists():
            import json as _j2
            data = _j2.loads(log_file.read_text(encoding="utf-8"))
            lines.append(f"🧠 Self-improvement циклов: {len(data)}")
            if not data:
                lines.append("\n⚠️ Self-improvement loop ни разу не запускался!")
                lines.append("Запусти: /night_now")
        else:
            lines.append("🧠 Self-improvement: никогда не запускался")
            lines.append("\n⚠️ Запусти: /night_now")
    except Exception as exc:
        lines.append(f"⚠️ Self-improvement: ошибка ({exc})")

    # Night workflow last run
    try:
        night_log = Path("state") / "night_workflows" / "phase_log.jsonl"
        if night_log.exists():
            import json as _j3
            entries = [_j3.loads(l) for l in night_log.read_text(encoding="utf-8").splitlines() if l.strip()]
            if entries:
                last = entries[-1]
                lines.append(f"🌙 Night Workflow: последний запуск {last.get('ts', '?')} ({last.get('phase', '?')})")
        else:
            lines.append("🌙 Night Workflow: не запускался")
    except Exception:
        pass

    send(chat_id, "\n".join(lines))


def _try_log_decision(query: str, intent: str, start_time: float) -> Optional[str]:
    """Log routing decision, return decision_id or None on failure."""
    try:
        import time as _time
        _r = str(Path(__file__).parent.parent)
        import sys as _sys_dl
        if _r not in _sys_dl.path:
            _sys_dl.path.insert(0, _r)
        from app.services.decision_log import log_decision
        ms = round((_time.time() - start_time) * 1000, 1)
        return log_decision(query, intent, intent, execution_time_ms=ms, outcome="pending")
    except Exception:
        return None


def run_intent(chat_id: str, pack: Dict[str, Any], state: Dict[str, Any]) -> None:
    import time as _time_ri
    _start = _time_ri.time()
    intent = pack.get("intent")
    query = (pack.get("query") or "").strip()
    mode = state.get("mode", "auto")
    _decision_id: Optional[str] = None
    if intent in ("research", "simple_question", "brain", "table", "engineer"):
        _decision_id = _try_log_decision(query, intent or "unknown", _start)
    state["last_decision_id"] = _decision_id

    # H8.2: self_status — real check, not AI Engineer
    if intent == "self_status":
        handle_self_status(chat_id)
        return

    # H8.2: action commands — real operations
    if intent == "action":
        action = pack.get("action", "")
        if action == "restart_backend":
            cmd_restart_backend(chat_id)
        elif action == "restart_bot":
            cmd_restart_bot(chat_id)
        else:
            send(chat_id, f"⚠️ Неизвестное действие: {action}")
        return

    # H8.2: progress report — read from logs
    if intent == "progress_report":
        handle_progress_report(chat_id)
        return

    if intent == "greeting":
        send(chat_id, greeting_answer())
        return

    if intent == "small_talk":
        send(chat_id, small_talk_answer(query or ""))
        return

    if intent == "continuation":
        # Re-run last meaningful intent from history
        last_intent = state.get("last_intent") or "chat"
        last_query = state.get("last_topic") or query
        run_intent(chat_id, {"intent": last_intent, "query": last_query + " (продолжение)"}, state)
        return

    if intent == "compound_task":
        if state.get("mesh_enabled", True):
            _handle_mesh_task(chat_id, query, state)
        else:
            _handle_compound_task(chat_id, query, state)
        return

    if intent == "can_you":
        send(chat_id, can_you_answer(query))
        return

    if intent in ("summarize_file", "extract_from_file", "ask_about_file", "accounting"):
        _handle_file_intent(chat_id, intent, query, state)
        return

    if intent == "preference_saved":
        send(chat_id, pack.get("message", "✅ Запомнил."))
        return

    if intent == "capabilities":
        send(chat_id, capabilities_text())
        return

    if intent == "health":
        send(chat_id, human_health(get_health()))
        return

    if intent == "table":
        clarification = needs_table_clarification(query)
        if clarification and not pack.get("from_clarification"):
            state["pending"] = {"type": "table_clarification", "base_query": query}
            save_state(state)
            send(chat_id, clarification)
            return

        state["pending"] = None
        query = expand_categories(query)
        query = apply_language(query, state)

        state["last_topic"] = query
        state["last_table_query"] = query
        save_state(state)

        msg_id = send_and_get_id(chat_id, "📊 Получаю запрос...")
        edit_message(chat_id, msg_id, "🔎 Ищу через Tavily и Perplexity...")
        data = backend_post("/api/jarvis/telegram-tools/internet-table", {
            "query": query,
            "max_results": 10,
            "send_to_telegram": True,
        }, timeout=300)
        edit_message(chat_id, msg_id, "🧠 Анализирую через Claude/GPT...")
        edit_message(chat_id, msg_id, "📊 Формирую таблицу...")

        if data.get("_error"):
            send(chat_id, (
                "❌ Не удалось создать таблицу: " + data["_error"] + "\n\n"
                "Возможные причины:\n"
                f"- backend сервер не отвечает (проверьте uvicorn на {BACKEND})\n"
                "- закончились лимиты Perplexity/Tavily\n"
                "- timeout при поиске данных\n\n"
                "Попробуйте через минуту или измените запрос."
            ))
            return

        state["last_table_path"] = data.get("table_path", "")
        save_state(state)

        msg = (
            "✅ Таблица готова.\n"
            f"Строк: {data.get('rows_count')}\n"
            f"Файл: {data.get('table_path')}\n"
        )
        if (data.get("telegram_send") or {}).get("ok"):
            msg += "📎 Файл отправлен в Telegram."
        else:
            msg += "⚠️ Файл создан, но отправка могла не пройти."

        if mode == "debug":
            msg += "\n\nDebug:\n" + pretty(data, 1800)

        send(chat_id, msg)
        return

    if intent == "identity":
        keyboard = {
            "inline_keyboard": [[
                {"text": "Что умеешь?", "callback_data": "capabilities"},
                {"text": "Топ AI", "callback_data": "table_top_ai"},
                {"text": "Статус", "callback_data": "health"},
            ]]
        }
        send(chat_id, identity_answer(), reply_markup=keyboard)
        return

    if intent == "simple_question":
        try:
            _root_qa = str(Path(__file__).parent.parent)
            import sys as _sys_qa
            if _root_qa not in _sys_qa.path:
                _sys_qa.path.insert(0, _root_qa)
            from app.services.quick_answer import quick_answer as _quick_answer
            answer = _quick_answer(query)
        except Exception:
            answer = None
        if answer:
            # Mobile-compact: 2 sentences max
            compact = _compact_text(answer, max_sentences=2)
            send_with_feedback(chat_id, compact, _decision_id, query=query)
        else:
            # Fallback: route as research
            run_intent(chat_id, {"intent": "research", "query": query}, state)
        return

    if intent == "research":
        q = apply_language(query, state)
        send(chat_id, "🔎 Ищу и анализирую...")
        data = backend_post("/api/jarvis/tools/internet/research", {"query": q}, timeout=240)
        if data.get("_error"):
            send(chat_id, (
                "❌ Не смог получить результат: " + data["_error"] + "\n\n"
                "Возможные причины:\n"
                f"- backend сервер не отвечает ({BACKEND})\n"
                "- закончились лимиты Perplexity\n"
                "- timeout при поиске\n\n"
                "Попробуйте через минуту."
            ))
            return
        answer = data.get("answer") or pretty(data)
        send_with_feedback(chat_id, "🔎 Результат:\n" + answer, _decision_id, query=query)
        return

    if intent == "brain":
        q = apply_language(query, state)
        send(chat_id, "🧠 Думаю и проверяю актуальную информацию...")
        data = backend_post("/api/jarvis/brain/plan", {
            "task": q,
            "quality_target": "research",
        }, timeout=240)
        if data.get("_error"):
            send(chat_id, (
                "❌ Brain не ответил: " + data["_error"] + "\n\n"
                "Возможные причины:\n"
                f"- backend сервер не отвечает ({BACKEND})\n"
                "- timeout при анализе\n\n"
                "Попробуйте через минуту."
            ))
            return
        if mode == "debug":
            send(chat_id, "🧠 Brain debug:\n" + pretty(data))
        else:
            decision = data.get("decision", {})
            send(chat_id, (
                "🧠 План готов.\n"
                f"Интернет использован: {decision.get('use_internet')}\n"
                f"Причина: {decision.get('reason')}\n\n"
                "Краткий план:\n- " + "\n- ".join(data.get("plan", [])[:6])
            ))
        return

    if intent == "engineer":
        q = apply_language(query, state)
        send(chat_id, "🛠 Запускаю AI Engineer: анализ, риски, архитектура, план...")
        data = backend_post("/api/jarvis/ai-engineer/review", {
            "task": q,
            "mode": "safe_plan",
        }, timeout=300)

        if data.get("_error"):
            send(chat_id, (
                "❌ AI Engineer не ответил: " + data["_error"] + "\n\n"
                "Возможные причины:\n"
                f"- backend сервер не отвечает ({BACKEND})\n"
                "- timeout при анализе архитектуры\n\n"
                "Попробуйте через минуту."
            ))
            return

        msg = (
            "🛠 AI Engineer готов.\n"
            f"Отчёт: {data.get('artifact_path')}\n\n"
            "План:\n- " + "\n- ".join(data.get("plan", [])[:7])
        )
        rec = data.get("recommendation") or {}
        if rec:
            msg += "\n\nРекомендация:\n" + pretty(rec, 900)
        send(chat_id, msg)
        return

    if intent == "generate":
        prompt = re.sub(
            r"^(сгенерируй|создай\s+картинк[уи]|создай\s+фото|изображение|нарисуй|/gen)\s*:?",
            "", query, flags=re.I
        ).strip() or query
        # Parse num_images from original message (e.g. "Сделай 4 фото")
        num_match = re.search(r'\b(\d+)\s*(?:фот|картинк|изображен|photo|image|picture)', query, re.IGNORECASE)
        num_images = min(int(num_match.group(1)), 4) if num_match else 1
        loading_msg_id = send_and_get_id(
            chat_id,
            f"🎨 Генерирую {'изображения' if num_images > 1 else 'изображение'} через Replicate FLUX..."
        )
        data = backend_post("/api/jarvis/image/generate", {
            "prompt": prompt,
            "num_images": num_images,
            "aspect_ratio": "9:16",
            "style": "realistic",
        }, timeout=180)
        if data.get("_error"):
            edit_message(chat_id, loading_msg_id, (
                "❌ Генерация не запустилась: " + data["_error"] + "\n\n"
                "Убедитесь что REPLICATE_API_KEY установлен в .env\n"
                f"Backend: {BACKEND}"
            ))
            return
        urls = data.get("urls", [])
        provider = data.get("provider", "unknown")
        if not urls:
            edit_message(chat_id, loading_msg_id, "❌ Провайдер вернул пустой список изображений.")
            return
        edit_message(chat_id, loading_msg_id, f"✅ Готово! ({provider}) Отправляю {len(urls)} изображений...")
        for url in urls:
            _send_photo_url(chat_id, url, prompt[:80])
        return

    if intent == "job":
        job_id = query.strip()
        if not job_id:
            send(chat_id, (
                "Использование: /job <job_id>\n"
                "ID получаешь после /gen или \"сгенерируй фото\"\n\n"
                "Пример: /job abc123def456"
            ))
            return
        data = backend_get(f"/api/jarvis/v5/content-factory/jobs/{urllib.parse.quote(job_id)}", timeout=60)
        if data.get("_error"):
            send(chat_id, (
                "❌ Не удалось получить статус job: " + data["_error"] + "\n\n"
                "Возможные причины:\n"
                f"- backend сервер не отвечает ({BACKEND})\n"
                "- job_id не существует\n\n"
                "Уточните job_id или попробуйте позже."
            ))
            return
        result = data.get("result") or {}
        send(chat_id, (
            f"📦 Job: {job_id}\n"
            f"Status: {data.get('status')}\n"
            f"Run ID: {result.get('run_id')}\n"
            f"Drive: {result.get('drive_folder_url')}\n"
            f"Images: {len(result.get('image_urls') or [])}"
        ))
        return

    if intent == "chat":
        # General unknown conversational message goes to research instead of dead end
        q = apply_language(query, state)
        send(chat_id, "💬 Понял. Дам ответ через интернет/brain, чтобы не гадать.")
        data = backend_post("/api/jarvis/tools/internet/research", {"query": q}, timeout=240)
        if data.get("_error"):
            send(chat_id, (
                "❌ Не смог получить ответ: " + data["_error"] + "\n\n"
                "Попробуйте через минуту или переформулируйте вопрос."
            ))
            return
        answer = data.get("answer") or "Не смог найти уверенный ответ."
        send(chat_id, answer)
        return

    send(chat_id, "Не выбрал инструмент. Напиши «что ты умеешь?» или /smart_help")


def _load_mesh_settings() -> dict:
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.mesh_settings import load_mesh_settings
        return load_mesh_settings()
    except Exception:
        return {"mode": "auto", "confirm_before_mesh": "big_tasks",
                "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}


def _save_mesh_settings(settings: dict) -> None:
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.mesh_settings import save_mesh_settings
        save_mesh_settings(settings)
    except Exception:
        pass


def _mesh_control_text(state: dict) -> str:
    s = _load_mesh_settings()
    mode = s.get("mode", "auto")
    enabled = state.get("mesh_enabled", True)
    mesh_history = state.get("mesh_history", [])
    mode_labels = {"simple": "🎯 SIMPLE", "auto": "🤖 AUTO", "always": "🚀 ALWAYS"}
    return (
        f"🎛 MESH CONTROL\n\n"
        f"Smart Router: {'✅ ON' if enabled else '⏸ OFF'}\n"
        f"Текущий режим: {mode_labels.get(mode, mode)}\n"
        f"Mesh выполнений сегодня: {len(mesh_history)}"
    )


def _mesh_control_keyboard(state: dict) -> list:
    s = _load_mesh_settings()
    mode = s.get("mode", "auto")
    marks = {m: " ✓" if mode == m else "" for m in ["simple", "auto", "always"]}
    return [
        [
            {"text": f"🎯 SIMPLE{marks['simple']}", "callback_data": "mesh:mode:simple"},
            {"text": f"🤖 AUTO{marks['auto']}", "callback_data": "mesh:mode:auto"},
            {"text": f"🚀 ALWAYS{marks['always']}", "callback_data": "mesh:mode:always"},
        ],
        [
            {"text": "🔧 Settings", "callback_data": "mesh:settings"},
            {"text": "📋 Last plan", "callback_data": "mesh:last_plan"},
            {"text": "📜 History", "callback_data": "mesh:history"},
        ],
    ]


def _mesh_settings_text(s: dict) -> str:
    conf = s.get("confirm_before_mesh", "big_tasks")
    conf_labels = {"always": "Всегда", "big_tasks": "Крупные задачи", "never": "Никогда"}
    max_a = s.get("max_agents_per_task", 5)
    cost = s.get("cost_limit_per_task", 0.10)
    cw = s.get("cowork_delegation", "auto")
    cw_labels = {"auto": "Авто", "confirm": "Подтвердить", "never": "Никогда"}
    return (
        "⚙️ MESH SETTINGS\n\n"
        f"Подтвердить перед mesh: {conf_labels.get(conf, conf)}\n"
        f"Макс агентов на задачу: {max_a if max_a < 99 else '∞'}\n"
        f"Лимит стоимости задачи: ${cost:.2f}\n"
        f"Cowork делегирование: {cw_labels.get(cw, cw)}"
    )


def _mesh_settings_keyboard(s: dict) -> list:
    conf = s.get("confirm_before_mesh", "big_tasks")
    max_a = s.get("max_agents_per_task", 5)
    cost = s.get("cost_limit_per_task", 0.10)
    cw = s.get("cowork_delegation", "auto")

    def chk(val, current): return f" ✓" if val == current else ""

    return [
        [
            {"text": f"✅ Всегда{chk('always', conf)}", "callback_data": "mesh:cfg:confirm:always"},
            {"text": f"⚠️ Крупные{chk('big_tasks', conf)}", "callback_data": "mesh:cfg:confirm:big_tasks"},
            {"text": f"❌ Никогда{chk('never', conf)}", "callback_data": "mesh:cfg:confirm:never"},
        ],
        [
            {"text": f"1{chk(1, max_a)}", "callback_data": "mesh:cfg:maxagents:1"},
            {"text": f"3{chk(3, max_a)}", "callback_data": "mesh:cfg:maxagents:3"},
            {"text": f"5{chk(5, max_a)}", "callback_data": "mesh:cfg:maxagents:5"},
            {"text": f"∞{chk(99, max_a)}", "callback_data": "mesh:cfg:maxagents:99"},
        ],
        [
            {"text": f"$0.05{chk(0.05, cost)}", "callback_data": "mesh:cfg:cost:0.05"},
            {"text": f"$0.10{chk(0.10, cost)}", "callback_data": "mesh:cfg:cost:0.10"},
            {"text": f"$0.50{chk(0.50, cost)}", "callback_data": "mesh:cfg:cost:0.50"},
            {"text": f"∞{chk(99.0, cost)}", "callback_data": "mesh:cfg:cost:99"},
        ],
        [
            {"text": f"✅ Авто{chk('auto', cw)}", "callback_data": "mesh:cfg:cowork:auto"},
            {"text": f"⚠️ Confirm{chk('confirm', cw)}", "callback_data": "mesh:cfg:cowork:confirm"},
            {"text": f"❌ Никогда{chk('never', cw)}", "callback_data": "mesh:cfg:cowork:never"},
        ],
        [
            {"text": "💾 Save", "callback_data": "mesh:cfg:save"},
            {"text": "↩️ Defaults", "callback_data": "mesh:cfg:reset"},
            {"text": "🔙 Back", "callback_data": "mesh:back"},
        ],
    ]


def _send_mesh_control_panel(chat_id: str, state: dict) -> None:
    send_with_keyboard(chat_id, _mesh_control_text(state), _mesh_control_keyboard(state))


def handle_callback_query(callback_query: dict, state: dict) -> None:
    """Route callback_query from Telegram inline keyboards."""
    cq_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    msg = callback_query.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    message_id = msg.get("message_id")

    print(f"[CQ:handle] data={data!r}, chat={chat_id}, msg={message_id}", flush=True)

    if not data or not chat_id or not message_id:
        answer_callback_query(cq_id)
        print(f"[CQ:handle] Missing required fields, answered empty", flush=True)
        return

    parts = data.split(":")

    # ── Mesh mode switches ────────────────────────────────────────────────────
    if data.startswith("mesh:mode:"):
        new_mode = parts[2]
        s = _load_mesh_settings()
        s["mode"] = new_mode
        _save_mesh_settings(s)
        answer_callback_query(cq_id, f"Режим: {new_mode}")
        edit_message_with_keyboard(chat_id, message_id, _mesh_control_text(state), _mesh_control_keyboard(state))
        return

    # ── Mesh main panel buttons ───────────────────────────────────────────────
    if data == "mesh:settings":
        s = _load_mesh_settings()
        answer_callback_query(cq_id)
        edit_message_with_keyboard(chat_id, message_id, _mesh_settings_text(s), _mesh_settings_keyboard(s))
        return

    if data == "mesh:last_plan":
        answer_callback_query(cq_id)
        last_mesh = state.get("last_mesh_plan") or "Нет данных о последнем плане."
        send(chat_id, f"📋 Последний план:\n{last_mesh[:3000]}")
        return

    if data == "mesh:history":
        answer_callback_query(cq_id)
        history = state.get("mesh_history", [])
        if not history:
            send(chat_id, "История пуста.")
        else:
            lines = ["📚 Последние 10 mesh executions:"]
            for h in history[-10:]:
                q = h.get("query", "?")[:50]
                ag = h.get("agents", "?")
                lines.append(f"  • {q} → {ag}")
            send(chat_id, "\n".join(lines))
        return

    if data == "mesh:back":
        answer_callback_query(cq_id)
        edit_message_with_keyboard(chat_id, message_id, _mesh_control_text(state), _mesh_control_keyboard(state))
        return

    # ── Settings panel ────────────────────────────────────────────────────────
    if data.startswith("mesh:cfg:"):
        s = _load_mesh_settings()
        if len(parts) >= 4:
            key_map = {
                "confirm": ("confirm_before_mesh", str),
                "maxagents": ("max_agents_per_task", int),
                "cost": ("cost_limit_per_task", float),
                "cowork": ("cowork_delegation", str),
            }
            cfg_key = parts[2]
            cfg_val = parts[3]
            if cfg_key in key_map:
                field, cast = key_map[cfg_key]
                try:
                    s[field] = cast(cfg_val)
                except (ValueError, TypeError):
                    pass
                _save_mesh_settings(s)
                answer_callback_query(cq_id, "Сохранено")
                edit_message_with_keyboard(chat_id, message_id, _mesh_settings_text(s), _mesh_settings_keyboard(s))
                return
        if parts[2] == "save":
            _save_mesh_settings(s)
            answer_callback_query(cq_id, "✅ Настройки сохранены")
            edit_message_with_keyboard(chat_id, message_id, _mesh_settings_text(s), _mesh_settings_keyboard(s))
            return
        if parts[2] == "reset":
            try:
                from app.services.mesh_settings import reset_to_defaults
                s = reset_to_defaults()
            except Exception:
                s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
                     "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
                _save_mesh_settings(s)
            answer_callback_query(cq_id, "Сброшено до умолчаний")
            edit_message_with_keyboard(chat_id, message_id, _mesh_settings_text(s), _mesh_settings_keyboard(s))
            return

    # ── Per-task confirmation ─────────────────────────────────────────────────
    if data.startswith("task:"):
        action = parts[1] if len(parts) > 1 else ""
        task_id = parts[2] if len(parts) > 2 else ""
        _handle_task_callback(chat_id, message_id, cq_id, action, task_id, state)
        return

    # ── Decision feedback (👍/👎) ─────────────────────────────────────────────
    if data.startswith("feedback:"):
        _handle_feedback_callback(chat_id, cq_id, data, message_id)
        return

    # ── Quick-action buttons (Phase 41) ──────────────────────────────────────
    if data.startswith("qa:more:"):
        decision_id = parts[2] if len(parts) > 2 else ""
        answer_callback_query(cq_id, "🔄 Ищу подробнее...")
        orig_text = (msg.get("text") or "").replace("🔎 Результат:\n", "").strip()[:200]
        query_for_more = orig_text or "подробнее"
        run_intent(chat_id, {"intent": "research", "query": query_for_more + " подробный анализ"}, state)
        return

    if data.startswith("qa:obsidian:"):
        answer_callback_query(cq_id, "📋 Сохраняю в Obsidian...")
        orig_text = (msg.get("text") or "")[:3000]
        data_post = backend_post("/api/jarvis/tools/obsidian/save", {
            "content": orig_text,
            "title": "Jarvis — " + orig_text[:60],
        }, timeout=30)
        if data_post.get("_error"):
            send(chat_id, "❌ Obsidian недоступен: " + data_post["_error"])
        else:
            send(chat_id, "✅ Сохранено в Obsidian: " + (data_post.get("path") or "без пути"))
        return

    # ── Photo Studio callbacks (H4) ───────────────────────────────────────────
    try:
        _r_pscb = str(Path(__file__).parent.parent)
        import sys as _sys_pscb
        if _r_pscb not in _sys_pscb.path:
            _sys_pscb.path.insert(0, _r_pscb)
        from tools.photo_studio_telegram import (
            handle_faceswap_callback, handle_lora_callback,
            handle_photo_router_callback, handle_social_post_callback,
        )
        answer_callback_query(cq_id)
        if (
            handle_faceswap_callback(chat_id, data, send, _send_photo_url)
            or handle_lora_callback(chat_id, data, send)
            or handle_photo_router_callback(chat_id, data, send, _send_photo_url)
            or handle_social_post_callback(chat_id, data, send)
        ):
            return
    except Exception as _pscb_err:
        print(f"[PS:callback] error: {_pscb_err}", flush=True)
        answer_callback_query(cq_id)
        return

    answer_callback_query(cq_id)


def _handle_task_callback(
    chat_id: str,
    message_id: int,
    cq_id: str,
    action: str,
    task_id: str,
    state: dict,
) -> None:
    """Handle per-task confirmation buttons."""
    pending_plans = state.get("pending_plans", {})
    plan_info = pending_plans.get(task_id)

    if action == "execute":
        answer_callback_query(cq_id, "Выполняю...")
        if plan_info:
            edit_message_with_keyboard(chat_id, message_id, "🚀 Выполняю план...", [])
            _execute_pending_plan(chat_id, task_id, plan_info, state)
        else:
            send(chat_id, "⚠️ Задача не найдена или устарела.")

    elif action == "cancel":
        answer_callback_query(cq_id, "Отменено")
        pending_plans.pop(task_id, None)
        state["pending_plans"] = pending_plans
        save_state(state)
        edit_message_with_keyboard(chat_id, message_id, "❌ Задача отменена.", [])

    elif action == "simpler":
        answer_callback_query(cq_id, "Упрощаю план...")
        if plan_info:
            plan_info["max_agents"] = 1
            pending_plans[task_id] = plan_info
            state["pending_plans"] = pending_plans
            save_state(state)
            _show_task_confirmation(chat_id, message_id, task_id, plan_info, state)
        else:
            answer_callback_query(cq_id)

    elif action == "deeper":
        answer_callback_query(cq_id, "Расширяю анализ...")
        if plan_info:
            plan_info["deeper"] = True
            pending_plans[task_id] = plan_info
            state["pending_plans"] = pending_plans
            save_state(state)
            _show_task_confirmation(chat_id, message_id, task_id, plan_info, state)
        else:
            answer_callback_query(cq_id)

    else:
        answer_callback_query(cq_id)


def _handle_feedback_callback(chat_id: str, cq_id: str, data: str, message_id: int) -> None:
    """Handle 👍/👎 feedback from inline keyboards."""
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.decision_log import parse_feedback_callback, record_feedback_by_decision_id
        parsed = parse_feedback_callback(data)
        if not parsed:
            answer_callback_query(cq_id)
            return

        feedback_type = parsed["type"]
        decision_id = parsed["decision_id"]

        ok = record_feedback_by_decision_id(decision_id, feedback_type)
        if feedback_type == "positive":
            answer_callback_query(cq_id, "👍 Спасибо!")
        else:
            answer_callback_query(cq_id, "👎 Понял, учту!")
            if ok:
                send(chat_id, "Что было не так? Напишите коротко, я запомню.")
    except Exception as exc:
        answer_callback_query(cq_id)
        print(f"[feedback_cb] error: {exc}", flush=True)


def _show_task_confirmation(
    chat_id: str,
    message_id: Optional[int],
    task_id: str,
    plan_info: dict,
    state: dict,
) -> None:
    """Show/update per-task confirmation message with inline keyboard."""
    agents = plan_info.get("agents", [])
    query = plan_info.get("query", "")
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.agent_registry import estimate_plan_cost_usd, get_agent
        cost_usd = estimate_plan_cost_usd(agents)
    except Exception:
        cost_usd = 0.0
        def get_agent(x): return {"label": x, "speed_sec": (5, 30), "cost_per_use": 0.0}

    agent_lines = []
    total_sec_min, total_sec_max = 0, 0
    for i, aid in enumerate(agents, 1):
        try:
            cfg = get_agent(aid) or {}
        except Exception:
            cfg = {}
        label = cfg.get("label", aid)
        spd = cfg.get("speed_sec", (5, 30))
        total_sec_min += spd[0]
        total_sec_max += spd[1]
        agent_lines.append(f"[{i}] {label} (~{spd[1]}s, ~${cfg.get('cost_per_use', 0.0):.3f})")

    text = (
        f"🧠 Планирую выполнить задачу в {len(agents)} шага{'га' if len(agents) in [2,3,4] else 'ов'}:\n\n"
        + "\n".join(agent_lines)
        + f"\n\nИтого: ~{total_sec_max}с, ~${cost_usd:.3f}"
    )
    keyboard = [
        [
            {"text": "✅ Выполнить", "callback_data": f"task:execute:{task_id}"},
            {"text": "❌ Отмена", "callback_data": f"task:cancel:{task_id}"},
        ],
        [
            {"text": "🎯 Проще", "callback_data": f"task:simpler:{task_id}"},
            {"text": "🚀 Глубже", "callback_data": f"task:deeper:{task_id}"},
        ],
    ]
    if message_id:
        edit_message_with_keyboard(chat_id, message_id, text, keyboard)
    else:
        send_with_keyboard(chat_id, text, keyboard)


def _execute_pending_plan(chat_id: str, task_id: str, plan_info: dict, state: dict) -> None:
    """Actually run the mesh plan after user confirmation."""
    pending_plans = state.get("pending_plans", {})
    pending_plans.pop(task_id, None)
    state["pending_plans"] = pending_plans
    save_state(state)

    query = plan_info.get("query", "")
    if query:
        state_fresh = load_state()
        run_intent(chat_id, {"intent": "compound_task", "query": query}, state_fresh)
    else:
        send(chat_id, "⚠️ Не удалось восстановить задачу.")


def maybe_show_confirmation(
    chat_id: str,
    task_id: str,
    query: str,
    agents: list,
    state: dict,
) -> bool:
    """Show confirmation if settings require it. Returns True if confirmation was shown."""
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.mesh_settings import should_confirm_task
        from app.services.agent_registry import estimate_plan_cost_usd
        cost = estimate_plan_cost_usd(agents)
        if not should_confirm_task(agents, cost):
            return False
    except Exception:
        return False

    plan_info = {"query": query, "agents": agents}
    pending_plans = state.get("pending_plans", {})
    pending_plans[task_id] = plan_info
    state["pending_plans"] = pending_plans
    save_state(state)
    _show_task_confirmation(chat_id, None, task_id, plan_info, state)
    return True


def _handle_n8n_command(chat_id: str, query: str) -> None:
    """Handle /n8n [list|run <id>|status <exec_id>|enable/disable <id>]."""
    try:
        import sys as _sys_n8n
        _r_n8n = str(Path(__file__).parent.parent)
        if _r_n8n not in _sys_n8n.path:
            _sys_n8n.path.insert(0, _r_n8n)
        from app.services.n8n_integration import (
            discover_n8n_workflows,
            trigger_workflow,
            get_workflow_status,
            toggle_workflow,
            workflow_list_text,
        )
    except Exception as exc:
        send(chat_id, f"❌ n8n модуль недоступен: {exc}")
        return

    parts = query.split(None, 2)
    sub = parts[0].lower() if parts else "list"

    if sub == "list" or not sub:
        try:
            workflows = discover_n8n_workflows()
            # Support: /n8n list active|inactive|<filter>|<page_num>
            arg = parts[1].lower() if len(parts) > 1 else ""
            page = 0
            filter_str = ""
            if arg == "active":
                workflows = [w for w in workflows if w.get("active")]
            elif arg == "inactive":
                workflows = [w for w in workflows if not w.get("active")]
            elif arg.isdigit():
                page = int(arg)
            elif arg:
                filter_str = arg
            send(chat_id, workflow_list_text(workflows, page=page, filter_str=filter_str))
        except Exception as exc:
            send(chat_id, f"❌ Не удалось получить список workflows: {exc}")
        return

    if sub == "run":
        if len(parts) < 2:
            send(chat_id, "Использование: /n8n run <id или название> [параметры JSON]")
            return
        wf_id = parts[1]
        payload_raw = parts[2] if len(parts) > 2 else "{}"
        try:
            import json as _json
            payload = _json.loads(payload_raw)
        except Exception:
            payload = {"query": payload_raw}
        try:
            result = trigger_workflow(wf_id, payload)
            exec_id = result.get("execution_id") or result.get("id") or "?"
            send(chat_id, (
                f"🚀 Workflow запущен.\n"
                f"Execution ID: {exec_id}\n\n"
                f"Проверить статус: /n8n status {exec_id}"
            ))
        except Exception as exc:
            send(chat_id, f"❌ Не удалось запустить workflow: {exc}")
        return

    if sub == "status":
        if len(parts) < 2:
            send(chat_id, "Использование: /n8n status <execution_id>")
            return
        exec_id = parts[1]
        try:
            status = get_workflow_status(exec_id)
            finished = "✅" if status.get("finished") else "⏳"
            send(chat_id, (
                f"{finished} Execution: {exec_id}\n"
                f"Статус: {status.get('status', 'unknown')}\n"
                f"Завершён: {'да' if status.get('finished') else 'нет'}"
            ))
        except Exception as exc:
            send(chat_id, f"❌ Ошибка получения статуса: {translate_exception(exc)}")
        return

    if sub in ("enable", "disable"):
        if len(parts) < 2:
            send(chat_id, f"Использование: /n8n {sub} <workflow_id>")
            return
        wf_id = parts[1]
        active = (sub == "enable")
        try:
            ok = toggle_workflow(wf_id, active)
            icon = "✅" if ok else "❌"
            action = "включён" if active else "выключен"
            send(chat_id, f"{icon} Workflow {wf_id} {action}." if ok else f"❌ Не удалось изменить статус.")
        except Exception as exc:
            send(chat_id, f"❌ Ошибка: {translate_exception(exc)}")
        return

    send(chat_id, (
        "Использование:\n"
        "/n8n list — список workflows\n"
        "/n8n run <id> [JSON] — запустить\n"
        "/n8n status <exec_id> — статус\n"
        "/n8n enable/disable <id> — вкл/выкл"
    ))


# ─── Scheduler helpers ───────────────────────────────────────────────────────

def _get_scheduler():
    """Get or create global JarvisScheduler instance."""
    global _JARVIS_SCHEDULER
    if _JARVIS_SCHEDULER is None:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.scheduler import JarvisScheduler
        _JARVIS_SCHEDULER = JarvisScheduler(send_fn=send)
        _JARVIS_SCHEDULER.start()
    return _JARVIS_SCHEDULER


_JARVIS_SCHEDULER = None


def _handle_remind_command(chat_id: str, query: str) -> None:
    """Handle /remind <natural language time + text>."""
    if not query:
        send(chat_id, (
            "Использование: /remind <когда> <что>\n\n"
            "Примеры:\n"
            "  /remind через 1 час позвонить маме\n"
            "  /remind завтра в 9:00 проверить почту\n"
            "  /remind каждый день 7:00 утренний бриф\n"
            "  /remind в пятницу 18:00 сделать отчёт"
        ))
        return

    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.scheduler import parse_remind_text
        parsed = parse_remind_text(query)
    except Exception as exc:
        send(chat_id, f"❌ Ошибка парсера: {translate_exception(exc)}")
        return

    if not parsed:
        send(chat_id, (
            "Не смог разобрать время.\n"
            "Попробуйте: /remind через 30 минут <текст>\n"
            "или: /remind завтра в 10:00 <текст>"
        ))
        return

    sched = _get_scheduler()
    try:
        if parsed["schedule_type"] == "daily":
            task_id = sched.add_task(
                action="remind",
                params={"text": parsed["text"]},
                cron=parsed["cron"],
                chat_id=chat_id,
            )
            send(chat_id, (
                f"✅ Ежедневное напоминание создано!\n"
                f"Время: по расписанию ({parsed['cron']})\n"
                f"Текст: {parsed['text']}\n"
                f"ID: {task_id[:8]}\n\n"
                f"Управление: /schedule list"
            ))
        else:
            dt = parsed["datetime"]
            task_id = sched.add_task(
                action="remind",
                params={"text": parsed["text"]},
                run_at=dt,
                chat_id=chat_id,
            )
            dt_str = dt.strftime("%d.%m.%Y %H:%M") if dt else "?"
            send(chat_id, (
                f"✅ Напоминание установлено!\n"
                f"Время: {dt_str}\n"
                f"Текст: {parsed['text']}\n"
                f"ID: {task_id[:8]}\n\n"
                f"Управление: /schedule list"
            ))
    except Exception as exc:
        send(chat_id, f"❌ Не удалось создать напоминание: {exc}")


def _handle_schedule_command(chat_id: str, query: str) -> None:
    """Handle /schedule [list|remove <id>|add <cron> <action>]."""
    parts = query.split(None, 2)
    sub = parts[0].lower() if parts else "list"

    sched = _get_scheduler()

    if not sub or sub == "list":
        try:
            import sys as _sys
            _r = str(Path(__file__).parent.parent)
            if _r not in _sys.path:
                _sys.path.insert(0, _r)
            from app.services.scheduler import format_task_list
            tasks = sched.list_tasks()
            send(chat_id, format_task_list(tasks))
        except Exception as exc:
            send(chat_id, f"❌ Ошибка: {translate_exception(exc)}")
        return

    if sub == "remove" and len(parts) >= 2:
        task_id_prefix = parts[1]
        tasks = sched.list_tasks()
        # Support prefix match
        match = next((t for t in tasks if t["task_id"].startswith(task_id_prefix)), None)
        if not match:
            send(chat_id, f"Задача с ID «{task_id_prefix}» не найдена.")
            return
        ok = sched.remove_task(match["task_id"])
        if ok:
            send(chat_id, f"✅ Задача {task_id_prefix} отменена.")
        else:
            send(chat_id, f"❌ Не удалось отменить задачу.")
        return

    send(chat_id, (
        "Использование:\n"
        "/schedule list — список задач\n"
        "/schedule remove <id> — отменить задачу\n\n"
        "/remind — создать напоминание\n"
        "/brief on|off|time 9:00 — утренний бриф"
    ))


def _handle_brief_command(chat_id: str, query: str) -> None:
    """Handle /brief [on|off|time HH:MM]."""
    parts = query.split()
    sub = parts[0].lower() if parts else ""
    sched = _get_scheduler()

    if not sub or sub == "on":
        # Create morning brief at 9:00 UTC
        time_arg = parts[1] if len(parts) > 1 else "9:00"
        h, m = (9, 0)
        try:
            h, m = map(int, time_arg.split(":"))
        except Exception:
            pass
        task_id = sched.add_task(
            action="morning_brief",
            params={},
            cron=f"{m} {h} * * *",
            chat_id=chat_id,
        )
        send(chat_id, (
            f"✅ Утренний бриф включён!\n"
            f"Время: {h:02d}:{m:02d} UTC каждый день\n"
            f"ID: {task_id[:8]}\n\n"
            f"Отключить: /brief off"
        ))
        return

    if sub == "off":
        tasks = sched.list_tasks()
        brief_tasks = [t for t in tasks if t.get("action") == "morning_brief" and t.get("chat_id") == chat_id]
        if not brief_tasks:
            send(chat_id, "Утренний бриф не был включён.")
            return
        for t in brief_tasks:
            sched.remove_task(t["task_id"])
        send(chat_id, f"✅ Утренний бриф отключён ({len(brief_tasks)} задач удалено).")
        return

    if sub == "time" and len(parts) >= 2:
        _handle_brief_command(chat_id, "on " + parts[1])
        return

    send(chat_id, (
        "Использование:\n"
        "/brief on — включить утренний бриф (9:00 UTC)\n"
        "/brief time 8:00 — задать своё время\n"
        "/brief off — отключить"
    ))


def _handle_logs_command(chat_id: str, query: str) -> None:
    """Handle /logs [bot|backend|errors] [N]."""
    parts = query.split()
    component = parts[0].lower() if parts else "errors"
    try:
        n = int(parts[1]) if len(parts) > 1 else 30
        n = min(max(n, 1), 200)
    except ValueError:
        n = 30

    log_paths = {
        "errors": Path("state") / "errors.log",
        "decisions": Path("state") / "decisions.jsonl",
        "tasks": Path("state") / "scheduled_tasks.json",
    }

    _empty_messages = {
        "decisions": (
            "📊 Лог решений пуст. Используй Jarvis больше — "
            "каждый запрос пишется сюда автоматически."
        ),
        "errors": "✅ Лог ошибок пуст — всё работает чисто!",
        "tasks": "📭 Нет активных задач по расписанию.\nСоздай командой /remind",
    }

    if component in log_paths:
        p = log_paths[component]
        if not p.exists() or p.stat().st_size == 0:
            send(chat_id, _empty_messages.get(component, f"Файл {p} пуст или не найден."))
            return
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            if not lines:
                send(chat_id, _empty_messages.get(component, f"Файл {component} пуст."))
                return
            tail = lines[-n:] if len(lines) > n else lines
            text = f"📄 {component} (последние {len(tail)} строк):\n" + "\n".join(tail)
            send(chat_id, text[:3800])
        except Exception as exc:
            send(chat_id, f"❌ Ошибка чтения логов: {translate_exception(exc)}")
        return

    # Try to read Python log files from root
    bot_log_candidates = [Path("bot.log"), Path("jarvis_bot.log"), Path("logs") / "bot.log"]
    for p in bot_log_candidates:
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = lines[-n:]
                send(chat_id, f"📄 bot log ({len(tail)} строк):\n" + "\n".join(tail))
            except Exception as exc:
                send(chat_id, f"❌ {exc}")
            return

    send(chat_id, (
        "Использование: /logs [component] [N]\n"
        "Компоненты: errors, decisions, tasks\n"
        "Пример: /logs errors 50"
    ))


def _handle_selfcheck_command(chat_id: str) -> None:
    """Handle /selfcheck — extended self-diagnostic."""
    lines = ["🔍 Self-Check расширенный:\n"]

    # Backend health
    try:
        import requests as _req
        resp = _req.get(f"{BACKEND}/health/detailed", timeout=5)
        data = resp.json()
        lines.append("✅ Backend: online")
        lines.append(f"  uptime: {data.get('uptime_seconds', '?')}s")
        lines.append(f"  задач по расписанию: {data.get('scheduled_tasks', '?')}")
        lines.append(f"  ошибок за час: {data.get('errors_last_hour', '?')}")
        agents = data.get("agents", {})
        for name, status in agents.items():
            icon = "✅" if status == "configured" else "⚠️"
            lines.append(f"  {icon} {name}: {status}")
    except Exception as exc:
        lines.append(f"⚠️ Backend: {exc}")

    # Scheduler
    sched = _get_scheduler()
    tasks = sched.list_tasks()
    active = [t for t in tasks if t.get("active")]
    lines.append(f"\n⏰ Scheduler: {len(active)} активных задач")

    # Error log
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.error_reporter import get_recent_errors
        errors = get_recent_errors(5)
        lines.append(f"\n🔴 Последних ошибок: {len(errors)}")
        for e in errors[-3:]:
            lines.append(f"  [{e['error_id'][:8]}] {e['exception_type']}: {e['exception_message'][:50]}")
    except Exception as exc:
        lines.append(f"⚠️ Error log: {exc}")

    # Disk check
    try:
        import shutil
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        lines.append(f"\n💾 Диск: {free_gb:.1f}GB свободно / {total_gb:.1f}GB")
    except Exception:
        pass

    # Decision stats
    try:
        from app.services.decision_log import get_stats
        stats = get_stats(50)
        lines.append(f"\n📊 Последние 50 решений:")
        lines.append(f"  success rate: {stats['success_rate']}%")
        lines.append(f"  👍: {stats['positive_feedback']} 👎: {stats['negative_feedback']}")
    except Exception:
        pass

    send(chat_id, "\n".join(lines))


def _handle_errors_command(chat_id: str, query: str) -> None:
    """Handle /errors [recent|clear|trace <id>]."""
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.error_reporter import (
            clear_errors,
            format_recent_errors,
            get_error_by_id,
        )
    except Exception as exc:
        send(chat_id, f"❌ Error reporter недоступен: {exc}")
        return

    parts = query.split(None, 1)
    sub = parts[0].lower() if parts else "recent"

    if not sub or sub == "recent":
        send(chat_id, format_recent_errors(10))
        return

    if sub == "clear":
        count = clear_errors()
        send(chat_id, f"✅ Лог ошибок очищен ({count} записей).")
        return

    if sub == "trace" and len(parts) >= 2:
        error_id = parts[1].strip()
        error = get_error_by_id(error_id)
        if not error:
            send(chat_id, f"Ошибка с ID «{error_id}» не найдена.")
            return
        tb = error.get("traceback", "Нет traceback")[:3000]
        send(chat_id, (
            f"🔴 Error [{error['error_id'][:8]}]\n"
            f"Время: {error.get('timestamp', '?')[:19]}\n"
            f"Тип: {error.get('exception_type', '?')}\n"
            f"Сообщение: {error.get('exception_message', '?')}\n"
            f"Контекст: {error.get('context', '?')}\n\n"
            f"Traceback:\n{tb}"
        ))
        return

    send(chat_id, (
        "Использование:\n"
        "/errors recent — последние 10 ошибок\n"
        "/errors clear — очистить лог\n"
        "/errors trace <id> — полный traceback"
    ))


def _handle_improve_command(chat_id: str, query: str) -> None:
    """Handle /improve [analyze|stats]."""
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.decision_log import analyze_decisions, format_stats_message
    except Exception as exc:
        send(chat_id, f"❌ Decision log недоступен: {exc}")
        return

    sub = (query.split()[0] if query else "").lower()

    if sub == "analyze" or sub == "analyse":
        send(chat_id, "🔍 Анализирую решения за последние 50 запросов...")
        try:
            analysis = analyze_decisions(50)
            send(chat_id, f"🧠 Анализ и рекомендации:\n\n{analysis}")
        except Exception as exc:
            send(chat_id, f"❌ Ошибка анализа: {translate_exception(exc)}")
        return

    if sub == "stats":
        send(chat_id, format_stats_message(100))
        return

    send(chat_id, (
        "Использование:\n"
        "/improve analyze — анализ последних 50 решений + рекомендации\n"
        "/improve stats — статистика (success rate, intents, feedback)"
    ))


def _handle_cowork_command(chat_id: str, query: str) -> None:
    """Handle /cowork [status|send|list|stop] <text>."""
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.cowork_bridge import (
            list_pending_tasks,
            make_task,
            record_cowork_cost,
            send_task_to_cowork,
        )
        from app.services.cowork_watcher import get_watcher, start_watcher, stop_watcher
    except Exception as e:
        send(chat_id, f"❌ Cowork недоступен: {e}")
        return

    sub = query.lower().split(None, 1)[0] if query else "status"
    rest = query[len(sub):].strip() if query else ""

    if sub == "status" or not query:
        watcher = get_watcher()
        active = watcher is not None and watcher.is_active()
        pending = list_pending_tasks()
        lines = [
            f"🔧 Cowork Bridge",
            f"  Watcher: {'✅ активен' if active else '⏸ остановлен'}",
            f"  Задач в очереди: {len(pending)}",
            f"  Inbox: state/cowork_inbox/",
            f"  Outbox: state/cowork_outbox/",
            "",
            "Команды:",
            "  /cowork send <задача>",
            "  /cowork list — последние задачи",
            "  /cowork stop — остановить watcher",
        ]
        send(chat_id, "\n".join(lines))

    elif sub == "send":
        if not rest:
            send(chat_id, "Использование: /cowork send <описание задачи>")
            return
        try:
            from app.services.cowork_watcher import _is_safe_instruction
            if not _is_safe_instruction(rest):
                send(chat_id, "❌ Задача содержит запрещённые пути.")
                return
        except Exception:
            pass
        watcher = get_watcher()
        if watcher is None or not watcher.is_active():
            watcher = start_watcher(callback=_cowork_delivery_callback)
        task = make_task(rest)
        task_id = send_task_to_cowork(task)
        record_cowork_cost(task_id, units=1.0)
        if watcher:
            watcher.track_task(task_id)
        send(chat_id, f"📤 Задача отправлена в Cowork\nID: {task_id[:8]}...\n\nCowork ответит автоматически. Ожидай уведомления.")

    elif sub == "list":
        pending = list_pending_tasks()
        if not pending:
            send(chat_id, "📋 Очередь Cowork пуста.")
            return
        lines = [f"📋 Задач в очереди: {len(pending)}"]
        for t in pending[-5:]:
            tid = t.get("task_id", "?")[:8]
            instr = t.get("instruction", "?")[:50]
            lines.append(f"  • {tid}... — {instr}")
        send(chat_id, "\n".join(lines))

    elif sub == "stop":
        stop_watcher()
        send(chat_id, "⏸ Cowork watcher остановлен.")

    else:
        # Treat entire query as a task instruction
        task = make_task(query)
        task_id = send_task_to_cowork(task)
        record_cowork_cost(task_id, units=1.0)
        watcher = get_watcher()
        if watcher is None or not watcher.is_active():
            watcher = start_watcher(callback=_cowork_delivery_callback)
        if watcher:
            watcher.track_task(task_id)
        send(chat_id, f"📤 Задача отправлена в Cowork\nID: {task_id[:8]}...\n\nОжидай ответа.")


def _run_diag() -> str:
    """Run self-check and return structured Telegram text."""
    lines = ["🔍 Jarvis Self-Diagnostics\n"]

    # 1. Backend reachable?
    try:
        resp = http_json("GET", f"{BACKEND}/health", timeout=5)
        if resp.get("ok") is False or "_error" in resp:
            lines.append("❌ Backend: недоступен")
        else:
            lines.append("✅ Backend: доступен")
    except Exception as e:
        lines.append(f"❌ Backend: ошибка ({e})")

    # 2. All 13 agents available?
    try:
        import sys as _sys
        _root = str(Path(__file__).parent.parent)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from app.services.agent_registry import AGENTS
        count = len(AGENTS)
        lines.append(f"{'✅' if count >= 13 else '⚠️'} Агентов в реестре: {count}/13")
    except Exception as e:
        lines.append(f"❌ Agent registry: {e}")

    # 3. Cowork inbox/outbox exist?
    cowork_inbox = ROOT / "state" / "cowork_inbox"
    cowork_outbox = ROOT / "state" / "cowork_outbox"
    lines.append(f"{'✅' if cowork_inbox.exists() else '❌'} Cowork inbox: {cowork_inbox}")
    lines.append(f"{'✅' if cowork_outbox.exists() else '❌'} Cowork outbox: {cowork_outbox}")

    # 4. Phase 13 code present?
    try:
        from app.services import jarvis_telegram_file_tools  # noqa: F401
        lines.append("✅ Phase 13 file tools: загружены")
    except Exception as e:
        lines.append(f"❌ Phase 13 file tools: {e}")

    # 5. Conversation memory writable?
    mem_dir = ROOT / "state" / "conversation_memory"
    try:
        mem_dir.mkdir(parents=True, exist_ok=True)
        test_file = mem_dir / ".diag_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        lines.append("✅ Conversation memory: запись работает")
    except Exception as e:
        lines.append(f"❌ Conversation memory: {e}")

    # 6. classify_file_caption present in this module?
    import inspect as _inspect
    has_clf = "classify_file_caption" in dir() or callable(globals().get("classify_file_caption"))
    lines.append(f"{'✅' if has_clf else '❌'} classify_file_caption: {'OK' if has_clf else 'missing'}")

    # 7. Bot token set?
    lines.append(f"{'✅' if BOT_TOKEN else '❌'} BOT_TOKEN: {'задан' if BOT_TOKEN else 'НЕ задан!'}")

    # 8. BACKEND env var
    lines.append(f"ℹ️  BACKEND URL: {BACKEND}")

    lines.append("\n/diag завершён.")
    return "\n".join(lines)


HELP_TEXT = """🤖 Jarvis Smart Help

📊 Исследование:
  /research <запрос> — поиск через Perplexity
  /table <тема> — Excel/CSV таблица с умными колонками
  /brain <задача> — анализ + план

🛠 Разработка:
  /engineer <задача> — архитектура, риски, план

🎨 Генерация контента:
  /gen <описание> — изображение (job_id в ответе)
  /job <job_id> — статус генерации

📂 Файлы:
  Отправь файл (PDF/DOCX/XLSX/CSV) с подписью → авто-анализ
  «суммируй файл» / «извлеки данные» / «что в файле»

🧠 Agent Mesh:
  /agents — статус всех агентов
  /mesh — настройка Smart Router
  /cowork [send|list|status|stop] — Cowork Bridge (Claude Desktop)
  /status — статус AI провайдеров
  /stats — статистика за сегодня

⏰ Расписание:
  /remind <когда> <текст> — напоминание на естественном языке
  /schedule list — список задач
  /schedule remove <id> — отменить задачу
  /brief on|off|time 9:00 — утренний бриф

🧠 Self-Improvement:
  /improve stats — статистика решений
  /improve analyze — анализ + рекомендации от Claude

🔧 Надёжность + Remote Control:
  /errors recent — последние 10 ошибок
  /errors trace <id> — traceback ошибки
  /errors clear — очистить лог
  /logs errors|decisions|tasks [N] — читать лог-файлы
  /selfcheck — расширенная диагностика

⚙️ Управление:
  /mode auto|simple|debug — режим работы
  /diag — self-check диагностика
  /smart_health — статус систем
  /debug_health — полный JSON статус
  /cancel — отменить текущую задачу
  /history — последние 10 сообщений
  /clear — очистить историю

💬 Естественный язык (примеры):
  «Сравни FAL и Replicate»
  «Создай таблицу топ AI сервисов»
  «Найди топ-10 AI и сделай таблицу» → mesh execution
  «Запусти генерацию изображения девушки»"""


def cmd_capabilities(chat_id: str) -> None:
    """Send full capabilities list."""
    text = (
        "🤖 ВСЕ ВОЗМОЖНОСТИ JARVIS:\n\n"
        "═══════════════════════════\n"
        "🧠 AI & RESEARCH\n"
        "═══════════════════════════\n"
        "- Internet research (Perplexity)\n"
        "- AI engineer (архитектура, код)\n"
        "- Brain planning\n"
        "- Smart routing между AI\n\n"
        "═══════════════════════════\n"
        "📸 PHOTO STUDIO\n"
        "═══════════════════════════\n"
        "- /menu_photo <блюдо> — фото блюд\n"
        "- /social_post <блюдо> — Instagram пост\n"
        "- /menu_book <блюда> — серия фото\n"
        "- /party_promo <тема> — постер вечеринки\n"
        "- /invite_card — приглашение\n"
        "- /faceswap — замена лица\n"
        "- /enhance — улучшение фото\n"
        "- /lora_train — обучить твою модель\n"
        "- /me_as <роль> — себя в роли\n"
        "- /me_in <место> — себя в месте\n"
        "- /me_style <стиль> — стилизация\n\n"
        "═══════════════════════════\n"
        "🌙 NIGHT AUTONOMY\n"
        "═══════════════════════════\n"
        "- Night Workflow (5 phases)\n"
        "- Daily Recap → Obsidian\n"
        "- Auto Content → завтрашние посты\n"
        "- Self-Improvement loop\n"
        "- Trend Analyzer\n"
        "- Smart Schedule\n\n"
        "═══════════════════════════\n"
        "DESIGN STUDIO (Block F+L)\n"
        "═══════════════════════════\n"
        "- /design <описание> — Figma design brief via Claude AI\n"
        "- /figma_queue — pending Figma designs\n"
        "- /figma_status — history of designs\n"
        "- /figma_clear — cleanup old designs\n"
        "- /landing <тема> — quick HTML landing (Tailwind)\n"
        "- /landing_brief — Landing 2.0 (8 steps + Claude content)\n"
        "- /landing_demo — examples\n"
        "- /simple_game snake|tictactoe|memory|2048 — HTML5 игры\n\n"
        "═══════════════════════════\n"
        "AI APP BUILDER (Block L)\n"
        "═══════════════════════════\n"
        "- /create_app <описание> — bolt.diy app spec via Claude\n"
        "- /create_simple <описание> — quick app spec\n"
        "- /bolt_status — check bolt.diy status\n"
        "- /bolt_queue — pending app specs\n\n"
        "═══════════════════════════\n"
        "SMART PHOTOS (Block L)\n"
        "═══════════════════════════\n"
        "- /smart_photo <описание> — Claude-enhanced pro prompt\n"
        "- /pro_food <блюдо> — food photography prompt\n\n"
        "═══════════════════════════\n"
        "INTEGRATIONS\n"
        "═══════════════════════════\n"
        "- Telegram bot\n"
        "- Web Dashboard\n"
        "- Public API v1\n"
        "- Obsidian Vault\n"
        "- n8n Cloud\n"
        "- Voice (Whisper)\n"
        "- Vision (Claude)\n"
        "- bolt.diy (AI app builder)\n"
        "- Figma MCP (real Figma designs)\n\n"
        "═══════════════════════════\n"
        "RELIABILITY\n"
        "═══════════════════════════\n"
        "- Self-healing\n"
        "- 24/7 watchdog\n"
        "- Auto-recovery\n"
        "- Periodic cleanup\n"
        "- 1800+ автотестов\n\n"
        "Используй /smart_help для деталей."
    )
    send(chat_id, text)


def cmd_night_status(chat_id: str) -> None:
    """H8.5: Show night autonomy schedule status."""
    import json as _jns
    tasks_path = Path("state") / "scheduled_tasks.json"
    if not tasks_path.exists():
        send(chat_id, "❌ Night Autonomy не активирована — конфигурация задач не найдена.")
        return
    try:
        tasks = _jns.loads(tasks_path.read_text(encoding="utf-8"))
        night_tasks = [t for t in tasks if str(t.get("action", "")).startswith("night_")]
        if not night_tasks:
            send(chat_id, "❌ Night Autonomy НЕ активирована! Нет night_* задач в планировщике.")
            return
        lines = ["🌙 Night Autonomy задачи:\n"]
        for t in night_tasks:
            active = "✅" if t.get("active") else "⏸"
            action = t.get("action", t.get("task_id", "?"))
            cron = t.get("cron", t.get("schedule", "unknown"))
            lines.append(f"{active} {action}: {cron}")
        send(chat_id, "\n".join(lines))
    except Exception as exc:
        send(chat_id, f"⚠️ Ошибка чтения расписания: {translate_exception(exc)}")


def cmd_night_now(chat_id: str) -> None:
    """H8.5: Run all night phases immediately for manual testing."""
    import threading as _thr_nn
    import asyncio as _asyncio_nn

    send(chat_id, "🌙 Запускаю Night Workflow вручную (winddown + deep_work + self_improve + morning_prep)...")

    def _run() -> None:
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys_nn
            if _root not in _sys_nn.path:
                _sys_nn.path.insert(0, _root)
            from app.services.night_workflows import NightWorkflow

            async def _async_run() -> None:
                nw = NightWorkflow()
                await nw.run_phase_winddown()
                await nw.run_phase_deep_work()
                await nw.run_phase_self_improve()
                await nw.run_phase_morning_prep()

            _asyncio_nn.run(_async_run())
            send(chat_id,
                 "✅ Все 4 phases выполнены!\n\n"
                 "Проверь:\n"
                 "- state/night_workflows/phase_log.jsonl\n"
                 "- state/improvement_log.json\n"
                 "- /night_status")
        except Exception as exc:
            send(chat_id, f"❌ Ошибка night workflow: {translate_exception(exc)}")

    _thr_nn.Thread(target=_run, daemon=True).start()


def cmd_restart_backend(chat_id: str) -> None:
    """H8.4: Kill backend processes and restart via PowerShell script."""
    import subprocess as _sub
    send(chat_id, "🔄 Перезагружаю backend...")
    try:
        _sub.run(
            ["powershell", "-Command",
             "Get-Process -Name python -ErrorAction SilentlyContinue | "
             "Where-Object { $_.CommandLine -like '*app.main*' } | Stop-Process -Force"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        send(chat_id, f"⚠️ Остановка: {exc}")

    time.sleep(3)

    try:
        script = Path(__file__).parent.parent / "scripts" / "start_backend_only.ps1"
        if not script.exists():
            script = Path(__file__).parent.parent / "start_jarvis.ps1"
        if script.exists():
            _sub.Popen(["powershell", "-File", str(script)], cwd=str(Path(__file__).parent.parent))
            time.sleep(20)
            try:
                resp = urllib.request.urlopen(f"{BACKEND}/health", timeout=5)
                if resp.status == 200:
                    send(chat_id, "✅ Backend перезагружен и работает!")
                    return
            except Exception:
                pass
            send(chat_id, "⚠️ Backend перезапущен, но health check не прошёл. Подожди 30с и проверь /selfcheck")
        else:
            send(chat_id, "❌ Скрипт запуска backend не найден. Запустите вручную: `.\\start_jarvis.ps1`")
    except Exception as exc:
        send(chat_id, f"❌ Не удалось запустить backend: {exc}")


def cmd_restart_bot(chat_id: str) -> None:
    """H8.4: Self-terminate bot — watchdog will restart it."""
    send(chat_id, "🔄 Перезапускаю бот через 5 секунд... Watchdog поднимет новый процесс.")
    time.sleep(5)
    os._exit(0)


def cmd_design(chat_id: str, query: str) -> None:
    """L.1: Generate Figma design brief via Claude API and queue it."""
    if not query:
        send(chat_id,
            "Использование: /design <описание>\n\n"
            "Примеры:\n"
            "  /design лендинг ресторана для семейных ужинов\n"
            "  /design мобильное приложение для бронирования\n"
            "  /design дашборд аналитики e-commerce\n\n"
            "Claude AI сгенерирует детальный дизайн-бриф.\n"
            "Затем создашь реальный дизайн в Figma через Claude Code!")
        return

    send(chat_id, "Генерирую дизайн-бриф через Claude AI...")

    try:
        from app.services.figma_brief_generator import generate_design_brief, format_brief_preview
        from app.services.figma_queue import FigmaQueue

        brief = generate_design_brief(query)
        queue = FigmaQueue()
        queue_id = queue.add(brief)
        preview = format_brief_preview(brief)

        comp_count = len(brief.get("components", []))
        project_name = brief.get("project_name", "Проект")
        project_type = brief.get("project_type", "landing")
        design_style = brief.get("design_style", "modern")

        send(chat_id,
            f"Бриф готов!\n\n"
            f"{preview}\n\n"
            f"Компонентов: {comp_count}\n"
            f"Mobile + Desktop: да\n\n"
            f"Чтобы создать в Figma:\n"
            f"1. Открой Claude Code в проекте\n"
            f"2. Напиши: process figma queue\n"
            f"3. Дизайн появится в Figma!\n\n"
            f"ID запроса: {queue_id}")
    except Exception as exc:
        logger.exception("cmd_design failed chat=%s", chat_id)
        send(chat_id, f"Ошибка: {translate_exception(exc)}")


def cmd_figma_queue(chat_id: str, query: str) -> None:
    """L.1: Show pending Figma designs in queue."""
    from app.services.figma_queue import FigmaQueue
    queue = FigmaQueue()
    items = queue.get_pending()
    if not items:
        send(chat_id, "Очередь Figma пустая.\n\nДобавь дизайн: /design <описание>")
        return
    lines = [f"В очереди Figma: {len(items)}\n"]
    for i, item in enumerate(items[:10], 1):
        brief = item.get("brief", {})
        name = brief.get("project_name", "?")
        created = item.get("created_at", "")[:10]
        lines.append(f"{i}. {name} ({created}) — ID: {item['id']}")
    lines.append("\nОбработать: напиши 'process figma queue' в Claude Code")
    send(chat_id, "\n".join(lines))


def cmd_figma_status(chat_id: str, query: str) -> None:
    """L.1: Show recent Figma design history."""
    from app.services.figma_queue import FigmaQueue, STATUS_COMPLETED, STATUS_PENDING, STATUS_FAILED
    queue = FigmaQueue()
    items = queue.get_recent(limit=5)
    summary = queue.get_status_summary()
    if not items:
        send(chat_id, "Нет истории дизайнов.\n\nСоздай первый: /design <описание>")
        return
    lines = [
        f"Дизайны Figma (всего: ожидают {summary.get('pending', 0)}, "
        f"готово {summary.get('completed', 0)}, ошибок {summary.get('failed', 0)})\n"
    ]
    status_icon = {STATUS_COMPLETED: "OK", STATUS_PENDING: "...", STATUS_FAILED: "ERR", "processing": ">>>"}
    for item in items:
        brief = item.get("brief", {})
        name = brief.get("project_name", "?")
        st = item.get("status", "?")
        icon = status_icon.get(st, "?")
        created = item.get("created_at", "")[:10]
        lines.append(f"[{icon}] {name} ({created}) — {item['id']}")
    send(chat_id, "\n".join(lines))


def cmd_figma_clear(chat_id: str, query: str) -> None:
    """L.1: Clear old completed Figma designs."""
    from app.services.figma_queue import FigmaQueue
    queue = FigmaQueue()
    removed = queue.clear_completed(older_than_days=7)
    if removed:
        send(chat_id, f"Удалено {removed} выполненных дизайнов старше 7 дней.")
    else:
        send(chat_id, "Нечего очищать — нет выполненных дизайнов старше 7 дней.")


# ── bolt.diy commands (Block L.2) ────────────────────────────────────────────

def cmd_bolt_status(chat_id: str, query: str) -> None:
    """L.2: Check if bolt.diy is running."""
    from app.services.bolt_diy_health import check_bolt_running, get_bolt_url
    if check_bolt_running():
        send(chat_id,
            f"bolt.diy работает!\n\n"
            f"Открой: {get_bolt_url()}\n\n"
            "Создай приложение: /create_app <описание>")
    else:
        from app.services.bolt_diy_health import format_bolt_not_running_message
        send(chat_id, format_bolt_not_running_message())


def cmd_bolt_open(chat_id: str, query: str) -> None:
    """L.2: Send bolt.diy link."""
    from app.services.bolt_diy_health import check_bolt_running, get_bolt_url
    url = get_bolt_url()
    running = check_bolt_running()
    status = "работает" if running else "не запущен"
    send(chat_id,
        f"bolt.diy ({status}): {url}\n\n"
        "Для генерации приложений: /create_app <описание>")


def cmd_create_app(chat_id: str, query: str) -> None:
    """L.2: Generate full app spec for bolt.diy."""
    if not query:
        send(chat_id,
            "Использование: /create_app <описание>\n\n"
            "Примеры:\n"
            "  /create_app трекер тренировок с прогрессом\n"
            "  /create_app CRM для малого бизнеса\n"
            "  /create_app планировщик задач с канбан-доской\n\n"
            "Claude AI сгенерирует спецификацию и промпт для bolt.diy.\n"
            "Запусти bolt.diy сначала: /bolt_status")
        return

    from app.services.bolt_diy_health import check_bolt_running, format_bolt_not_running_message
    if not check_bolt_running():
        send(chat_id, format_bolt_not_running_message())
        return

    send(chat_id, f"Генерирую спецификацию приложения: {query[:60]}...")

    try:
        from app.services.app_spec_generator import generate_app_spec, format_spec_preview
        from app.services.bolt_queue import BoltQueue
        from app.services.bolt_diy_health import get_bolt_url

        spec = generate_app_spec(query)
        queue = BoltQueue()
        queue_id = queue.add(spec)

        preview = format_spec_preview(spec)
        app_name = spec.get("app_name", "MyApp")
        tagline = spec.get("tagline", "")
        stack = spec.get("tech_stack", "React + Tailwind")
        features = spec.get("features", [])
        feat_lines = "\n".join(
            f"{i}. {f.get('name', '?')}"
            for i, f in enumerate(features[:5], 1)
        )
        bolt_prompt = spec.get("bolt_diy_prompt", "")
        bolt_url = get_bolt_url()

        send(chat_id,
            f"App спецификация готова!\n\n"
            f"<b>{app_name}</b>\n"
            f"{tagline}\n\n"
            f"Stack: {stack}\n\n"
            f"Фичи ({len(features)}):\n{feat_lines}\n\n"
            f"Файл: state/bolt_queue/{queue_id}.json\n\n"
            f"<b>Как использовать:</b>\n"
            f"1. Открой <a href=\"{bolt_url}\">bolt.diy</a>\n"
            f"2. Вставь промпт ниже в чат\n"
            f"3. Получи приложение через 1-3 минуты!\n\n"
            f"<b>Промпт для bolt.diy:</b>\n"
            f"<pre>{bolt_prompt[:800]}</pre>")
    except Exception as exc:
        logger.exception("cmd_create_app failed chat=%s", chat_id)
        send(chat_id, f"Ошибка: {translate_exception(exc)}")


def cmd_create_simple(chat_id: str, query: str) -> None:
    """L.2: Generate simplified app spec for bolt.diy."""
    if not query:
        send(chat_id,
            "Использование: /create_simple <описание>\n\n"
            "Для быстрых простых приложений (3-5 фич).\n"
            "Для сложных используй /create_app")
        return

    from app.services.bolt_diy_health import check_bolt_running, format_bolt_not_running_message
    if not check_bolt_running():
        send(chat_id, format_bolt_not_running_message())
        return

    send(chat_id, f"Быстрая спецификация: {query[:50]}...")

    try:
        from app.services.app_spec_generator import generate_simple_app_spec
        from app.services.bolt_queue import BoltQueue
        from app.services.bolt_diy_health import get_bolt_url

        spec = generate_simple_app_spec(query)
        queue = BoltQueue()
        queue_id = queue.add(spec)

        app_name = spec.get("app_name", "App")
        features = spec.get("features", [])
        if isinstance(features[0], dict) if features else False:
            feat_lines = "\n".join(f"- {f.get('name', '?')}" for f in features)
        else:
            feat_lines = "\n".join(f"- {f}" for f in features)
        bolt_prompt = spec.get("bolt_diy_prompt", "")

        send(chat_id,
            f"<b>{app_name}</b>\n\n"
            f"Фичи:\n{feat_lines}\n\n"
            f"<b>Промпт для bolt.diy:</b>\n"
            f"<pre>{bolt_prompt[:600]}</pre>")
    except Exception as exc:
        logger.exception("cmd_create_simple failed chat=%s", chat_id)
        send(chat_id, f"Ошибка: {translate_exception(exc)}")


def cmd_bolt_queue(chat_id: str, query: str) -> None:
    """L.2: Show pending bolt.diy app specs."""
    from app.services.bolt_queue import BoltQueue
    queue = BoltQueue()
    items = queue.get_pending()
    if not items:
        send(chat_id, "Очередь bolt.diy пустая.\n\nСоздай приложение: /create_app <описание>")
        return
    lines = [f"В очереди bolt.diy: {len(items)}\n"]
    for i, item in enumerate(items[:10], 1):
        spec = item.get("spec", {})
        name = spec.get("app_name", "?")
        created = item.get("created_at", "")[:10]
        lines.append(f"{i}. {name} ({created}) — ID: {item['id']}")
    send(chat_id, "\n".join(lines))


def cmd_landing(chat_id: str, query: str) -> None:
    """F.3: Generate HTML landing page from topic description."""
    if not query:
        send(chat_id,
            "🌐 Использование: /landing <тема>\n\n"
            "Примеры:\n"
            "  /landing AI photo studio\n"
            "  /landing restaurant booking\n"
            "  /landing online store\n\n"
            "💡 Создам готовый HTML с Tailwind CSS — открывается в браузере.")
        return

    send(chat_id, f"🌐 Генерирую landing page: {query[:60]}...")

    try:
        from app.services.landing_generator import generate_landing
        path = generate_landing(query, str(ROOT / "state" / "landings"))

        send(chat_id,
            f"✅ Landing page готов!\n\n"
            f"📁 Файл: {path}\n\n"
            f"🌐 Как открыть:\n"
            f"1. Открой проводник Windows\n"
            f"2. Перейди в папку state/landings/\n"
            f"3. Двойной клик на HTML файле\n"
            f"4. Откроется в браузере\n\n"
            f"💡 Можешь редактировать в любом редакторе.")
    except Exception as exc:
        send(chat_id, f"❌ Ошибка: {translate_exception(exc)}")


# ── Smart Photo Prompts (Block L.3) ──────────────────────────────────────────

def cmd_smart_photo(chat_id: str, query: str) -> None:
    """L.3: Generate professional photo prompt via Claude then create image."""
    if not query:
        send(chat_id,
            "Использование: /smart_photo <описание>\n\n"
            "Примеры:\n"
            "  /smart_photo паста с морепродуктами в синей тарелке\n"
            "  /smart_photo портрет девушки на закате\n"
            "  /smart_photo интерьер современного кафе\n\n"
            "Claude AI улучшит твой промпт до уровня профессионального фотографа!")
        return

    send(chat_id, f"Анализирую запрос: {query[:60]}...")

    try:
        from app.services.smart_prompts import smart_enhance
        result = smart_enhance(query)
        category = result["category"]
        enhanced = result["enhanced"]
        from_cache = result.get("from_cache", False)
        word_count = len(enhanced.split())

        category_ru = {
            "food": "Еда",
            "design": "Дизайн",
            "people": "Люди",
            "place": "Место",
            "object": "Объект",
            "abstract": "Абстракция",
        }.get(category, category.capitalize())

        cache_note = " (из кэша)" if from_cache else ""
        send(chat_id,
            f"Категория: {category_ru}{cache_note}\n"
            f"Профессиональный промпт сгенерирован!\n\n"
            f"Промпт ({word_count} слов):\n"
            f"<code>{enhanced[:300]}{'...' if len(enhanced) > 300 else ''}</code>\n\n"
            f"Создаю фото в премиум качестве...")

        # Generate photo using existing FLUX pipeline
        from app.services.replicate_image_gen import generate_images_replicate
        urls = generate_images_replicate(enhanced)
        url = urls[0] if urls else None

        if url:
            _send_photo_url(chat_id, url, f"Smart Photo: {query[:60]}")
            send(chat_id,
                "Готово!\n"
                "Совет: можешь скопировать промпт и попробовать в любом AI генераторе!")
        else:
            send(chat_id, f"Промпт готов, но генерация фото не удалась.\n\nПромпт:\n{enhanced}")
    except Exception as exc:
        logger.exception("cmd_smart_photo failed chat=%s", chat_id)
        send(chat_id, f"Ошибка: {translate_exception(exc)}")


def cmd_pro_food(chat_id: str, query: str) -> None:
    """L.3: Food-specific professional photo prompt shortcut."""
    if not query:
        send(chat_id,
            "Использование: /pro_food <блюдо>\n\n"
            "Примеры:\n"
            "  /pro_food борщ со сметаной\n"
            "  /pro_food тирамису с ягодами\n\n"
            "Специализированный промпт для food photography!")
        return

    send(chat_id, f"Создаю food photography промпт: {query[:50]}...")

    try:
        from app.services.smart_prompts import enhance_food_prompt
        from app.services.replicate_image_gen import generate_images_replicate

        enhanced = enhance_food_prompt(query)
        send(chat_id,
            f"Промпт готов:\n<code>{enhanced[:300]}</code>\n\n"
            "Генерирую фото...")

        urls = generate_images_replicate(enhanced)
        url = urls[0] if urls else None

        if url:
            _send_photo_url(chat_id, url, f"Food photo: {query[:60]}")
        else:
            send(chat_id, f"Промпт создан, фото не удалось.\nПромпт: {enhanced}")
    except Exception as exc:
        logger.exception("cmd_pro_food failed chat=%s", chat_id)
        send(chat_id, f"Ошибка: {translate_exception(exc)}")


# ── Landing Brief 2.0 (Block L.4) ────────────────────────────────────────────

def cmd_landing_brief(chat_id: str, query: str) -> None:
    """L.4: Start multi-step landing brief session."""
    from app.services.landing_brief_session import LandingBriefSession
    # Cancel existing active session if any
    existing = LandingBriefSession.find_active(chat_id)
    if existing:
        existing.cancel()

    session = LandingBriefSession(user_id=chat_id, chat_id=chat_id)
    question = session.get_current_question()
    send(chat_id,
        f"Запускаю Landing Brief 2.0!\n\n"
        f"Буду задавать 8 вопросов. После ответа Claude AI создаст "
        f"уникальный контент для лендинга.\n\n"
        f"Для отмены напиши: /cancel\n\n"
        f"{question}")


def cmd_landing_brief_answer(chat_id: str, text: str) -> bool:
    """
    Handle user's answer to landing brief question.
    Returns True if answer was processed (session is active).
    """
    from app.services.landing_brief_session import LandingBriefSession
    session = LandingBriefSession.find_active(chat_id)
    if not session:
        return False

    completed, next_q = session.advance(text)

    if completed:
        send(chat_id,
            "Бриф собран! Генерирую лендинг через Claude AI...\n\n"
            "Это займёт 15-30 секунд...")
        try:
            from app.services.landing_content_generator import generate_landing_content
            from app.services.landing_generator_v2 import save_landing_v2
            brief = session.get_brief_data()
            content = generate_landing_content(brief)
            path = save_landing_v2(brief, content)
            biz = brief.get("business_name", "Бизнес")
            style = brief.get("style", "современный")
            send(chat_id,
                f"Лендинг \"{biz}\" готов!\n\n"
                f"Файл: {path}\n"
                f"Открой в браузере: двойной клик\n\n"
                f"Стиль: {style}\n"
                f"Уникальный контент через Claude API\n"
                f"Mobile responsive\n"
                f"Кликабельные кнопки\n\n"
                f"Можешь продать клиенту за $50-200!")
        except Exception as exc:
            logger.exception("cmd_landing_brief generation failed chat=%s", chat_id)
            send(chat_id, f"Ошибка генерации: {translate_exception(exc)}")
    else:
        send(chat_id, next_q)

    return True


def cmd_cancel(chat_id: str, query: str) -> None:
    """L.4: Cancel active landing brief session."""
    from app.services.landing_brief_session import LandingBriefSession
    session = LandingBriefSession.find_active(chat_id)
    if session:
        session.cancel()
        send(chat_id, "Сессия Landing Brief отменена.\n\nНачать снова: /landing_brief")
    else:
        send(chat_id, "Нет активной сессии.")


def cmd_landing_demo(chat_id: str, query: str) -> None:
    """L.4: Show landing brief examples."""
    send(chat_id,
        "Примеры использования Landing Brief 2.0:\n\n"
        "Кафе/Ресторан:\n"
        "Название: Cafe Lapin\n"
        "Аудитория: семьи 25-45 лет\n"
        "Продукт: авторская кухня\n"
        "Стиль: тёплая / luxury\n\n"
        "Фитнес-студия:\n"
        "Название: FitSpace\n"
        "Аудитория: молодёжь 20-35\n"
        "Продукт: групповые тренировки\n"
        "Стиль: health\n\n"
        "IT-компания:\n"
        "Название: DevCraft\n"
        "Аудитория: B2B, стартапы\n"
        "Продукт: разработка MVP\n"
        "Стиль: современный / деловой\n\n"
        "Запусти: /landing_brief")


def cmd_simple_game(chat_id: str, query: str) -> None:
    """F.4: Generate HTML5 game from type name."""
    from app.services.game_generator import GAMES, GAME_LABELS, generate_game

    if not query:
        lines = ["🎮 Использование: /simple_game <тип>\n\nДоступные игры:"]
        for key, label in GAME_LABELS.items():
            lines.append(f"  {key} — {label}")
        send(chat_id, "\n".join(lines))
        return

    game_type = query.lower().strip()

    try:
        path = generate_game(game_type, str(ROOT / "state" / "games"))
        label = GAME_LABELS.get(game_type, game_type)
        send(chat_id,
            f"🎮 Игра {label} готова!\n\n"
            f"📁 Файл: {path}\n\n"
            f"Открой в браузере двойным кликом.")
    except ValueError as exc:
        send(chat_id, f"❌ {exc}")
    except Exception as exc:
        send(chat_id, f"❌ Ошибка: {translate_exception(exc)}")


def handle_command(chat_id: str, cmd: str, query: str, state: Dict[str, Any]) -> None:
    if cmd in ["/start", "/smart", "/smart_help", "/help"]:
        send(chat_id, HELP_TEXT)
        return

    if cmd == "/capabilities":
        cmd_capabilities(chat_id)
        return

    if cmd == "/status":
        try:
            import sys as _sys
            _root = str(Path(__file__).parent.parent)
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.provider_health import check_all_providers, get_healthy_provider
            statuses = check_all_providers(use_cache=False)
            primary = get_healthy_provider(use_cache=False)
            lines = ["🔌 Статус AI провайдеров:"]
            icons = {True: "✅", False: "🔴"}
            for p, ok in statuses.items():
                lines.append(f"  {icons[ok]} {p}")
            lines.append(f"\nАктивный провайдер: {primary or 'нет доступных'}")
            # Mesh stats
            mesh_history = state.get("mesh_history", [])
            mesh_enabled = state.get("mesh_enabled", True)
            lines.append(f"\n🧠 Smart Router: {'✅ вкл' if mesh_enabled else '⏸ выкл'}")
            lines.append(f"   Выполнений mesh: {len(mesh_history)}")
            send(chat_id, "\n".join(lines))
        except Exception as e:
            send(chat_id, f"⚠️ Status check failed: {e}")
        return

    if cmd == "/stats":
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.structured_logger import build_daily_report
            report = build_daily_report()
            lines = [
                f"📊 Статистика за сегодня ({report['date']}):",
                f"Всего запросов: {report['total_events']}",
                f"Ошибок: {report['errors']}",
                f"Avg latency: {report['avg_latency_ms']}ms",
                "\nПо интентам:",
            ] + [f"  {k}: {v}" for k, v in sorted(report["intents"].items(), key=lambda x: -x[1])]
            send(chat_id, "\n".join(lines))
        except Exception as e:
            send(chat_id, f"⚠️ Stats unavailable: {e}")
        return

    if cmd == "/clear":
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.chat_history import clear_history
            count = clear_history(chat_id)
            send(chat_id, f"🗑 История очищена. Удалено {count} сообщений.")
        except Exception as e:
            send(chat_id, f"⚠️ Не удалось очистить историю: {e}")
        return

    if cmd == "/history":
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.chat_history import format_history_for_display
            text = format_history_for_display(chat_id, n=10)
            send(chat_id, "📜 Последние 10 сообщений:\n\n" + text)
        except Exception as e:
            send(chat_id, f"⚠️ История недоступна: {e}")
        return

    if cmd == "/memory_stats":
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.chat_history import count_history
            count = count_history(chat_id)
            send(chat_id, f"🧠 Памяти о тебе: {count} сообщений сохранено.")
        except Exception as e:
            send(chat_id, f"⚠️ Не удалось получить статистику: {e}")
        return

    # H8.4: Real action commands
    if cmd == "/restart_backend":
        cmd_restart_backend(chat_id)
        return

    if cmd == "/restart_bot":
        cmd_restart_bot(chat_id)
        return

    # H8.5: Night Autonomy commands
    if cmd == "/night_status":
        cmd_night_status(chat_id)
        return

    if cmd == "/night_now":
        cmd_night_now(chat_id)
        return

    if cmd == "/cancel":
        pending_id = state.get("pending_task_id")
        state["pending"] = None
        state["pending_task_id"] = None
        save_state(state)
        send(chat_id, "❌ Задача отменена." if pending_id else "Нет активной задачи для отмены.")
        return

    if cmd == "/mode":
        mode = query.strip().lower()
        if mode not in ["simple", "debug", "auto"]:
            send(chat_id, "Доступные режимы: /mode simple, /mode debug, /mode auto")
            return
        state["mode"] = mode
        save_state(state)
        send(chat_id, f"✅ Режим изменён: {mode}")
        return

    if cmd == "/smart_health":
        send(chat_id, human_health(get_health()))
        return

    if cmd == "/debug_health":
        send(chat_id, "🧪 Debug health:\n" + pretty(get_health()))
        return

    if cmd == "/plan":
        if not query:
            send(chat_id, "Укажи задачу после /plan:\n/plan исследуй тему X и затем создай таблицу")
            return
        _handle_compound_task(chat_id, query, state, plan_only=True)
        return

    if cmd == "/tasks":
        last_plan = state.get("last_plan")
        if not last_plan:
            send(chat_id, "Нет активных задач. Используй /plan <задача> чтобы запланировать.")
            return
        from app.services.task_planner import TaskPlan, TaskStep
        steps = [
            TaskStep(
                step_number=s["step_number"],
                intent=s["intent"],
                query=s["query"],
                description=s["description"],
                done=s.get("done", False),
                result=s.get("result"),
            )
            for s in last_plan.get("steps", [])
        ]
        plan = TaskPlan(
            original_query=last_plan.get("original_query", ""),
            steps=steps,
            completed=last_plan.get("completed", False),
        )
        send(chat_id, plan.summary())
        return

    if cmd == "/agents":
        try:
            _root = str(Path(__file__).parent.parent)
            import sys as _sys
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from app.services.agent_registry import AGENTS, check_all_agents_health
            health = check_all_agents_health()
            lines = ["🔌 Статус всех агентов:\n"]
            type_icons = {"api_tool": "🔧", "llm": "🧠", "llm_tool": "🧠",
                          "external_workflow": "🔗", "local_tool": "💾", "external_app": "🖥"}
            for aid, cfg in AGENTS.items():
                icon = "✅" if health.get(aid) else ("⏸" if not cfg.get("available") else "🔴")
                t_icon = type_icons.get(cfg.get("type", ""), "🔹")
                label = cfg.get("label", aid)
                lines.append(f"  {t_icon} {icon} {label}")
            lines.append("\n⏸ = отключён  🔴 = недоступен  ✅ = готов")
            send(chat_id, "\n".join(lines))
        except Exception as e:
            send(chat_id, f"⚠️ Agents status failed: {e}")
        return

    if cmd == "/mesh":
        sub = query.strip().lower()
        if sub in ("on", "off"):
            state["mesh_enabled"] = (sub == "on")
            save_state(state)
            send(chat_id, f"🧠 Smart Router {'включён' if sub == 'on' else 'выключен'}.")
            return
        if sub == "debug":
            last_mesh = state.get("last_mesh_plan")
            if not last_mesh:
                send(chat_id, "Нет данных о последнем mesh execution.")
            else:
                send(chat_id, f"🔍 Последний mesh план:\n{last_mesh}")
            return
        if sub == "history":
            history = state.get("mesh_history", [])
            if not history:
                send(chat_id, "История mesh executions пуста.")
                return
            lines = ["📚 Последние mesh executions:"]
            for h in history[-5:]:
                lines.append(f"  • {h.get('query', '?')[:60]} → {h.get('agents', '?')}")
            send(chat_id, "\n".join(lines))
            return
        # Default: show interactive Mesh Control panel
        _send_mesh_control_panel(chat_id, state)
        return

    if cmd == "/cowork":
        _handle_cowork_command(chat_id, query.strip())
        return

    if cmd == "/diag":
        send(chat_id, _run_diag())
        return

    if cmd == "/n8n":
        _handle_n8n_command(chat_id, query.strip())
        return

    if cmd == "/remind":
        _handle_remind_command(chat_id, query.strip())
        return

    if cmd == "/schedule":
        _handle_schedule_command(chat_id, query.strip())
        return

    if cmd == "/brief":
        _handle_brief_command(chat_id, query.strip())
        return

    if cmd == "/improve":
        _handle_improve_command(chat_id, query.strip())
        return

    if cmd == "/errors":
        _handle_errors_command(chat_id, query.strip())
        return

    if cmd == "/logs":
        _handle_logs_command(chat_id, query.strip())
        return

    if cmd == "/selfcheck":
        _handle_selfcheck_command(chat_id)
        return

    if cmd == "/design":
        cmd_design(chat_id, query)
        return

    if cmd == "/figma_queue":
        cmd_figma_queue(chat_id, query)
        return

    if cmd == "/figma_status":
        cmd_figma_status(chat_id, query)
        return

    if cmd == "/figma_clear":
        cmd_figma_clear(chat_id, query)
        return

    if cmd == "/create_app":
        cmd_create_app(chat_id, query)
        return

    if cmd == "/create_simple":
        cmd_create_simple(chat_id, query)
        return

    if cmd == "/bolt_status":
        cmd_bolt_status(chat_id, query)
        return

    if cmd == "/bolt_open":
        cmd_bolt_open(chat_id, query)
        return

    if cmd == "/bolt_queue":
        cmd_bolt_queue(chat_id, query)
        return

    if cmd == "/smart_photo":
        cmd_smart_photo(chat_id, query)
        return

    if cmd == "/pro_food":
        cmd_pro_food(chat_id, query)
        return

    if cmd == "/landing_brief":
        cmd_landing_brief(chat_id, query)
        return

    if cmd == "/landing_demo":
        cmd_landing_demo(chat_id, query)
        return

    if cmd == "/cancel":
        cmd_cancel(chat_id, query)
        return

    if cmd == "/landing":
        cmd_landing(chat_id, query)
        return

    if cmd == "/simple_game":
        cmd_simple_game(chat_id, query)
        return

    if cmd == "/create_persona":
        try:
            _r_m = str(Path(__file__).parent.parent)
            import sys as _sys_m
            if _r_m not in _sys_m.path:
                _sys_m.path.insert(0, _r_m)
            from app.handlers.persona_handler import (
                init_bot as _persona_init,
                handle_create_persona as _hcp,
            )
            _persona_init(send, _send_photo_url)
            _hcp(int(chat_id))
        except Exception as _cpe:
            logger.exception("/create_persona failed chat=%s", chat_id)
            send(chat_id, f"Ошибка создания персоны: {translate_exception(_cpe)}")
        return

    if cmd == "/cancel_persona":
        try:
            _r_m2 = str(Path(__file__).parent.parent)
            import sys as _sys_m2
            if _r_m2 not in _sys_m2.path:
                _sys_m2.path.insert(0, _r_m2)
            from app.handlers.persona_handler import (
                init_bot as _persona_init2,
                handle_cancel_persona as _hcancel,
            )
            _persona_init2(send, _send_photo_url)
            _hcancel(int(chat_id))
        except Exception as _cpe2:
            logger.exception("/cancel_persona failed chat=%s", chat_id)
            send(chat_id, f"Ошибка отмены персоны: {translate_exception(_cpe2)}")
        return

    if cmd == "/train_lora":
        try:
            _r_tl = str(Path(__file__).parent.parent)
            import sys as _sys_tl
            if _r_tl not in _sys_tl.path:
                _sys_tl.path.insert(0, _r_tl)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_tl,
                handle_train_lora as _htl,
            )
            _persona_init_tl(send, _send_photo_url)
            _htl(int(chat_id), query)
        except Exception as _tle:
            logger.exception("/train_lora failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_tle)}")
        return

    if cmd == "/lora_status":
        try:
            _r_ls = str(Path(__file__).parent.parent)
            import sys as _sys_ls
            if _r_ls not in _sys_ls.path:
                _sys_ls.path.insert(0, _r_ls)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_ls,
                handle_lora_status as _hls,
            )
            _persona_init_ls(send, _send_photo_url)
            _hls(int(chat_id), query)
        except Exception as _lse:
            logger.exception("/lora_status failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_lse)}")
        return

    if cmd == "/list_loras":
        try:
            _r_ll = str(Path(__file__).parent.parent)
            import sys as _sys_ll
            if _r_ll not in _sys_ll.path:
                _sys_ll.path.insert(0, _r_ll)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_ll,
                handle_list_loras as _hll,
            )
            _persona_init_ll(send, _send_photo_url)
            _hll(int(chat_id))
        except Exception as _lle:
            logger.exception("/list_loras failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_lle)}")
        return

    if cmd == "/cancel_lora":
        try:
            _r_cl = str(Path(__file__).parent.parent)
            import sys as _sys_cl
            if _r_cl not in _sys_cl.path:
                _sys_cl.path.insert(0, _r_cl)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_cl,
                handle_cancel_lora as _hcl,
            )
            _persona_init_cl(send, _send_photo_url)
            _hcl(int(chat_id), query)
        except Exception as _cle:
            logger.exception("/cancel_lora failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_cle)}")
        return

    if cmd == "/persona_photo":
        try:
            _r_pp = str(Path(__file__).parent.parent)
            import sys as _sys_pp
            if _r_pp not in _sys_pp.path:
                _sys_pp.path.insert(0, _r_pp)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_pp,
                handle_persona_photo as _hpp,
            )
            _persona_init_pp(send, _send_photo_url)
            _hpp(int(chat_id), query)
        except Exception as _ppe:
            logger.exception("/persona_photo failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_ppe)}")
        return

    if cmd == "/persona_video":
        # Phase C: threaded dispatch with per-chat lock and progress stages.
        _persona_video_dispatch(chat_id, query)
        return

    if cmd == "/persona_video_redo":
        _persona_video_dispatch(chat_id, query, command="redo")
        return

    # ── Block M.2.5 face-swap batch commands ────────────────────────────────
    if cmd == "/swapbatch":
        _swapbatch_dispatch(chat_id, "help")
        return
    if cmd == "/swapbatch_source":
        _swapbatch_dispatch(chat_id, "source")
        return
    if cmd == "/swapbatch_batch":
        _swapbatch_dispatch(chat_id, "batch")
        return
    if cmd == "/swapbatch_go":
        _swapbatch_dispatch(chat_id, "go")
        return
    if cmd == "/swapbatch_set_quality":
        _h_sq, _ = _swapbatch_get_handler()
        if _h_sq is None:
            send(str(chat_id), "⚠️ Модуль face-swap недоступен.")
        else:
            _swapbatch_apply_reply(
                str(chat_id), _h_sq.handle_set_quality(int(chat_id), query)
            )
        return
    if cmd == "/swapbatch_animate_yes":
        _swapbatch_dispatch(chat_id, "animate_yes")
        return
    if cmd == "/swapbatch_animate_custom":
        _swapbatch_dispatch(chat_id, "animate_custom")
        return
    if cmd == "/swapbatch_animate_no":
        _swapbatch_dispatch(chat_id, "animate_no")
        return
    if cmd == "/swapbatch_no":
        _swapbatch_dispatch(chat_id, "no")
        return
    if cmd == "/swapbatch_confirm":
        _swapbatch_dispatch(chat_id, "confirm")
        return
    if cmd == "/swapbatch_retry":
        _swapbatch_dispatch(chat_id, "retry")
        return
    if cmd == "/swapbatch_apply_partial":
        _swapbatch_dispatch(chat_id, "apply_partial")
        return
    if cmd == "/swapbatch_apply_first":
        _swapbatch_dispatch(chat_id, "apply_first")
        return
    if cmd == "/swapbatch_cancel":
        _swapbatch_dispatch(chat_id, "cancel")
        return
    if cmd == "/swapbatch_status":
        _swapbatch_dispatch(chat_id, "status")
        return

    if cmd == "/persona_redo":
        try:
            _r_pr = str(Path(__file__).parent.parent)
            import sys as _sys_pr
            if _r_pr not in _sys_pr.path:
                _sys_pr.path.insert(0, _r_pr)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_pr,
                handle_persona_redo as _hpr,
            )
            _persona_init_pr(send, _send_photo_url)
            _hpr(int(chat_id), query)
        except Exception as _pre:
            logger.exception("/persona_redo failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_pre)}")
        return

    if cmd == "/persona_engine":
        try:
            _r_pe = str(Path(__file__).parent.parent)
            import sys as _sys_pe
            if _r_pe not in _sys_pe.path:
                _sys_pe.path.insert(0, _r_pe)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_pe,
                handle_persona_engine as _hpe,
            )
            _persona_init_pe(send, _send_photo_url)
            _hpe(int(chat_id), query)
        except Exception as _pee:
            logger.exception("/persona_engine failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_pee)}")
        return

    if cmd == "/me_seed":
        try:
            _r_ms = str(Path(__file__).parent.parent)
            import sys as _sys_ms
            if _r_ms not in _sys_ms.path:
                _sys_ms.path.insert(0, _r_ms)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_ms,
                handle_me_seed as _hms,
            )
            _persona_init_ms(send, _send_photo_url)
            _hms(int(chat_id))
        except Exception as _mse:
            logger.exception("/me_seed failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_mse)}")
        return

    if cmd == "/me_done":
        try:
            _r_md = str(Path(__file__).parent.parent)
            import sys as _sys_md
            if _r_md not in _sys_md.path:
                _sys_md.path.insert(0, _r_md)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_md,
                handle_me_done as _hmd,
            )
            _persona_init_md(send, _send_photo_url)
            _hmd(int(chat_id))
        except Exception as _mde:
            logger.exception("/me_done failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_mde)}")
        return

    if cmd == "/me_swap_photo":
        try:
            _r_msp = str(Path(__file__).parent.parent)
            import sys as _sys_msp
            if _r_msp not in _sys_msp.path:
                _sys_msp.path.insert(0, _r_msp)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_msp,
                handle_me_swap_photo as _hmsp,
            )
            _persona_init_msp(send, _send_photo_url)
            _hmsp(int(chat_id), query)
        except Exception as _mspe:
            logger.exception("/me_swap_photo failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_mspe)}")
        return

    if cmd == "/me_swap_video":
        try:
            _r_msv = str(Path(__file__).parent.parent)
            import sys as _sys_msv
            if _r_msv not in _sys_msv.path:
                _sys_msv.path.insert(0, _r_msv)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_msv,
                handle_me_swap_video as _hmsv,
            )
            _persona_init_msv(send, _send_photo_url)
            _hmsv(int(chat_id), query)
        except Exception as _msve:
            logger.exception("/me_swap_video failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_msve)}")
        return

    if cmd == "/costs":
        try:
            _r_cs = str(Path(__file__).parent.parent)
            import sys as _sys_cs
            if _r_cs not in _sys_cs.path:
                _sys_cs.path.insert(0, _r_cs)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_cs,
                handle_costs as _hcs,
            )
            _persona_init_cs(send, _send_photo_url)
            _hcs(int(chat_id))
        except Exception as _cse:
            logger.exception("/costs failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_cse)}")
        return

    if cmd == "/history":
        try:
            _r_hs = str(Path(__file__).parent.parent)
            import sys as _sys_hs
            if _r_hs not in _sys_hs.path:
                _sys_hs.path.insert(0, _r_hs)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_hs,
                handle_history as _hhs,
            )
            _persona_init_hs(send, _send_photo_url)
            _hhs(int(chat_id), query)
        except Exception as _hse:
            logger.exception("/history failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_hse)}")
        return

    if cmd == "/persona_batch":
        try:
            _r_pb = str(Path(__file__).parent.parent)
            import sys as _sys_pb
            if _r_pb not in _sys_pb.path:
                _sys_pb.path.insert(0, _r_pb)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_pb,
                handle_persona_batch as _hpb,
            )
            _persona_init_pb(send, _send_photo_url)
            _hpb(int(chat_id), query)
        except Exception as _pbe:
            logger.exception("/persona_batch failed chat=%s", chat_id)
            send(chat_id, f"Ошибка: {translate_exception(_pbe)}")
        return

    mapped = {
        "/brain": "brain",
        "/research": "research",
        "/engineer": "engineer",
        "/table": "table",
        "/gen": "generate",
        "/job": "job",
    }
    if cmd in mapped:
        run_intent(chat_id, {"intent": mapped[cmd], "query": query}, state)
        return

    # ── Photo Studio commands (Block H4) ─────────────────────────────────────
    try:
        _r_ps = str(Path(__file__).parent.parent)
        import sys as _sys_ps
        if _r_ps not in _sys_ps.path:
            _sys_ps.path.insert(0, _r_ps)
        from tools.photo_studio_telegram import (
            handle_menu_photo, handle_social_post, handle_menu_book, handle_dish_styles,
            handle_party_promo, handle_invite_card, handle_event_photo, handle_party_themes,
            handle_faceswap_start, handle_enhance_start, handle_me_into_start,
            handle_lora_train_start, handle_lora_done, handle_lora_list,
            handle_lora_status, handle_lora_delete,
            handle_me_as, handle_me_in, handle_me_with, handle_me_style,
            handle_me_roles, handle_me_places, handle_me_styles,
        )
        _ps_cmd_map = {
            "/menu_photo":   lambda: handle_menu_photo(chat_id, query, send, _send_photo_url),
            "/social_post":  lambda: handle_social_post(chat_id, query, send, _send_photo_url),
            "/menu_book":    lambda: handle_menu_book(chat_id, query, send, _send_photo_url),
            "/dish_styles":  lambda: handle_dish_styles(chat_id, send),
            "/party_promo":  lambda: handle_party_promo(chat_id, query, send, _send_photo_url),
            "/invite_card":  lambda: handle_invite_card(chat_id, query, send, _send_photo_url),
            "/event_photo":  lambda: handle_event_photo(chat_id, query, send, _send_photo_url),
            "/party_themes": lambda: handle_party_themes(chat_id, send),
            "/faceswap":     lambda: handle_faceswap_start(chat_id, send),
            "/enhance":      lambda: handle_enhance_start(chat_id, send),
            "/me_into":      lambda: handle_me_into_start(chat_id, send),
            "/lora_train":   lambda: handle_lora_train_start(chat_id, send),
            "/lora_done":    lambda: handle_lora_done(chat_id, send),
            "/lora_list":    lambda: handle_lora_list(chat_id, send),
            "/lora_status":  lambda: handle_lora_status(chat_id, query, send),
            "/lora_delete":  lambda: handle_lora_delete(chat_id, query, send),
            "/me_as":        lambda: handle_me_as(chat_id, query, send, _send_photo_url),
            "/me_in":        lambda: handle_me_in(chat_id, query, send, _send_photo_url),
            "/me_with":      lambda: handle_me_with(chat_id, query, send, _send_photo_url),
            "/me_style":     lambda: handle_me_style(chat_id, query, send, _send_photo_url),
            "/me_roles":     lambda: handle_me_roles(chat_id, send),
            "/me_places":    lambda: handle_me_places(chat_id, send),
            "/me_styles":    lambda: handle_me_styles(chat_id, send),
        }
        if cmd in _ps_cmd_map:
            _ps_cmd_map[cmd]()
            return
    except Exception as _ps_err:
        logger.exception("Photo Studio command failed chat=%s", chat_id)
        send(chat_id, f"❌ Photo Studio error: {translate_exception(_ps_err)}")
        return

    send(chat_id, "Не знаю такую команду. Напиши /smart_help")


_INCOMING_DIR = ROOT / "state" / "incoming_files"
_INCOMING_DIR.mkdir(parents=True, exist_ok=True)


def _download_telegram_file(file_id: str, filename: str) -> Optional[str]:
    """Download a Telegram file by file_id. Returns local path or None."""
    info = tg_call("getFile", {"file_id": file_id})
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    dest = _INCOMING_DIR / filename
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        return str(dest)
    except Exception:
        return None


def _parse_file_safe(local_path: str, mime_type: str = "") -> Dict[str, Any]:
    """Parse a file, gracefully handle missing parsers."""
    try:
        _root = str(Path(__file__).parent.parent)
        import sys as _sys
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from app.services.file_parsers import parse_file, summarize_parse_result
        result = parse_file(local_path, mime_type)
        result["_summary"] = summarize_parse_result(result, Path(local_path).name)
        return result
    except Exception as e:
        return {"ok": False, "text": "", "tables": [], "metadata": {}, "error": str(e), "_summary": f"Ошибка: {e}"}


def classify_file_caption(caption: str) -> Dict[str, Any]:
    """Route a file caption to the appropriate file intent.
    Called when we already know a file was received — applies liberal matching.
    Defaults to summarize_file for any non-empty caption that doesn't match."""
    t = low(norm(caption))

    # Accounting (most specific — financial documents)
    if any(x in t for x in [
        "бухгалтер", "счёт-фактур", "накладная", "платёжка",
        "приход", "расход", "счета и расходы", "финансы", "финансов",
        "оплата", "счёт на оплату", "акт ", "акт\n",
    ]):
        return {"intent": "accounting", "query": caption}

    # Extract structured data
    if any(x in t for x in [
        "извлеки", "вытащи", "достань", "extract", "суммы из",
        "даты из", "данные из", "получи данные", "список из",
    ]):
        return {"intent": "extract_from_file", "query": caption}

    # Ask a question about the file
    if any(x in t for x in [
        "найди в", "есть ли", "где в", "в этом файле",
        "из этого файла", "содержит ли", "отвечает ли",
    ]):
        return {"intent": "ask_about_file", "query": caption}

    # Summarize — broad natural-language captions
    if any(x in t for x in [
        "просмотри", "посмотри", "взгляни", "что это", "что за",
        "суммируй", "кратко", "о чём", "о чем", "перескажи",
        "расскажи про", "расскажи о", "расскажи что", "объясни",
        "что в", "что тут", "проверь", "прочитай", "прочти",
        "analyse", "analyze", "summarize", "review", "describe",
        "что здесь", "опиши", "про эти файлы", "про этот файл",
    ]):
        return {"intent": "summarize_file", "query": caption}

    # Default: any caption with a file → summarize
    return {"intent": "summarize_file", "query": caption or "суммируй файл"}


def _extract_file_from_msg(msg: Dict[str, Any]):
    """Return (file_id, filename, mime_type) from a message or None."""
    doc = msg.get("document")
    photos = msg.get("photo")
    if doc:
        fid = doc.get("file_id", "")
        fname = doc.get("file_name") or f"doc_{fid[:8]}"
        mime = doc.get("mime_type", "")
        return fid, fname, mime
    if photos:
        photo = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
        fid = photo.get("file_id", "")
        return fid, f"photo_{fid[:8]}.jpg", "image/jpeg"
    return None


def _handle_file_message(chat_id: str, msg: Dict[str, Any], state: Dict[str, Any]) -> None:
    """Handle incoming document or photo from Telegram (including forwards)."""
    # Support forwarded messages with attachments
    source_msg = msg
    if msg.get("forward_from") or msg.get("forward_sender_name") or msg.get("forward_origin"):
        pass  # use msg itself — Telegram includes the file in the forwarded message

    extracted = _extract_file_from_msg(source_msg)
    if not extracted:
        return
    file_id, filename, mime_type = extracted

    if not file_id:
        return

    caption = (msg.get("caption") or "").strip()
    is_forward = bool(msg.get("forward_from") or msg.get("forward_sender_name") or msg.get("forward_origin"))

    # ── Me-Persona seed collection (/me_seed flow) ────────────────────────────
    is_photo = bool(msg.get("photo"))
    if is_photo:
        try:
            _r_mpc = str(Path(__file__).parent.parent)
            import sys as _sys_mpc
            if _r_mpc not in _sys_mpc.path:
                _sys_mpc.path.insert(0, _r_mpc)
            from tools.photo_studio_telegram import get_telegram_photo_url as _gtu_mpc
            from app.handlers.persona_handler import (
                init_bot as _persona_init_mpc,
                handle_photo_message as _hpm,
            )
            _persona_init_mpc(send, _send_photo_url)
            _photo_url_mpc = _gtu_mpc(file_id, BOT_TOKEN)
            if _photo_url_mpc and _hpm(int(chat_id), _photo_url_mpc):
                return
        except Exception as _mpc_err:
            print(f"[MPC] me_seed photo error: {_mpc_err}", flush=True)

    # ── Multi-step Photo Studio flow (H4.3/H4.4) ─────────────────────────────
    if is_photo:
        try:
            _r_pst = str(Path(__file__).parent.parent)
            import sys as _sys_pst
            if _r_pst not in _sys_pst.path:
                _sys_pst.path.insert(0, _r_pst)
            from tools.photo_studio_telegram import (
                handle_faceswap_photo_step, get_telegram_photo_url, load_conv,
            )
            conv = load_conv(chat_id)
            if conv.get("step") in (
                "faceswap_source", "faceswap_target", "enhance_upload",
                "meinto_target", "lora_collecting"
            ):
                photo_url = get_telegram_photo_url(file_id, BOT_TOKEN)
                if photo_url:
                    consumed = handle_faceswap_photo_step(chat_id, photo_url, send, _send_photo_url)
                    if consumed:
                        return
        except Exception as _pst_err:
            print(f"[PST] photo step error: {_pst_err}", flush=True)

    send(chat_id, f"📥 Получаю файл: {filename}..." + (" (пересланное сообщение)" if is_forward else ""))
    local_path = _download_telegram_file(file_id, filename)
    if not local_path:
        send(chat_id, "❌ Не смог скачать файл. Попробуй отправить ещё раз.")
        return

    result = _parse_file_safe(local_path, mime_type)

    state["last_uploaded_file"] = {
        "path": local_path,
        "filename": filename,
        "mime_type": mime_type,
        "parse_result": {k: v for k, v in result.items() if k not in ("tables",)},
    }
    save_state(state)

    summary = result.get("_summary", f"📄 {filename}")
    text_preview = (result.get("text") or "")[:300]
    preview_line = f"\n\n📝 Превью:\n{text_preview}..." if text_preview else ""
    action_hint = "\n\nЧто сделать?\n• суммируй файл\n• извлеки данные (суммы, даты, контрагенты)\n• задай вопрос по файлу"

    if caption:
        # Phase 27: if it's a photo and caption is a question → Vision + question
        is_photo = bool(msg.get("photo"))
        if is_photo and local_path:
            try:
                _root_v = str(Path(__file__).parent.parent)
                import sys as _sys_v
                if _root_v not in _sys_v.path:
                    _sys_v.path.insert(0, _root_v)
                from app.services.vision import analyze_image, is_vision_supported
                if is_vision_supported():
                    send(chat_id, "🖼 Анализирую изображение...")
                    analysis = analyze_image(local_path, caption)
                    send(chat_id, f"🖼 Анализ изображения:\n\n{analysis}")
                    return
            except Exception:
                pass
        # Use file-aware caption router — never falls into research/table/etc.
        pack = classify_file_caption(caption)
        run_intent(chat_id, pack, state)
    else:
        # Phase 27: photo without caption → Vision analysis
        is_photo = bool(msg.get("photo"))
        if is_photo and local_path:
            try:
                _root_v2 = str(Path(__file__).parent.parent)
                import sys as _sys_v2
                if _root_v2 not in _sys_v2.path:
                    _sys_v2.path.insert(0, _root_v2)
                from app.services.vision import analyze_image, is_vision_supported
                if is_vision_supported():
                    send(chat_id, "🖼 Анализирую изображение...")
                    analysis = analyze_image(local_path)
                    send(chat_id, f"🖼 Анализ изображения:\n\n{analysis}")
                    return
            except Exception:
                pass
        send(chat_id, summary + preview_line + action_hint)


def _handle_file_intent(chat_id: str, intent: str, query: str, state: Dict[str, Any]) -> None:
    """Handle file-related intents using last_uploaded_file context."""
    file_info = state.get("last_uploaded_file")
    if not file_info:
        send(chat_id, "📂 Сначала отправь файл (документ или фото).")
        return

    filename = file_info.get("filename", "файл")
    parse_result = file_info.get("parse_result") or {}
    file_text = parse_result.get("text", "")
    if not file_text:
        send(chat_id, f"⚠️ Файл {filename} не содержит извлечённого текста. Попробуй другой формат.")
        return

    # Build LLM prompt based on intent
    if intent == "summarize_file":
        system_instruction = f"Сделай краткое резюме содержимого файла '{filename}' на русском языке. Максимум 5-7 предложений."
    elif intent == "extract_from_file":
        system_instruction = (
            f"Из файла '{filename}' извлеки структурированные данные: "
            "даты, суммы, контрагентов, типы операций (приход/расход). "
            "Верни JSON: {{items: [{{date, counterparty, amount, type, description}}]}}"
        )
    elif intent == "accounting":
        system_instruction = (
            f"Это файл бухгалтерии '{filename}'. Извлеки все финансовые операции. "
            "Для каждой: дата, контрагент, сумма, тип (приход/расход/НДС). "
            "Верни в формате JSON: {{operations: [{{date, counterparty, amount, type, note}}]}}"
        )
    else:  # ask_about_file
        system_instruction = f"Ответь на вопрос по содержимому файла '{filename}': {query}"

    send(chat_id, f"🧠 Анализирую {filename}...")
    data = backend_post("/api/jarvis/tools/internet/research", {
        "query": f"{system_instruction}\n\nСодержимое файла:\n{file_text[:3000]}"
    }, timeout=120)

    if data.get("_error"):
        send(chat_id, f"❌ Не смог проанализировать: {data['_error']}")
        return

    answer = data.get("answer") or "Не смог извлечь данные."
    send(chat_id, f"📄 Результат анализа {filename}:\n\n{answer}")


def _log(intent: str, query: str, user_id: str, result: str = "", latency: int = 0, error: str = "") -> None:
    try:
        import sys
        import os as _os
        # Add project root to path for import when bot runs standalone
        _root = str(Path(__file__).parent.parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from app.services.structured_logger import log_event
        log_event(intent=intent, query=query, user_id=user_id,
                  result_summary=result, latency_ms=latency, error=error)
    except Exception:
        pass  # logging must never crash the bot


def handle(chat_id: str, text: str) -> None:
    if str(chat_id) != ALLOWED_CHAT_ID:
        send(chat_id, "Access denied.")
        return

    state = load_state()
    t0 = time.time()
    pack = classify_message(text, state)
    intent = pack.get("intent", "unknown")

    if pack.get("intent") == "command":
        handle_command(chat_id, pack.get("command", ""), pack.get("query", ""), state)
    else:
        # ── LoRA multi-step text state (H4.4) ─────────────────────────────────
        try:
            _r_lts = str(Path(__file__).parent.parent)
            import sys as _sys_lts
            if _r_lts not in _sys_lts.path:
                _sys_lts.path.insert(0, _r_lts)
            from tools.photo_studio_telegram import (
                handle_lora_name_text, handle_lora_trigger_text, load_conv,
            )
            conv = load_conv(chat_id)
            lora_step = conv.get("step")
            if lora_step == "lora_name":
                if handle_lora_name_text(chat_id, text, send):
                    return
            elif lora_step == "lora_trigger":
                if handle_lora_trigger_text(chat_id, text, send):
                    return
        except Exception as _lts_err:
            print(f"[LoRA:text] error: {_lts_err}", flush=True)

        # ── Persona Creator (M.1.1) — check active dialog before routing ─────────
        try:
            _r_pa = str(Path(__file__).parent.parent)
            import sys as _sys_pa
            if _r_pa not in _sys_pa.path:
                _sys_pa.path.insert(0, _r_pa)
            from app.handlers.persona_handler import (
                init_bot as _persona_init_pa,
                handle_persona_answer as _hpa,
            )
            _persona_init_pa(send, _send_photo_url)
            if _hpa(int(chat_id), text):
                return
        except Exception as _pa_err:
            print(f"[PersonaHandler] error: {_pa_err}", flush=True)

        # ── Landing Brief 2.0 (L.4) — check active session before routing ────────
        try:
            if cmd_landing_brief_answer(chat_id, text):
                return
        except Exception as _lb_err:
            print(f"[LandingBrief] error: {_lb_err}", flush=True)

        # ── Smart Photo Router (H4.6) — only for non-command free text ────────
        if intent not in ("command",):
            try:
                from tools.photo_studio_telegram import handle_photo_router_text
                if handle_photo_router_text(chat_id, text, send, _send_photo_url):
                    return
            except Exception:
                pass

        run_intent(chat_id, pack, state)

    latency = int((time.time() - t0) * 1000)
    _log(intent=intent, query=text[:200], user_id=chat_id, latency=latency)
    # Save to per-user history (non-blocking, errors ignored)
    try:
        _root = str(Path(__file__).parent.parent)
        import sys as _sys
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from app.services.chat_history import add_to_history
        add_to_history(chat_id, "user", text[:500], intent=intent)
    except Exception:
        pass


def _cowork_delivery_callback(result: dict) -> None:
    """Called by CoworkWatcher when Cowork writes a result to outbox."""
    if not ALLOWED_CHAT_ID:
        return
    task_id = result.get("task_id", "?")
    status = result.get("status", "unknown")
    text_result = result.get("result", "")
    if status == "timeout":
        send(ALLOWED_CHAT_ID, text_result)
    elif status in ("done", "completed", "success"):
        msg = f"✅ Cowork завершил задачу {task_id[:8]}:\n\n{text_result}"
        send(ALLOWED_CHAT_ID, msg[:4000])
        try:
            import sys as _sys
            _r = str(Path(__file__).parent.parent)
            if _r not in _sys.path:
                _sys.path.insert(0, _r)
            from app.services.cowork_bridge import mark_result_processed
            mark_result_processed(task_id)
        except Exception:
            pass
    else:
        send(ALLOWED_CHAT_ID, f"ℹ️ Cowork ответил (статус={status}):\n{text_result[:3900]}")


def _heartbeat_thread() -> None:
    """Write timestamp every 30s to prove bot is alive."""
    import threading as _thr_hb
    while True:
        try:
            _HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _HEARTBEAT_FILE.write_text(str(int(time.time())), encoding="utf-8")
        except Exception:
            pass
        time.sleep(30)


def _check_backend_startup() -> None:
    result = http_json("GET", BACKEND + "/health", timeout=5)
    if result.get("_error"):
        port = BACKEND.rsplit(":", 1)[-1]
        print(
            f"\n⚠️  WARNING: Backend at {BACKEND} is DOWN\n"
            "   Bot will accept messages but most commands will fail.\n"
            f"   Start backend with: python -m uvicorn app.main:app --host 127.0.0.1 --port {port}\n",
            flush=True,
        )
    else:
        print(f"✅ Backend at {BACKEND} is UP.", flush=True)


def _extract_user_id(upd: Dict[str, Any]) -> Optional[int]:
    """Return ``from.id`` for a Telegram update, or ``None`` if absent."""
    cq = upd.get("callback_query") or {}
    if cq:
        raw = (cq.get("from") or {}).get("id")
    else:
        msg = upd.get("message") or upd.get("edited_message") or {}
        raw = (msg.get("from") or {}).get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_reply_chat_id(upd: Dict[str, Any]) -> Optional[str]:
    """Return the chat_id to address a reply back to the sender."""
    cq = upd.get("callback_query") or {}
    if cq:
        chat = (cq.get("message") or {}).get("chat") or {}
        cid = chat.get("id") or (cq.get("from") or {}).get("id")
    else:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id") or (msg.get("from") or {}).get("id")
    return str(cid) if cid is not None else None


def _whitelist_gate(upd: Dict[str, Any]) -> bool:
    """Return True if the update may proceed; False if it was rejected.

    Rejected updates get a single polite reply (REJECT_MESSAGE) and an INFO
    log line so ops can audit access attempts. Updates without an extractable
    user_id pass through unchanged — they're system / edge-case updates that
    pre-existing dispatch already handles.
    """
    user_id = _extract_user_id(upd)
    if user_id is None:
        return True
    if _whitelist.is_allowed(user_id):
        return True
    cq = upd.get("callback_query") or {}
    msg = upd.get("message") or upd.get("edited_message") or {}
    username = (
        (cq.get("from") or {}).get("username") if cq
        else (msg.get("from") or {}).get("username")
    )
    logger.info(
        "whitelist: rejected user_id=%s username=%s", user_id, username
    )
    print(
        f"[whitelist] rejected user_id={user_id} username={username!r}",
        flush=True,
    )
    chat_id = _extract_reply_chat_id(upd)
    if chat_id:
        try:
            send(chat_id, _whitelist.REJECT_MESSAGE)
        except Exception as _e:
            print(f"[whitelist] reject reply failed: {_e}", flush=True)
    try:
        _audit.audit_event(
            user_id=user_id,
            username=username,
            chat_id=str(chat_id) if chat_id else str(user_id),
            event="whitelist_rejected",
            details={"username": username},
        )
    except Exception as _e:
        print(f"[audit] whitelist_rejected event failed: {_e}", flush=True)
    return False


def _extract_audit_ctx(upd: Dict[str, Any]) -> tuple[Optional[int], Optional[str], str]:
    """Return (user_id, username, chat_id) for audit logging."""
    cq = upd.get("callback_query") or {}
    if cq:
        frm = cq.get("from") or {}
        chat = (cq.get("message") or {}).get("chat") or {}
    else:
        msg = upd.get("message") or upd.get("edited_message") or {}
        frm = msg.get("from") or {}
        chat = msg.get("chat") or {}
    try:
        uid: Optional[int] = int(frm.get("id")) if frm.get("id") is not None else None
    except (TypeError, ValueError):
        uid = None
    uname = frm.get("username")
    cid = str(chat.get("id") or frm.get("id") or "")
    return uid, uname, cid


# Specific commands that map to dedicated audit events (so the admin forward
# template can show context). Everything else logs as the generic "command".
_AUDIT_COMMAND_EVENT: Dict[str, tuple[str, Dict[str, Any]]] = {
    "/swapbatch_go": ("swapbatch_go", {}),
    "/swapbatch_animate_yes": ("animate_started", {"mode": "yes"}),
    "/swapbatch_animate_custom": ("animate_started", {"mode": "custom"}),
    "/swapbatch_no": ("animate_started", {"mode": "no"}),
}


def _audit_message(upd: Dict[str, Any]) -> None:
    """Emit an audit event for a message update if it carries a command."""
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    uid, uname, cid = _extract_audit_ctx(upd)
    if uid is None:
        return
    parts = text.split(maxsplit=1)
    cmd = parts[0].split("@", 1)[0]  # strip /cmd@botname → /cmd
    args = parts[1] if len(parts) > 1 else ""
    event, extra = _AUDIT_COMMAND_EVENT.get(cmd, ("command", {}))
    details: Dict[str, Any] = {"command": cmd}
    if args:
        details["args"] = args[:500]
    details.update(extra)
    try:
        _audit.audit_event(uid, uname, cid, event, details)
    except Exception as _e:
        print(f"[audit] command event failed: {_e}", flush=True)


# Latest @username seen per chat_id, captured at the dispatch layer (the only
# place the Telegram identity is available) so the async batch-completion hook
# in _swapbatch_run_phase can attribute cost to a readable name.
_USERNAME_BY_CHAT: Dict[str, Optional[str]] = {}


def _remember_identity(upd: Dict[str, Any]) -> None:
    """Cache the inbound user's @username keyed by chat_id (best-effort)."""
    uid, uname, cid = _extract_audit_ctx(upd)
    if cid:
        _USERNAME_BY_CHAT[str(cid)] = uname


def _cost_command_intercept(upd: Dict[str, Any]) -> bool:
    """Handle /my_stats and /admin_costs before normal dispatch.

    Runs after the whitelist gate, so any whitelisted user reaches /my_stats
    (it must work for non-admins, who are blocked by the ALLOWED_CHAT_ID guard
    in ``handle``). Returns True if the update was a cost command and consumed.
    """
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0]
    if cmd not in ("/my_stats", "/admin_costs"):
        return False
    uid, uname, cid = _extract_audit_ctx(upd)
    reply_to = cid or (str(uid) if uid is not None else "")
    if not reply_to:
        return True

    if cmd == "/my_stats":
        try:
            send(reply_to, _cost.format_my_stats_message(uid, uname))
        except Exception as _e:  # noqa: BLE001
            print(f"[cost] /my_stats failed: {_e}", flush=True)
        return True

    # /admin_costs — admin only.
    admin = _whitelist.load_admin_user_id()
    if admin is None or uid != admin:
        send(reply_to, "🚫 /admin_costs доступен только администратору.")
        return True
    try:
        send(reply_to, _cost.format_admin_costs_message())
    except Exception as _e:  # noqa: BLE001
        print(f"[cost] /admin_costs failed: {_e}", flush=True)
    return True


# ── Phase 4: unified LLM router (natural-language → tools) ───────────────────
# Plain (non-command) text is routed through the Claude tool_use router. This
# is purely additive: commands and a disabled/unavailable router fall back to
# the legacy ``handle`` dispatcher, so all existing behaviour is preserved.

_ROUTER_SINGLETON: Any = None
_ROUTER_BUILD_FAILED = False

# Phase-4 Step 2.7: per-chat conversation history for the router. Kept in
# process memory (no DB yet) and capped to the last N messages so token cost
# stays bounded. Each turn is stored as a (user, assistant) pair so the list is
# always a valid alternating prefix starting with a user turn.
_ROUTER_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
_ROUTER_HISTORY_MAX = 10  # last N messages (≈5 user/assistant exchanges)


def _router_history_get(chat_id: str) -> List[Dict[str, Any]]:
    """Return a copy of the stored history for ``chat_id`` (oldest-first)."""
    return list(_ROUTER_HISTORY.get(str(chat_id), []))


def _router_history_append(chat_id: str, user_text: str, assistant_text: str) -> None:
    """Append one (user, assistant) exchange, trimming to the last N messages.

    Only recorded when ``assistant_text`` is non-empty, so the stored history
    stays an even-length, user-first, alternating sequence — exactly what the
    Anthropic Messages API requires when it is replayed as ``conversation_history``.
    """
    if not assistant_text:
        return
    hist = _ROUTER_HISTORY.setdefault(str(chat_id), [])
    hist.append({"role": "user", "content": user_text})
    hist.append({"role": "assistant", "content": assistant_text})
    # Drop whole exchanges from the front so the list stays user-first.
    while len(hist) > _ROUTER_HISTORY_MAX:
        del hist[:2]


def _router_stats_backend(user_id: Optional[int], username: Optional[str]) -> str:
    """REAL ``get_user_stats`` backend — the same per-user cost table as /my_stats."""
    return _cost.format_my_stats_message(user_id, username)


def _router_research_backend(query: str) -> dict:
    """Adapter for the web_research tool — same endpoint as /research."""
    return backend_post("/api/jarvis/tools/internet/research", {"query": query}, timeout=240)


def _router_table_backend(query: str) -> dict:
    """Adapter for the build_table tool — same endpoint as /table."""
    return backend_post(
        "/api/jarvis/telegram-tools/internet-table",
        {"query": query, "max_results": 10, "send_to_telegram": True},
        timeout=300,
    )


def _persona_generate_backend(persona_id: str, prompt: str, count: int) -> List[str]:
    """Explicit stub for ``generate_persona_photo`` (graceful, never silent).

    A real backend exists — ``app.services.block_m1_persona.photo_generator
    .PhotoGenerator.generate_photo`` — but wiring it needs a ReplicateVideoClient
    + PersonaStorage + CostTracker and a count→loop adapter (it returns one
    image per call) and makes billable Replicate calls. Until that adapter
    lands, this stub raises a clear message the tool surfaces to the user
    instead of pretending success. See docs/PHASE_4_ROADMAP.md.
    """
    raise RuntimeError(
        "Генерация фото персон ещё не подключена к роутеру "
        "(нужен адаптер к block_m1_persona.PhotoGenerator). Это явная заглушка."
    )


def _swapbatch_set_quality_call(chat_id_int: int, args: str) -> None:
    """Adapter so the router's set_quality tool reuses the legacy handler."""
    handler, _ = _swapbatch_get_handler()
    if handler is None:
        send(str(chat_id_int), "⚠️ Модуль face-swap недоступен.")
        return
    _swapbatch_apply_reply(
        str(chat_id_int), handler.handle_set_quality(chat_id_int, args)
    )


def _build_router():
    """Lazily build the singleton :class:`LLMRouter`, or None if unavailable.

    Returns None when the anthropic SDK or ``ANTHROPIC_API_KEY`` is missing so
    the caller transparently falls back to the legacy dispatcher. The result is
    cached; a failed build is remembered so we don't retry on every message.
    """
    global _ROUTER_SINGLETON, _ROUTER_BUILD_FAILED
    if _ROUTER_SINGLETON is not None:
        return _ROUTER_SINGLETON
    if _ROUTER_BUILD_FAILED:
        return None
    try:
        from app.services.unified.llm_router.llm_client import (
            build_anthropic_client,
            resolve_model,
        )
        from app.services.unified.llm_router.router import LLMRouter
        from app.services.unified.llm_router.tool_registry import ToolRegistry
        from app.services.unified.llm_router.tools import register_default_tools

        client = build_anthropic_client()
        if client is None:
            _ROUTER_BUILD_FAILED = True
            return None
        registry = ToolRegistry()
        register_default_tools(
            registry,
            dispatch_fn=_swapbatch_dispatch,
            set_quality_fn=_swapbatch_set_quality_call,
            # Step 2.7: stats is wired to the real cost formatter; persona photo
            # is an explicit graceful stub until its FLUX adapter lands.
            stats_fn=_router_stats_backend,
            research_fn=_router_research_backend,
            table_fn=_router_table_backend,
            persona_generate_fn=_persona_generate_backend,
            video_swap_dispatch_fn=_video_face_swap_dispatch,
            # On-request spoken replies: reuse the existing TTS pipeline,
            # independent of the global JARVIS_VOICE_REPLY_ENABLED flag.
            voice_synthesize_fn=_voice_synthesize,
            voice_send_fn=_router_voice_send,
        )
        _ROUTER_SINGLETON = LLMRouter(
            client,
            registry,
            model=resolve_model(),
            record_cost=_cost.record_cost,
            audit=_audit.audit_event,
        )
        return _ROUTER_SINGLETON
    except Exception as e:  # noqa: BLE001 - any failure → legacy fallback
        print(f"[router] build failed, using legacy dispatcher: {e}", flush=True)
        _ROUTER_BUILD_FAILED = True
        return None


def _render_router_response(chat_id: str, response) -> None:
    """Render a RouterResponse back to Telegram (text + any media)."""
    chat_id_s = str(chat_id)
    media = getattr(response, "media", None) or []
    if response.text:
        send(chat_id_s, response.text)
    for item in media:
        if item.kind == "photo" and item.media:
            _send_photo_url(chat_id_s, item.media, item.text or "")
        elif item.kind == "video" and item.media:
            send(chat_id_s, f"🎬 Видео: {item.media}")
    if not response.text and not media:
        send(chat_id_s, "Готово.")


def _run_router(chat_id: str, text: str, msg: Dict[str, Any]):
    """Run the LLM router for ``text`` and return the RouterResponse, or None.

    Returns None when the router should not handle the message (disabled, a
    ``/command``, unavailable) or when routing raised — in every such case the
    caller falls back to the legacy ``handle`` dispatcher. The router is OFF by
    default (opt-in): set ``JARVIS_ROUTER_ENABLED=1`` to enable it.
    """
    if os.getenv("JARVIS_ROUTER_ENABLED", "0").strip() != "1":
        return None
    if not text or text.lstrip().startswith("/"):
        return None
    router = _build_router()
    if router is None:
        return None

    import asyncio as _asyncio

    from app.services.unified.llm_router.tool_registry import ToolContext

    frm = msg.get("from") or {}
    try:
        uid = int(frm.get("id")) if frm.get("id") is not None else None
    except (TypeError, ValueError):
        uid = None
    context = ToolContext(
        user_id=uid, username=frm.get("username"), chat_id=str(chat_id)
    )
    history = _router_history_get(str(chat_id))
    try:
        response = _asyncio.run(
            router.route_message(text, context, conversation_history=history)
        )
    except Exception as e:  # noqa: BLE001 - any failure → legacy fallback
        print(f"[router] route_message failed, falling back: {e}", flush=True)
        return None
    # A graceful error result (disabled / API failure after retries) means the
    # router did not handle the message — fall back to the legacy dispatcher and
    # do not poison the conversation history with a failed turn.
    if getattr(response, "error", ""):
        return None
    _router_history_append(str(chat_id), text, getattr(response, "text", "") or "")
    return response


def _route_plain_text(chat_id: str, text: str, msg: Dict[str, Any]) -> bool:
    """Route plain (non-command) text through the LLM router.

    Returns True if the router consumed the message; False to fall back to the
    legacy ``handle`` dispatcher. Default-off keeps this additive — existing
    plain-text behaviour is unchanged until an operator enables the router.
    """
    response = _run_router(chat_id, text, msg)
    if response is None:
        return False
    try:
        _render_router_response(chat_id, response)
    except Exception as e:  # noqa: BLE001 - render must not crash the loop
        print(f"[router] render failed: {e}", flush=True)
    return True


# ── Phase 4 Step 2: voice in/out (Whisper transcription + optional TTS) ───────
# Voice notes are transcribed and fed into the same text flow as typed messages
# (router if enabled, else legacy ``handle``). A spoken reply is sent back only
# when JARVIS_VOICE_REPLY_ENABLED=1. Everything degrades to text on any failure.


def _uid_from_msg(msg: Dict[str, Any]):
    frm = msg.get("from") or {}
    try:
        return int(frm.get("id")) if frm.get("id") is not None else None
    except (TypeError, ValueError):
        return None


def _voice_transcribe(audio_path: str, *, duration_sec=None):
    """Bridge to the unified Whisper transcriber (kept as a seam for tests)."""
    from app.services.unified.voice.transcribe import transcribe_audio

    return transcribe_audio(audio_path, duration_sec=duration_sec)


def _voice_synthesize(text: str):
    """Bridge to the unified TTS synthesiser (kept as a seam for tests)."""
    from app.services.unified.voice.synthesize import synthesize_speech

    return synthesize_speech(text)


def _send_voice_note(chat_id: str, audio: bytes, audio_format: str = "ogg") -> None:
    """Upload ``audio`` bytes to Telegram as a voice note (or audio file)."""
    import requests

    if audio_format in ("ogg", "opus"):
        method, field, fname = "sendVoice", "voice", "reply.ogg"
    else:
        method, field, fname = "sendAudio", "audio", f"reply.{audio_format}"
    requests.post(
        f"{TG}/{method}",
        data={"chat_id": str(chat_id)},
        files={field: (fname, audio)},
        timeout=60,
    )


def _record_voice_event(chat_id: str, msg: Dict[str, Any], event: str, cost_usd: float, details: Dict[str, Any]) -> None:
    """Best-effort cost + audit recording for a voice transcription/synthesis."""
    uid = _uid_from_msg(msg)
    uname = (msg.get("from") or {}).get("username")
    if cost_usd:
        try:
            _cost.record_cost(uid, uname, cost_usd)
        except Exception:  # noqa: BLE001 - accounting must not break the flow
            pass
    try:
        _audit.audit_event(uid, uname, str(chat_id), event, {**details, "cost_usd": round(cost_usd, 6)})
    except Exception:  # noqa: BLE001
        pass


def _router_voice_send(context, result) -> None:
    """Deliver an on-request router voice reply + record its TTS cost.

    Used by the ``reply_with_voice`` tool. Unlike :func:`_maybe_voice_reply`
    this does NOT consult ``JARVIS_VOICE_REPLY_ENABLED`` — the user explicitly
    asked for a spoken answer, so we always send it. ``result`` is the
    ``SynthesisResult`` produced by :func:`_voice_synthesize`.
    """
    chat_id_s = str(context.chat_id)
    _send_voice_note(chat_id_s, result.audio, getattr(result, "audio_format", "ogg"))
    cost = getattr(result, "cost_usd", 0.0)
    if cost:
        try:
            _cost.record_cost(context.user_id, context.username, cost)
        except Exception:  # noqa: BLE001 - accounting must not break the flow
            pass
    try:
        _audit.audit_event(
            context.user_id,
            context.username,
            chat_id_s,
            "voice_synthesize",
            {
                "provider": getattr(result, "provider", ""),
                "cost_usd": round(cost, 6),
                "via": "reply_with_voice",
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _maybe_voice_reply(chat_id: str, text: str, msg: Dict[str, Any]) -> None:
    """Speak ``text`` back as a voice note when the feature flag is enabled."""
    from app.services.unified.voice.synthesize import voice_reply_enabled

    if not voice_reply_enabled() or not text:
        return
    result = _voice_synthesize(text)
    if getattr(result, "is_error", False) or not getattr(result, "audio", b""):
        return
    try:
        _send_voice_note(str(chat_id), result.audio, getattr(result, "audio_format", "ogg"))
    except Exception as e:  # noqa: BLE001 - a failed voice reply must not crash the loop
        print(f"[voice] send voice note failed: {e}", flush=True)
        return
    _record_voice_event(
        chat_id, msg, "voice_synthesize",
        getattr(result, "cost_usd", 0.0),
        {"chars": len(text), "provider": getattr(result, "provider", "")},
    )


def _route_voice(chat_id: str, msg: Dict[str, Any]) -> bool:
    """Transcribe a voice note and feed the text into the normal flow.

    Returns True when a voice payload was handled (so the caller does nothing
    further); False when there was no usable voice payload.
    """
    voice = msg.get("voice") or msg.get("audio")
    file_id = voice.get("file_id") if voice else None
    if not file_id:
        return False

    audio_path = _download_telegram_file(file_id, f"voice_{int(time.time())}.oga")
    if not audio_path:
        send(str(chat_id), "❌ Не удалось скачать голосовое.")
        return True

    send(str(chat_id), "🎤 Транскрибирую голосовое...")
    try:
        result = _voice_transcribe(audio_path, duration_sec=(voice or {}).get("duration"))
    except Exception as e:  # noqa: BLE001 - transcription must not crash the loop
        send(str(chat_id), f"❌ Ошибка транскрипции: {translate_exception(e)}")
        return True

    if getattr(result, "is_error", False) or not getattr(result, "text", ""):
        send(str(chat_id), f"❌ {getattr(result, 'error', '') or 'Не удалось распознать речь.'}")
        return True

    text = result.text
    _record_voice_event(
        chat_id, msg, "voice_transcribe",
        getattr(result, "cost_usd", 0.0),
        {"chars": len(text), "duration_sec": getattr(result, "duration_sec", 0)},
    )
    send(str(chat_id), f"📝 Распознал: {text}")

    response = _run_router(chat_id, text, msg)
    if response is not None:
        try:
            _render_router_response(chat_id, response)
        except Exception as e:  # noqa: BLE001
            print(f"[router] render failed: {e}", flush=True)
        _maybe_voice_reply(chat_id, getattr(response, "text", ""), msg)
    else:
        handle(chat_id, text)
    return True


def process_update(upd: Dict[str, Any], media_group_buffer: Optional[Dict[str, Any]] = None) -> None:
    """Process a single Telegram update (shared by polling loop and webhook reader)."""
    if not _whitelist_gate(upd):
        return
    _audit_message(upd)
    _remember_identity(upd)
    if _cost_command_intercept(upd):
        return
    if media_group_buffer is None:
        media_group_buffer = {}

    cq = upd.get("callback_query")
    if cq:
        cq_chat_id = str((cq.get("from") or {}).get("id", ""))
        msg_chat_id = str((cq.get("message", {}).get("chat") or {}).get("id", ""))
        if cq_chat_id == ALLOWED_CHAT_ID or msg_chat_id == ALLOWED_CHAT_ID:
            state = load_state()
            try:
                handle_callback_query(cq, state)
            except Exception as e:
                try:
                    answer_callback_query(cq.get("id", ""), "❌ Ошибка")
                except Exception:
                    pass
        return

    msg = upd.get("message") or upd.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = msg.get("text", "")
    has_file = bool(msg.get("document") or msg.get("photo") or msg.get("video"))
    has_voice = bool(msg.get("voice") or msg.get("audio"))
    media_gid = msg.get("media_group_id")

    # Block M.2 Phase C: /persona_video with attached or replied-to photo.
    if chat_id == ALLOWED_CHAT_ID and _persona_video_intercept(chat_id, msg):
        return

    # Block M.2.6: /video_face_swap — face photo + video pair (either order).
    if chat_id == ALLOWED_CHAT_ID and _video_face_swap_intercept(chat_id, msg):
        return

    # Block M.2.5: single photo for an active swapbatch session (albums go
    # through the media_group buffer below, not here).
    if (
        chat_id == ALLOWED_CHAT_ID
        and msg.get("photo")
        and not media_gid
        and _swapbatch_photo_intercept(chat_id, msg)
    ):
        return

    if text:
        # Block M.2.5: numbered-prompt message for an active custom-prompts flow.
        if chat_id == ALLOWED_CHAT_ID and _swapbatch_text_intercept(chat_id, text):
            return
        if not _route_plain_text(chat_id, text, msg):
            handle(chat_id, text)
    elif has_voice:
        # Phase 4 Step 2: voice notes route through the unified Whisper +
        # (optional) TTS layer, then into the same flow as typed text. Aligned
        # with the text branch — available to any whitelisted user, not just
        # the legacy single chat.
        _route_voice(chat_id, msg)
    elif has_file and chat_id == ALLOWED_CHAT_ID:
        if media_gid:
            # Buffer media group — process when all parts arrive. Dedupe
            # duplicate deliveries by file_unique_id (B-51) instead of a naive
            # append; sharing this with the poll loop also closes the webhook
            # media_group duplication gap found in Day 8.
            _buffer_media_group_msg(media_group_buffer, media_gid, msg)
        else:
            state = load_state()
            _handle_file_message(chat_id, msg, state)


def _webhook_flush_stale_groups(
    media_group_buffer: Dict[str, Any], now: Optional[float] = None
) -> None:
    """Flush media groups whose last part arrived >= 2s ago.

    Mirrors the poll loop's stale-group sweep so the webhook path drains albums
    the same way. Pop-before-process lives in ``_flush_media_group`` (B-51 §5.2),
    so a mid-flush exception cannot leave a group buffered for a re-flush.
    """
    if now is None:
        now = time.time()
    for gid in list(media_group_buffer):
        if now - media_group_buffer[gid].get("last_seen", now) >= 2.0:
            _flush_media_group(media_group_buffer, gid)


def _webhook_drain_new_updates(
    f, seen_offset: int, media_group_buffer: Dict[str, Any]
) -> int:
    """Dispatch every new line in ``f`` (from ``seen_offset``) via process_update.

    The persistent ``media_group_buffer`` is threaded through so album photos
    accumulate across drains instead of each landing in a throwaway per-call
    buffer. Returns the new file offset to resume from on the next pass.
    """
    f.seek(seen_offset)
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            update = json.loads(line)
            process_update(update, media_group_buffer)
        except Exception as e:
            print(f"[Webhook] process error: {e}", flush=True)
    return f.tell()


def webhook_reader_thread() -> None:
    """Read webhook_queue.jsonl every 1s, process new Telegram updates.

    Activated automatically when WEBHOOK_URL env var is set.
    The backend writes incoming updates to state/webhook_queue.jsonl.

    A persistent media_group buffer is owned here (not recreated per update) and
    stale groups are flushed on the 2s timeout — matching the poll loop — so an
    album delivered over the webhook accumulates across drains and flushes
    exactly once instead of each photo being lost in a throwaway buffer.
    """
    queue_path = Path(__file__).parent.parent / "state" / "webhook_queue.jsonl"
    seen_offset = 0
    media_group_buffer: Dict[str, Any] = {}
    print("[Webhook] Reader thread started.", flush=True)
    while True:
        try:
            # Flush stale media groups (>2s old — all parts arrived) before
            # draining new updates, same cadence as the poll loop.
            _webhook_flush_stale_groups(media_group_buffer)
            if queue_path.exists():
                with queue_path.open("r", encoding="utf-8") as f:
                    seen_offset = _webhook_drain_new_updates(
                        f, seen_offset, media_group_buffer
                    )
        except Exception as e:
            print(f"[Webhook] reader error: {e}", flush=True)
        time.sleep(1)


def main() -> None:
    if not _check_single_instance():
        sys.exit(1)

    try:
        _main_inner()
    finally:
        try:
            _PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def _main_inner() -> None:
    if not BOT_TOKEN or not ALLOWED_CHAT_ID:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOWED_CHAT_ID")

    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    print("Jarvis Smart Telegram Control Brain V2 started.", flush=True)

    import threading as _thr_main
    _hb_thread = _thr_main.Thread(target=_heartbeat_thread, daemon=True)
    _hb_thread.start()
    print("✅ Bot heartbeat thread started.", flush=True)

    _check_backend_startup()

    if webhook_url:
        print(f"[Webhook] Mode active — URL: {webhook_url}", flush=True)
        # Register webhook with Telegram
        full_url = webhook_url.rstrip("/") + "/telegram/webhook"
        try:
            reg = tg_call("setWebhook", {"url": full_url, "max_connections": 40})
            if reg.get("ok"):
                print(f"[Webhook] Registered: {full_url}", flush=True)
            else:
                print(f"[Webhook] Registration warning: {reg}", flush=True)
        except Exception as e:
            print(f"[Webhook] Registration failed: {e}", flush=True)
        # Start reader thread
        import threading as _thr
        t = _thr.Thread(target=webhook_reader_thread, daemon=True)
        t.start()
        # Keep main thread alive
        while True:
            time.sleep(60)
        return

    offset = 0

    # Start Cowork outbox watcher
    try:
        import sys as _sys
        _r = str(Path(__file__).parent.parent)
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from app.services.cowork_watcher import start_watcher
        start_watcher(callback=_cowork_delivery_callback)
        print("✅ Cowork watcher started.", flush=True)
    except Exception as _e:
        print(f"⚠️  Cowork watcher not started: {_e}", flush=True)

    # Start Backend reachability monitor
    try:
        _r2 = str(Path(__file__).parent.parent)
        import sys as _sys2
        if _r2 not in _sys2.path:
            _sys2.path.insert(0, _r2)
        from app.services.backend_monitor import start_monitor

        def _backend_notify(msg: str, level: str) -> None:
            if ALLOWED_CHAT_ID:
                send(ALLOWED_CHAT_ID, msg)

        start_monitor(BACKEND, _backend_notify)
        print("✅ Backend monitor started.", flush=True)
    except Exception as _e:
        print(f"⚠️  Backend monitor not started: {_e}", flush=True)

    # media_group buffer: {group_id: {"msgs": [...], "last_seen": float}}
    media_group_buffer: Dict[str, Any] = {}

    while True:
        try:
            # Flush stale media groups (>2 sec old — all parts arrived).
            # Pop-before-process (B-51 §5.2): a mid-flush exception cannot
            # leave a partially-processed group buffered for a re-flush.
            now = time.time()
            for gid in list(media_group_buffer):
                if now - media_group_buffer[gid]["last_seen"] >= 2.0:
                    _flush_media_group(media_group_buffer, gid)

            _au = urllib.parse.quote(json.dumps(["message", "edited_message", "callback_query"]))
            url = f"{TG}/getUpdates?timeout=30&offset={offset}&allowed_updates={_au}"
            updates = http_json("GET", url, timeout=45).get("result", [])
            for upd in updates:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)

                # Phase-4 unification: the poll loop and the webhook reader now
                # share one dispatch path. process_update applies the whitelist
                # gate, audit, identity capture, cost-command + persona-video +
                # swap photo/text intercepts, B-51 media_group dedupe, unified
                # voice (_route_voice), and file handling. The persistent buffer
                # is threaded through so the stale-group flush above drains it.
                process_update(upd, media_group_buffer)
        except Exception as e:
            print("ERR:", repr(e), flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()