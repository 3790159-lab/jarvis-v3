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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
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
from app.services.block_m2_face_swap.cost_estimator import format_cost_report_ru
from app.services.block_m2_face_swap.prompt_parser import PromptParseError
from app.services.block_m2_face_swap.quality_settings import (
    QualityError,
    fps_interpolation_enabled,
    interpolation_multiplier,
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
        consumed: Whether the handler took ownership of the inbound event.
            For photo-ingestion methods this signals to the bot whether to
            stop further dispatch.
    """

    text: str | None = None
    photos: list[Path] | None = None
    numbered_photos: list[Path] | None = None
    videos: list[Path] | None = None
    consumed: bool = True


HELP_TEXT = (
    "🎭 Batch Face Swap (Block M.2.5)\n"
    "\n"
    "Команды:\n"
    "  /swapbatch_source — следующее фото будет твоим источником лица\n"
    "  /swapbatch_batch — начни загружать альбом target-фото (до 10 штук)\n"
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
                "📦 Жду альбом target-фото (до 10 штук). Пришли все фото "
                "одной отправкой (Telegram album). Я подожду 2 секунды после "
                "последнего фото и покажу оценку."
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
        mult = interpolation_multiplier(fps)
        fps_desc = (
            f"{fps} fps (RIFE ×{mult})"
            if mult > 1
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
        """Process a buffered media-group as the targets album."""
        if not self.orchestrator.is_waiting_for_targets(chat_id):
            return HandlerReply(consumed=False)
        try:
            sess, est = self.orchestrator.submit_targets(
                chat_id, local_photo_paths
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        msg = format_cost_report_ru(
            est,
            source_face_count=sess.source_face_count,
            total_targets=len(sess.targets),
        )
        return HandlerReply(text=msg)

    # ── long-running phases (called from worker thread) ─────────────────────

    async def run_swap_phase(
        self,
        chat_id: int,
        swap_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
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
        succeeded = len(photos)
        failed = sum(
            1 for t in sess.targets if t.valid and not t.swap_result_path
        )
        skipped = sum(1 for t in sess.targets if not t.valid)
        lines = [
            f"✅ Swap завершён: {succeeded} успешно",
        ]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        if skipped:
            lines.append(f"  ⏭ {skipped} пропущено (без лица)")
        if succeeded:
            lines.append("")
            lines.append(
                "/swapbatch_animate_yes — анимировать все swapped фото "
                "(дефолтный промпт)\n"
                "/swapbatch_animate_custom — задать свой промпт для каждого фото\n"
                "/swapbatch_set_quality duration=10 — изменить длительность (3-15с)\n"
                "/swapbatch_no — оставить только swapped фото (без анимации)"
            )
        return HandlerReply(text="\n".join(lines), photos=photos)

    async def run_animate_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
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
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)

    async def run_custom_animate_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
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
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)
