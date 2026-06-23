# -*- coding: utf-8 -*-
"""Telegram handler for Block M.2.5 — batch face-swap pipeline.

This handler is *transport-agnostic*: methods return reply text and, where
relevant, ``actions`` describing what the bot wiring should do (e.g. send a
media-group of swapped photos). The bot wiring (in
``tools/jarvis_smart_telegram_control.py``) is responsible for actually
calling Telegram.

All user-visible strings are in Russian to match the Phase C UX style.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    MAX_TARGETS,
    OrchestratorError,
    STATE_AWAITING_CUSTOM_PROMPTS,
    STATE_DONE,
    STATE_EXPECTING_SOURCE,
    STATE_EXPECTING_TARGETS,
    STATE_IDLE,
    STATE_SOURCE_RECEIVED,
    STATE_SWAP_DONE,
    STATE_TARGETS_RECEIVED,
    get_orchestrator,
)
from app.services.audit import cost_tracker as _cost
from app.services.auth.access_control import check_limit
from app.services.block_m2_face_swap.cost_estimator import (
    animate_enabled,
    format_cost_report_ru,
)
from app.services.block_m2_face_swap.prompt_parser import PromptParseError
from app.services.block_m2_face_swap.quality_settings import (
    FPS_NATIVE,
    QualityError,
    fps_interpolation_enabled,
    parse_quality_args,
    validate_quality,
)
from app.services.error_translator import translate_exception

logger = logging.getLogger(__name__)


# ── reply types ──────────────────────────────────────────────────────────────


@dataclass
class HandlerReply:
    """Structured reply from a handler method.

    Attributes:
        text: A text message to send (None ⇒ no message).
        photos: Local photo paths to send as a media-group (after ``text``).
        numbered_photos: Local photo paths to send *individually* with an
            explicit ``📸 N/M`` caption per photo (used by the custom-prompts
            re-display so the user can see which index is which).
        videos: Local video paths to send one-by-one (after photos).
        documents: Local files to send as Telegram documents (e.g. result zips),
            sent after photos.
        consumed: Whether the handler took ownership of the inbound event.
            For photo-ingestion methods this signals to the bot whether to
            stop further dispatch.
    """

    text: str | None = None
    photos: list[Path] | None = None
    numbered_photos: list[Path] | None = None
    videos: list[Path] | None = None
    documents: list[Path] | None = None
    consumed: bool = True


HELP_TEXT = (
    "🎭 Batch Face Swap (Block M.2.5)\n"
    "\n"
    "Команды:\n"
    "  /swapbatch_source — следующее фото будет твоим источником лица\n"
    "  /swapbatch_batch — начни загружать альбом target-фото (до 20 штук)\n"
    "  /swapbatch_go — запустить swap после отчёта по стоимости\n"
    "  /swapbatch_set_quality duration=10 fps=42 — длительность (3–15с) и fps\n"
    "  /swapbatch_animate_yes — анимировать все swapped фото (дефолтный промпт)\n"
    "  /swapbatch_animate_custom — задать свой промпт для каждого фото\n"
    "  /swapbatch_no — оставить только swapped фото (без анимации)\n"
    "  /swapbatch_animate_no — то же, что /swapbatch_no\n"
    "  /swapbatch_cancel — отменить текущий батч\n"
    "  /swapbatch_status — показать состояние\n"
    "\n"
    "Поток: /swapbatch_source → пришли фото → /swapbatch_batch → пришли "
    "альбом → /swapbatch_go → жди → /swapbatch_animate_yes / "
    "/swapbatch_animate_custom / /swapbatch_no."
)


CUSTOM_PROMPTS_INSTRUCTIONS = (
    "✍️ Пришли промпты для анимации одним сообщением — по строке на фото:\n"
    "\n"
    "1. описание движения для фото 1\n"
    "2. описание движения для фото 2\n"
    "…\n"
    "\n"
    "• Разделитель: 1.  или  1)  или  1:\n"
    "• Пустая строка или /skip после номера → дефолтный промпт для этого фото\n"
    "• Можно пропускать номера — пропущенные получат дефолт\n"
    "\n"
    "После отправки я покажу, что понял, и спрошу подтверждение."
)


def animate_cost_estimate(
    *,
    swapped_count: int,
    seconds: int,
    resolution: str,
    engine_mode: str,
) -> dict:
    """Cost + rough wall-clock for animating N videos on the chosen engine."""
    import math
    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for(engine_mode)
    per = caps.cost_for(seconds, resolution)
    concurrency = max(1, int(os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2")))
    minutes = math.ceil(swapped_count / concurrency) * caps.gen_seconds(seconds) / 60.0
    return {
        "count": swapped_count,
        "per_usd": per,
        "total_usd": round(per * swapped_count, 2),
        "minutes": round(minutes, 1),
        "seconds": caps.snap_duration(seconds),
        "resolution": caps.snap_resolution(resolution),
        "engine_mode": engine_mode,
        "display_name": caps.display_name,
    }


class FaceSwapHandler:
    """Russian-language command handlers for /swapbatch_*.

    Construct once at bot startup. The handler is stateless beyond the
    injected ``BatchOrchestrator``. The bot wiring is responsible for:

    - downloading photos to local paths (via ``_download_telegram_file``)
    - running long-running phases (``run_swap_phase`` / ``run_animate_phase``)
      in a worker thread + acquiring the shared video lock
    - sending replies + media groups + videos
    """

    def __init__(
        self,
        orchestrator: BatchOrchestrator | None = None,
    ) -> None:
        self.orchestrator = orchestrator or get_orchestrator()

    # ── commands ────────────────────────────────────────────────────────────

    def handle_help(self) -> HandlerReply:
        return HandlerReply(text=HELP_TEXT)

    def handle_source_intent(self, chat_id: int) -> HandlerReply:
        try:
            self.orchestrator.begin_source(chat_id)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return HandlerReply(
            text=(
                "📸 Жду фото с твоим лицом. Пришли одно фото — лицо должно "
                "быть чётко видно."
            )
        )

    def handle_batch_intent(self, chat_id: int) -> HandlerReply:
        try:
            self.orchestrator.begin_targets(chat_id)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return HandlerReply(
            text=(
                "📦 Жду target-фото (до 100 штук). Можешь слать несколькими "
                "альбомами подряд — я докину каждый в текущий батч и буду "
                "показывать сколько принято. Когда всё — /swapbatch_go."
            )
        )

    def handle_status(self, chat_id: int) -> HandlerReply:
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Нет активного батча.")
        lines = [
            f"📋 Состояние: {sess.status}",
            f"  Source: {'есть' if sess.source_path else 'нет'} "
            f"({sess.source_face_count} лиц)",
            f"  Targets: {len(sess.targets)} (валидных "
            f"{sum(1 for t in sess.targets if t.valid)})",
        ]
        if sess.cost_estimate:
            lines.append(
                f"  Оценка: ~${sess.cost_estimate.get('total_usd', 0):.2f} "
                f"за {sess.cost_estimate.get('total_minutes', 0):.0f} мин"
            )
        if sess.last_error:
            lines.append(f"  ⚠️ {sess.last_error}")
        return HandlerReply(text="\n".join(lines))

    def handle_cancel(self, chat_id: int) -> HandlerReply:
        ok = self.orchestrator.cancel(chat_id)
        if not ok:
            return HandlerReply(text="Нет активного батча для отмены.")
        return HandlerReply(
            text="🛑 Запрошена отмена. Если идёт swap/animate — остановим "
            "после текущего шага."
        )

    def handle_animate_no(self, chat_id: int) -> HandlerReply:
        try:
            sess = self.orchestrator.skip_animate(chat_id)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        self.orchestrator.prune(chat_id)
        n = sum(1 for t in sess.targets if t.swap_result_path)
        return HandlerReply(
            text=f"✅ Готово. Сохранено {n} swapped фото без анимации."
        )

    def handle_animate_yes(self, chat_id: int) -> HandlerReply:
        """Cost gate: show estimate + confirm prompt; does NOT run animation."""
        sess = self.orchestrator.get(chat_id)
        if sess is None or sess.status != STATE_SWAP_DONE:
            return HandlerReply(text="⚠️ Сначала заверши swap (/swapbatch_go).")
        swapped = sum(1 for t in sess.targets if t.swap_result_path)
        if swapped == 0:
            return HandlerReply(text="⚠️ Нет swapped фото для анимации.")
        est = animate_cost_estimate(
            swapped_count=swapped,
            seconds=sess.duration_sec,
            resolution=sess.resolution,
            engine_mode=sess.video_engine,
        )
        prompt_line = sess.motion_prompt or "(дефолтный промт движения)"
        return HandlerReply(text=(
            f"🎬 {est['display_name']}\n"
            f"Анимация {est['count']} фото × {est['seconds']}с × {est['resolution']}\n"
            f"Промт: {prompt_line}\n"
            f"Стоимость: ~${est['total_usd']:.2f} (${est['per_usd']:.2f}/видео)\n"
            f"Время: ~{est['minutes']:.0f} мин\n\n"
            f"/swapbatch_animate_go — запустить (платно)\n"
            f"/swapbatch_set_prompt <текст> — задать движение/сцену\n"
            f"💡 для плавности (скопируй):\n"
            f"/swapbatch_set_prompt slow gentle head turn, soft blinking, subtle breathing, "
            f"minimal movement, locked static camera, smooth continuous slow motion\n"
            f"/swapbatch_set_wardrobe preserve|safe|spicy — одежда (дефолт safe)\n"
            f"/swapbatch_set_quality duration=.. resolution=.. — качество\n"
            f"/swapbatch_no — без анимации"
        ))

    def handle_set_prompt(self, chat_id: int, text: str) -> HandlerReply:
        """/swapbatch_set_prompt — store shared motion prompt; clamp over-long
        with explicit warning (no silent truncation)."""
        if self.orchestrator.get(chat_id) is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        from app.services.block_m2_video.prompt_assembly import clamp_prompt
        cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
        clamped, truncated = clamp_prompt(text or "", cap)
        self.orchestrator.set_motion_prompt(chat_id, clamped)
        sess = self.orchestrator.get(chat_id)
        if not sess.motion_prompt:
            return HandlerReply(text="✅ Промт сброшен на дефолтный.")
        warn = ""
        if truncated:
            warn = (f"\n⚠️ Промт был длиннее лимита ({len(text)} > {cap} симв.) "
                    f"и обрезан до {len(clamped)} симв.")
        return HandlerReply(text=f"✅ Промт движения задан:\n«{sess.motion_prompt}»{warn}")

    def handle_set_wardrobe(self, chat_id: int, mode: str) -> HandlerReply:
        """/swapbatch_set_wardrobe preserve|safe|spicy — clothing control."""
        from app.services.block_m2_video.prompt_assembly import WARDROBE_MODES
        if self.orchestrator.get(chat_id) is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        mode = (mode or "").strip().lower()
        if mode not in WARDROBE_MODES:
            return HandlerReply(text=(
                "⚠️ Режим одежды: preserve | safe | spicy\n"
                "• preserve — сохранить одежду как на фото\n"
                "• safe — не раздевать (дефолт)\n"
                "• spicy — без ограничений (uncensored)"
            ))
        self.orchestrator.set_wardrobe(chat_id, mode)
        labels = {"preserve": "сохранять одежду", "safe": "не раздевать (дефолт)", "spicy": "без ограничений"}
        return HandlerReply(text=f"✅ Режим одежды: {mode} — {labels[mode]}.")

    @staticmethod
    def build_engine_keyboard() -> dict:
        """Inline-меню выбора движка после свапа. Seedance с пометкой censored."""
        from app.services.block_m2_video.engines.capabilities import (
            WAVESPEED_CAPS, SEEDANCE_CAPS,
        )
        return {"inline_keyboard": [
            [{"text": f"🎬 {WAVESPEED_CAPS.display_name}", "callback_data": "sbeng:spicy"}],
            [{"text": f"🎬 {SEEDANCE_CAPS.display_name} · censored (SFW)",
              "callback_data": "sbeng:seedance"}],
            [{"text": "🚫 Без анимации", "callback_data": "sbeng:none"}],
        ]}

    def handle_set_engine(self, chat_id: int, engine_mode: str) -> HandlerReply:
        """Установить движок батча и снапнуть качество в его caps."""
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(engine_mode)
        snapped_dur = caps.snap_duration(sess.duration_sec)
        snapped_res = caps.snap_resolution(sess.resolution)
        self.orchestrator.set_video_engine(chat_id, engine_mode)
        self.orchestrator.set_animate_quality(
            chat_id, duration=snapped_dur, resolution=snapped_res,
        )
        sess = self.orchestrator.get(chat_id)
        note = "" if not caps.censored else "\n⚠️ Censored: подходит для SFW/одетых сцен."
        return HandlerReply(text=(
            f"✅ Движок: {caps.display_name}. Качество: {sess.duration_sec}с, "
            f"{sess.resolution}, fps {caps.native_fps}.{note}\n"
            f"Дальше: /swapbatch_animate_yes → /swapbatch_animate_go."
        ))

    def build_single_animate_request(
        self, chat_id: int, *, image_path, motion: str,
        engine_mode: str | None = None, seconds: int | None = None,
        resolution: str | None = None,
    ):
        """Собрать один VideoRequest для standalone /animate.

        Engine/quality читаются из активной сессии, если есть; для чисто
        standalone-потока (без батча) их можно передать явно через оверрайды.
        """
        from pathlib import Path
        from app.services.block_m2_video.engines.engine_protocol import (
            VideoRequest, new_generation_id,
        )
        from app.services.block_m2_video.prompt_assembly import assemble_animate_prompt
        sess = self.orchestrator.get(chat_id)
        engine_mode = engine_mode or (sess.video_engine if sess else "spicy")
        seconds = seconds or (sess.duration_sec if sess else 5)
        resolution = resolution or (sess.resolution if sess else "720p")
        prompt, negative = assemble_animate_prompt(
            (sess.motion_prompt if sess else "") or motion,
            add_realism=True, add_negative=True,
        )
        return VideoRequest(
            persona_id=f"animate_{chat_id}", persona_name="animate",
            input_image_path=Path(image_path), prompt=prompt, seconds=seconds,
            resolution=resolution, negative_prompt=negative, mode=engine_mode,
            generation_id=new_generation_id(),
        )

    def build_quality_keyboard(self, chat_id: int) -> dict:
        """Caps-aware кнопки длины и разрешения для текущего движка."""
        from app.services.block_m2_video.engines.capabilities import caps_for
        sess = self.orchestrator.get(chat_id)
        caps = caps_for(sess.video_engine if sess else "spicy")
        dur_row = [{"text": f"{d}с", "callback_data": f"sbq:dur:{d}"}
                   for d in caps.allowed_durations]
        res_row = [{"text": r, "callback_data": f"sbq:res:{r}"}
                   for r in caps.allowed_resolutions]
        return {"inline_keyboard": [
            dur_row, res_row,
            [{"text": "✅ Готово", "callback_data": "sbq:done"}],
        ]}

    def handle_quality_button(self, chat_id: int, kind: str, value: str) -> HandlerReply:
        """Тап кнопки → собрать аргумент → существующий handle_set_animate_quality."""
        arg = f"duration={value}" if kind == "dur" else f"resolution={value}"
        return self.handle_set_animate_quality(chat_id, arg)

    def handle_set_animate_quality(self, chat_id: int, args_text: str) -> HandlerReply:
        """Engine-aware quality setter for the managed animate path: constrains
        duration + resolution to the chosen engine's caps; fps is info-only."""
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча. Начни с /swapbatch_source.")
        from app.services.block_m2_face_swap.quality_settings import (
            parse_animate_quality, QualityError,
        )
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(sess.video_engine)
        try:
            parsed = parse_animate_quality(args_text or "", engine_mode=sess.video_engine)
        except QualityError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        if not parsed:
            dur = "/".join(str(x) for x in caps.allowed_durations)
            res = "/".join(caps.allowed_resolutions)
            return HandlerReply(text=(
                f"📐 {caps.display_name}: сейчас {sess.duration_sec}с, {sess.resolution}, "
                f"fps {caps.native_fps} (нативный, не настраивается).\n"
                f"Доступно: duration={dur}, resolution={res}.\n"
                f"Изменить: /swapbatch_set_quality duration=.. resolution=.."
            ))
        self.orchestrator.set_animate_quality(
            chat_id,
            duration=parsed.get("duration"),
            resolution=parsed.get("resolution"),
        )
        sess = self.orchestrator.get(chat_id)
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(sess.video_engine)
        return HandlerReply(text=(
            f"✅ Качество: {sess.duration_sec}с, {sess.resolution} "
            f"(fps {caps.native_fps}, нативный).\n"
            f"Дальше: /swapbatch_animate_yes (покажу стоимость) → /swapbatch_animate_go."
        ))

    # ── quality settings (Task C) ───────────────────────────────────────────

    def handle_set_quality(self, chat_id: int, args_text: str) -> HandlerReply:
        """/swapbatch_set_quality duration=N fps=N — set per-batch quality.

        No args → show current values. fps > 21 is gated behind the
        ENABLE_FPS_INTERPOLATION flag (rejected with the smoke-test message
        while off). Unprovided keys keep the session's current value.
        """
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(
                text="⚠️ Нет активного батча. Начни с /swapbatch_source."
            )
        try:
            parsed = parse_quality_args(args_text or "")
        except QualityError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        if not parsed:
            return HandlerReply(
                text=self._format_quality(
                    sess.duration_sec, sess.fps, current=True
                )
            )
        duration = parsed.get("duration", sess.duration_sec)
        fps = parsed.get("fps", sess.fps)
        try:
            qs = validate_quality(
                duration, fps, fps_enabled=fps_interpolation_enabled()
            )
        except QualityError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        self.orchestrator.set_quality(
            chat_id, duration_sec=qs.duration_sec, fps=qs.fps
        )
        return HandlerReply(
            text=self._format_quality(qs.duration_sec, qs.fps, current=False)
        )

    @staticmethod
    def _format_quality(duration: int, fps: int, *, current: bool) -> str:
        fps_desc = (
            f"{fps} fps (RIFE интерполяция с {FPS_NATIVE})"
            if fps > FPS_NATIVE
            else f"{fps} fps (без интерполяции)"
        )
        head = "📐 Текущее качество" if current else "✅ Качество батча обновлено"
        lines = [f"{head}: {duration} сек, {fps_desc}."]
        if duration > 10:
            lines.append(
                "⚠️ Видео >10 сек может потерять качество "
                "(Wan 2.2 обучен на ~5-сек клипах)."
            )
        if not current:
            lines.append(
                "Дальше: /swapbatch_animate_yes или /swapbatch_animate_custom."
            )
        return "\n".join(lines)

    # ── custom-prompts flow (Day 6) ─────────────────────────────────────────

    def handle_animate_custom(self, chat_id: int) -> HandlerReply:
        """Enter the per-photo custom-prompts flow and re-display photos."""
        try:
            photos = self.orchestrator.start_custom_prompts(chat_id)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return HandlerReply(
            text=CUSTOM_PROMPTS_INSTRUCTIONS, numbered_photos=photos
        )

    def handle_no(self, chat_id: int) -> HandlerReply:
        """/swapbatch_no — exit the animate flow, keep only swapped photos."""
        sess = self.orchestrator.cancel_animate(chat_id)
        if sess is None:
            return HandlerReply(text="Нет активного батча.")
        n = sum(1 for t in sess.targets if t.swap_result_path)
        return HandlerReply(
            text=f"✅ Готово. Сохранено {n} swapped фото без анимации."
        )

    def consume_custom_prompts_text(
        self, chat_id: int, text: str
    ) -> HandlerReply:
        """Parse a numbered-prompt message while awaiting custom prompts.

        Returns ``consumed=False`` when the chat is not in the awaiting state so
        the bot can fall through to normal text handling.
        """
        if self.orchestrator.status(chat_id) != STATE_AWAITING_CUSTOM_PROMPTS:
            return HandlerReply(consumed=False)
        try:
            result = self.orchestrator.submit_custom_prompts(chat_id, text)
        except PromptParseError as exc:
            return HandlerReply(
                text=(
                    f"⚠️ Не смог разобрать промпты: {exc}\n\n"
                    "Пришли список заново, по строке на фото:\n"
                    "1. текст\n2. текст\n…"
                )
            )
        return HandlerReply(text=self._build_prompts_preview(chat_id, result))

    def handle_retry(self, chat_id: int) -> HandlerReply:
        """/swapbatch_retry — discard parsed prompts, ask again."""
        try:
            photos = self.orchestrator.retry_custom_prompts(chat_id)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return HandlerReply(
            text=CUSTOM_PROMPTS_INSTRUCTIONS, numbered_photos=photos
        )

    def _build_prompts_preview(self, chat_id: int, result) -> str:
        """Render the parsed prompts (with default markers) + next-step menu."""
        sess = self.orchestrator.get(chat_id)
        photo_count = (
            sum(1 for t in sess.targets if t.swap_result_path) if sess else 0
        )
        custom = result.prompts
        lines = ["📝 Вот что я понял:"]
        for i in range(1, photo_count + 1):
            prompt = custom.get(i)
            lines.append(f"  {i}. {prompt}" if prompt else f"  {i}. (по умолчанию)")
        lines.append("")

        mi = result.mismatch_info
        if mi and mi["kind"] == "too_few":
            lines.append(
                f"Ты указал {mi['provided']} промптов на {mi['expected']} фото.\n"
                f"  /swapbatch_apply_partial — первые {mi['provided']} кастомные, "
                f"остальные по умолчанию\n"
                "  /swapbatch_retry — ввести заново\n"
                "  /swapbatch_cancel — без анимации"
            )
        elif mi and mi["kind"] == "too_many":
            lines.append(
                f"Ты указал {mi['provided']} промптов, но фото только "
                f"{mi['expected']}.\n"
                f"  /swapbatch_apply_first — взять первые {mi['expected']}\n"
                "  /swapbatch_retry — ввести заново\n"
                "  /swapbatch_cancel — без анимации"
            )
        else:
            lines.append(
                "  /swapbatch_confirm — запустить анимацию\n"
                "  /swapbatch_retry — ввести заново\n"
                "  /swapbatch_cancel — без анимации"
            )
        return "\n".join(lines)

    # ── photo ingestion ─────────────────────────────────────────────────────

    def consume_source(
        self, chat_id: int, local_photo_path: Path
    ) -> HandlerReply:
        """Process a single inbound photo as the source face."""
        if not self.orchestrator.is_waiting_for_source(chat_id):
            return HandlerReply(consumed=False)
        try:
            sess = self.orchestrator.submit_source(chat_id, local_photo_path)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return HandlerReply(
            text=(
                f"✅ Source принят. Лиц найдено: {sess.source_face_count}.\n"
                "Теперь команда /swapbatch_batch и пришли альбом target-фото."
            )
        )

    def consume_targets_album(
        self, chat_id: int, local_photo_paths: list[Path]
    ) -> HandlerReply:
        """Append a buffered media-group to the targets (multi-album intake)."""
        from app.services.block_m2_face_swap.batch_orchestrator import (
            STATE_TARGETS_RECEIVED,
        )
        status = self.orchestrator.status(chat_id)
        if status not in (STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED):
            return HandlerReply(consumed=False)
        try:
            sess, est = self.orchestrator.add_targets(chat_id, local_photo_paths)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        no_face_advisory = sum(
            1 for t in sess.targets if t.valid and t.face_count == 0
        )
        accepted = len(sess.targets)
        animate_usd_override = None
        animate_minutes_override = None
        if est.valid_count > 0:
            acaps = animate_cost_estimate(
                swapped_count=est.valid_count,
                seconds=sess.duration_sec,
                resolution=sess.resolution,
                engine_mode=sess.video_engine,
            )
            animate_usd_override = acaps["total_usd"]
            animate_minutes_override = acaps["minutes"]
        msg = format_cost_report_ru(
            est,
            source_face_count=sess.source_face_count,
            total_targets=accepted,
            no_face_advisory=no_face_advisory,
            animate_enabled=animate_enabled(),
            animate_usd_override=animate_usd_override,
            animate_minutes_override=animate_minutes_override,
        )
        # Prepend running accumulation UX line.
        msg = f"принято {accepted}/{MAX_TARGETS}\n" + msg
        return HandlerReply(text=msg)

    # ── long-running phases (called from worker thread) ─────────────────────

    async def run_swap_phase(
        self,
        chat_id: int,
        swap_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        """Run the swap engine and assemble a reply with the swapped photos."""
        try:
            await self.orchestrator.confirm_swap(
                chat_id, swap_fn=swap_fn, progress_cb=progress_cb,
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(text=f"❌ Ошибка swap: {translate_exception(exc)}")

        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Сессия пропала (вероятно, отменена).")

        photos = [
            Path(t.swap_result_path)
            for t in sess.targets
            if t.swap_result_path
        ]
        # Build size-split zips from successful photos only.
        # `photos` contains only swap_result_path values that are non-None — so
        # failed targets (swap_result_path=None) are excluded by construction.
        documents: list[Path] = []
        if photos:
            from app.services.block_m2_face_swap.result_delivery import (
                build_result_zips,
            )
            zip_dir = photos[0].parent.parent / "delivery"
            try:
                documents = build_result_zips(photos, zip_dir)
            except Exception as exc:  # noqa: BLE001 — zip is a convenience, not critical
                logger.warning("result zip build failed: %s", exc)
                documents = []
        succeeded = len(photos)
        # Bill the per-user ledger for the swap phase exactly once (succeeded ×
        # swap rate). Best-effort: a billing failure must not break the reply.
        if user_id is not None and succeeded > 0:
            swap_rate = self._envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
            try:
                _cost.record_cost(user_id, username, succeeded * swap_rate)
            except Exception as exc:  # noqa: BLE001 - billing must not break the reply
                logger.warning("cost: swap record_cost failed: %s", exc)
        # Under advisory semantics: valid=False means unreadable (real skip);
        # valid=True + no swap_result_path = lucataco returned None (no face
        # or other per-target failure).
        failed = sum(
            1 for t in sess.targets if t.valid and not t.swap_result_path
        )
        skipped = sum(1 for t in sess.targets if not t.valid)
        lines = [
            f"✅ Swap завершён: {succeeded} успешно",
        ]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось (нет лица или ошибка)")
        if skipped:
            lines.append(f"  ⏭ {skipped} пропущено (битые/нечитаемые)")
        if succeeded:
            lines.append("")
            if animate_enabled():
                lines.append(
                    "/swapbatch_animate_yes — анимировать все swapped фото "
                    "(дефолтный промпт)\n"
                    "/swapbatch_animate_custom — задать свой промпт для каждого фото\n"
                    "/swapbatch_set_quality duration=10 — изменить длительность (3-15с)\n"
                    "/swapbatch_no — оставить только swapped фото (без анимации)"
                )
            else:
                lines.append(
                    "(видео-фаза отключена на этом этапе — фото сохранены)"
                )
        return HandlerReply(
            text="\n".join(lines), photos=photos, documents=documents,
        )

    async def run_animate_batch_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb=None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        """Paid runner: call confirm_animate_batch and bill successful videos."""
        sess0 = self.orchestrator.get(chat_id)
        seconds = sess0.duration_sec if sess0 else 10
        resolution = sess0.resolution if sess0 else "720p"
        engine_mode = sess0.video_engine if sess0 else "spicy"
        if user_id is not None:
            from app.services.block_m2_video.engines.capabilities import caps_for
            n = len(
                [t for t in sess0.targets if t.swap_result_path]
            ) if sess0 else 0
            est = n * caps_for(engine_mode).cost_for(seconds, resolution)
            allowed, reason = check_limit(user_id, estimated_usd=est)
            if not allowed:
                return HandlerReply(text=f"🚫 {reason}")
        try:
            await self.orchestrator.confirm_animate_batch(
                chat_id, animate_fn=animate_fn, progress_cb=progress_cb,
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(text=f"❌ Ошибка animate: {translate_exception(exc)}")

        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Сессия пропала (вероятно, отменена).")
        videos = [
            Path(t.animate_result_path)
            for t in sess.targets
            if t.animate_result_path
        ]
        succeeded = len(videos)
        failed = sum(
            1 for t in sess.targets
            if t.swap_result_path and not t.animate_result_path
        )
        lines = [f"🎬 Animate завершён: {succeeded} видео"]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        from app.services.block_m2_video.engines.capabilities import caps_for
        amount = succeeded * caps_for(engine_mode).cost_for(seconds, resolution)
        if user_id is not None and amount > 0:
            try:
                _cost.record_cost(user_id, username, amount)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cost: record_cost failed: %s", exc)
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)

    @staticmethod
    def _envf(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _bill_completed_videos(
        self, succeeded: int, user_id: int | None, username: str | None
    ) -> None:
        """Record cost for SUCCESSFUL videos only (swap + animate per video).

        Best-effort: a failure in cost tracking must never break a batch, so
        all exceptions are swallowed (mirrors the audit-logger contract).
        Failed animations are not billed.
        """
        if user_id is None or succeeded <= 0:
            return
        swap_rate = self._envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
        animate_rate = self._envf("SWAPBATCH_ANIMATE_USD_PER_VIDEO", 0.27)
        amount = succeeded * (swap_rate + animate_rate)
        try:
            _cost.record_cost(user_id, username, amount)
        except Exception as exc:  # noqa: BLE001 - cost tracking must not raise
            logger.warning("cost: record_cost failed: %s", exc)

    async def run_animate_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        try:
            await self.orchestrator.confirm_animate(
                chat_id, animate_fn=animate_fn, progress_cb=progress_cb,
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(text=f"❌ Ошибка animate: {translate_exception(exc)}")

        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Сессия пропала (вероятно, отменена).")

        videos = [
            Path(t.animate_result_path)
            for t in sess.targets
            if t.animate_result_path
        ]
        succeeded = len(videos)
        failed = sum(
            1 for t in sess.targets
            if t.swap_result_path and not t.animate_result_path
        )
        lines = [f"🎬 Animate завершён: {succeeded} видео"]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        self._bill_completed_videos(succeeded, user_id, username)
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)

    async def run_custom_animate_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        """Run the video engine with per-photo custom prompts and report tally.

        ``animate_fn(swapped, target_idx, prompt, cancel_check) -> Path`` — the
        bot wiring substitutes the default motion prompt when ``prompt`` is
        ``None`` so an all-default custom run matches /swapbatch_animate_yes.
        """
        try:
            await self.orchestrator.confirm_custom_animate(
                chat_id, animate_fn=animate_fn, progress_cb=progress_cb,
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(
                text=f"❌ Ошибка animate: {translate_exception(exc)}"
            )

        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Сессия пропала (вероятно, отменена).")

        videos = [
            Path(t.animate_result_path)
            for t in sess.targets
            if t.animate_result_path
        ]
        succeeded = len(videos)
        failed = sum(
            1 for t in sess.targets
            if t.swap_result_path and not t.animate_result_path
        )
        lines = [f"🎬 Animate завершён: {succeeded} видео"]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        self._bill_completed_videos(succeeded, user_id, username)
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)
