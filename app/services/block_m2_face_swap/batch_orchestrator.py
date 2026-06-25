# -*- coding: utf-8 -*-
"""Block M.2.5 batch face-swap orchestrator.

Tracks per-chat session state through the 9-state machine:

    IDLE → EXPECTING_SOURCE → SOURCE_RECEIVED → EXPECTING_TARGETS →
    TARGETS_RECEIVED → SWAPPING → SWAP_DONE →
    ANIMATING → DONE

The orchestrator is **storage-aware but transport-agnostic**: it does not
talk to Telegram, it does not run engines directly. Long-running phases
(``confirm_swap``, ``confirm_animate``) accept an engine and a
progress-callback; the *handler* (and bot wiring) is what actually invokes
the orchestrator from a worker thread.

Session state is persisted to disk at
``state/face_swap/batches/{chat_id}/session.json`` after every transition so
a bot restart can at least *report* an interrupted batch (we never
auto-resume an in-flight engine call — too dangerous).
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .cost_estimator import CostEstimate, estimate as estimate_cost
from .engines.factory import get_swap_cold_start_usd, get_swap_cost_per_photo
from .face_validator import FaceValidator
from .prompt_parser import ParseResult, parse_numbered_prompts

logger = logging.getLogger(__name__)


# Single source of truth for state-machine names.
STATE_IDLE = "IDLE"
STATE_EXPECTING_SOURCE = "EXPECTING_SOURCE"
STATE_SOURCE_RECEIVED = "SOURCE_RECEIVED"
STATE_EXPECTING_TARGETS = "EXPECTING_TARGETS"
STATE_TARGETS_RECEIVED = "TARGETS_RECEIVED"
STATE_SWAPPING = "SWAPPING"
STATE_SWAP_DONE = "SWAP_DONE"
# Day 6: per-photo custom-prompt sub-flow between SWAP_DONE and ANIMATING.
STATE_AWAITING_CUSTOM_PROMPTS = "AWAITING_CUSTOM_PROMPTS"
STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM = "AWAITING_CUSTOM_PROMPTS_CONFIRM"
# Задача 2 (Вариант A): animate ГОТОВЫХ фото без свапа. The user uploads
# already-finished photos which are dropped straight into ``swap_result_path``
# and into SWAP_DONE, never touching run_swap_phase — so swap is never billed.
STATE_EXPECTING_READY_PHOTOS = "EXPECTING_READY_PHOTOS"
STATE_ANIMATING = "ANIMATING"
STATE_DONE = "DONE"
STATE_FAILED_RESUMED = "FAILED_RESUMED"

_TERMINAL_STATES = {STATE_DONE, STATE_FAILED_RESUMED}
_LOCKABLE_STATES = {STATE_SWAPPING, STATE_ANIMATING}

# Max target photos per batch (post-dedupe). Raised 5→20 for larger persona
# Instagram drops (10-15 photos/shoot). Raised 20→100 for the lucataco batch
# (parallel swaps, ~100s for 100 photos). Animation is sequential — large
# animate batches are hours; swap-only is fast.
MAX_TARGETS = 100

# Legacy single-album cap used by the original submit_targets() path. Kept at
# 20 so the frozen single-album flow doesn't silently accept oversized batches
# and so existing tests remain green. The new add_targets() path uses
# MAX_TARGETS for cumulative accumulation across albums.
_SUBMIT_TARGETS_ALBUM_MAX = 20


class OrchestratorError(RuntimeError):
    """Raised when a state-machine transition is invalid."""


@dataclass
class TargetItem:
    """One target photo in the batch."""

    path: str
    face_count: int = 0
    valid: bool = False
    swap_result_path: str | None = None
    animate_result_path: str | None = None
    error: str | None = None


@dataclass
class BatchSession:
    """Per-chat session state. Serialisable to JSON via ``asdict``."""

    chat_id: int
    status: str = STATE_IDLE
    source_path: str | None = None
    source_face_count: int = 0
    targets: list[TargetItem] = field(default_factory=list)
    cost_estimate: dict[str, Any] | None = None
    cancel_requested: bool = False
    # Day 6: custom-prompts sub-flow. ``custom_prompts`` maps a 1-based photo
    # index (over the *swapped* photos shown to the user) → custom prompt, or
    # ``None`` to use the default. ``prompt_mismatch_info`` mirrors the parser's
    # mismatch_info so the bot can offer /apply_partial or /apply_first_N.
    custom_prompts: dict[int, str | None] | None = None
    prompt_mismatch_info: dict[str, Any] | None = None
    # Task C: per-batch video quality. duration_sec drives native frame count;
    # fps drives RIFE interpolation (21 = native, no interpolation).
    duration_sec: int = 5
    fps: int = 21
    resolution: str = "720p"
    video_engine: str = "spicy"   # "spicy" | "seedance"
    motion_prompt: str = ""        # shared batch motion prompt; "" -> engine default
    wardrobe_mode: str = "preserve"  # clothing control: preserve | safe | spicy
    #   safer default — start dressed (anti-undress anchor) → fewer censor E005
    # Задача 3: per-batch RIFE smooth flag. Money-safe default OFF; reset to
    # OFF on every new batch (fresh session in begin_source) so multi-user
    # chats never inherit another user's enabled smooth. multiplier reserved
    # for Задача 4 (only ×2 today → 1 = native, no interpolation).
    smooth_enabled: bool = False
    smooth_multiplier: int = 1
    created_at_unix: float = field(default_factory=time.time)
    updated_at_unix: float = field(default_factory=time.time)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchSession":
        targets = [TargetItem(**t) for t in data.get("targets", [])]
        # JSON serialises dict keys as strings; coerce custom-prompt keys back
        # to int so the index mapping survives a persist/reload round-trip.
        raw_prompts = data.get("custom_prompts")
        custom_prompts = (
            {int(k): v for k, v in raw_prompts.items()}
            if isinstance(raw_prompts, dict)
            else None
        )
        return cls(
            chat_id=int(data["chat_id"]),
            status=data.get("status", STATE_IDLE),
            source_path=data.get("source_path"),
            source_face_count=int(data.get("source_face_count", 0)),
            targets=targets,
            cost_estimate=data.get("cost_estimate"),
            cancel_requested=bool(data.get("cancel_requested", False)),
            custom_prompts=custom_prompts,
            prompt_mismatch_info=data.get("prompt_mismatch_info"),
            duration_sec=int(data.get("duration_sec", 5)),
            fps=int(data.get("fps", 21)),
            resolution=str(data.get("resolution", "720p")),
            video_engine=str(data.get("video_engine", "spicy")),
            motion_prompt=str(data.get("motion_prompt", "")),
            wardrobe_mode=str(data.get("wardrobe_mode", "preserve") or "preserve"),
            smooth_enabled=bool(data.get("smooth_enabled", False)),
            smooth_multiplier=int(data.get("smooth_multiplier", 1)),
            created_at_unix=float(data.get("created_at_unix", time.time())),
            updated_at_unix=float(data.get("updated_at_unix", time.time())),
            last_error=data.get("last_error"),
        )


ProgressCb = Callable[[str, dict[str, Any]], None]


class BatchOrchestrator:
    """Owns ``BatchSession`` instances keyed by ``chat_id``.

    Thread-safe: every public method takes ``self._lock`` to serialise
    mutations. Engine calls happen outside the lock (we drop into a
    "running" status, release the lock, run, re-acquire to record results).
    """

    def __init__(
        self,
        *,
        state_root: Path | None = None,
        validator: FaceValidator | None = None,
    ) -> None:
        self._state_root = state_root or Path("state") / "face_swap" / "batches"
        self._sessions: dict[int, BatchSession] = {}
        self._lock = threading.RLock()
        self._validator = validator or FaceValidator()

    # ── lookup ──────────────────────────────────────────────────────────────

    def get(self, chat_id: int) -> BatchSession | None:
        with self._lock:
            return self._sessions.get(chat_id)

    def status(self, chat_id: int) -> str:
        with self._lock:
            sess = self._sessions.get(chat_id)
            return sess.status if sess else STATE_IDLE

    def is_waiting_for_source(self, chat_id: int) -> bool:
        return self.status(chat_id) == STATE_EXPECTING_SOURCE

    def is_waiting_for_targets(self, chat_id: int) -> bool:
        return self.status(chat_id) == STATE_EXPECTING_TARGETS

    def is_in_lockable_phase(self, chat_id: int) -> bool:
        return self.status(chat_id) in _LOCKABLE_STATES

    # ── transitions: setup ──────────────────────────────────────────────────

    def begin_source(self, chat_id: int) -> BatchSession:
        """Move chat into EXPECTING_SOURCE (or reset existing session)."""
        with self._lock:
            existing = self._sessions.get(chat_id)
            if existing and existing.status in _LOCKABLE_STATES:
                raise OrchestratorError(
                    f"chat {chat_id} has an in-flight batch ({existing.status}); "
                    "wait or /swapbatch_cancel first"
                )
            # Anything else → fresh session.
            self._cleanup_files(chat_id)
            sess = BatchSession(
                chat_id=chat_id, status=STATE_EXPECTING_SOURCE
            )
            self._sessions[chat_id] = sess
            self._persist(sess)
            return sess

    def submit_source(self, chat_id: int, source_path: Path) -> BatchSession:
        """Validate the source face and move to SOURCE_RECEIVED.

        Raises:
            OrchestratorError: if state is wrong or source has no face.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_EXPECTING_SOURCE})
            face_count = self._validator.count_faces(source_path)
            if face_count == 0:
                # Stay in EXPECTING_SOURCE; user can resend.
                sess.last_error = "no face detected in source photo"
                self._touch(sess)
                self._persist(sess)
                raise OrchestratorError(
                    "На фото не найдено лицо. Пришли другое фото с чётко "
                    "видимым лицом."
                )

            staged = self._stage_source(chat_id, source_path)
            sess.source_path = str(staged)
            sess.source_face_count = face_count
            sess.last_error = None
            sess.status = STATE_SOURCE_RECEIVED
            self._touch(sess)
            self._persist(sess)
            return sess

    def begin_targets(self, chat_id: int) -> BatchSession:
        """Move from SOURCE_RECEIVED to EXPECTING_TARGETS."""
        with self._lock:
            sess = self._require(chat_id, {STATE_SOURCE_RECEIVED})
            sess.status = STATE_EXPECTING_TARGETS
            sess.targets = []
            sess.cost_estimate = None
            self._touch(sess)
            self._persist(sess)
            return sess

    # ── transitions: ready-photo batch (Задача 2, Вариант A) ────────────────

    def begin_ready_batch(self, chat_id: int) -> BatchSession:
        """Start an animate-only batch of ALREADY-finished photos.

        Mirrors ``begin_source`` but skips the swap setup entirely: a fresh
        session is created in ``EXPECTING_READY_PHOTOS``. Fresh session ⇒
        money-safe defaults (smooth OFF, wardrobe preserve) so a friend never
        inherits another user's enabled smooth and silently pays for it.
        """
        with self._lock:
            existing = self._sessions.get(chat_id)
            if existing and existing.status in _LOCKABLE_STATES:
                raise OrchestratorError(
                    f"chat {chat_id} has an in-flight batch ({existing.status}); "
                    "wait or /swapbatch_cancel first"
                )
            self._cleanup_files(chat_id)
            sess = BatchSession(
                chat_id=chat_id, status=STATE_EXPECTING_READY_PHOTOS
            )
            self._sessions[chat_id] = sess
            self._persist(sess)
            return sess

    def add_ready_photos(
        self, chat_id: int, photo_paths: list[Path]
    ) -> BatchSession:
        """Append already-finished photos, dropping each straight into
        ``swap_result_path`` (the field the animate phase reads).

        No swap is run and nothing is billed here — this is the whole trick of
        Variant A. Accumulates across albums (multi-album intake) and enforces
        the same ``MAX_TARGETS`` cap as the swap batch.

        ADVISORY readability: unreadable files (validator raises) are appended
        with ``valid=False`` and NO ``swap_result_path`` so the animate phase
        skips them instead of choking on a corrupt image.
        """
        if not photo_paths:
            raise OrchestratorError("no photos provided")
        photo_paths = list(dict.fromkeys(photo_paths))
        with self._lock:
            sess = self._require(chat_id, {STATE_EXPECTING_READY_PHOTOS})
            base = len(sess.targets)
            if base + len(photo_paths) > MAX_TARGETS:
                raise OrchestratorError(
                    f"Слишком много фото: {base + len(photo_paths)}. Максимум "
                    f"{MAX_TARGETS} за батч. Уже принято {base} — пришли меньше."
                )
            for offset, raw in enumerate(photo_paths):
                idx = base + offset
                staged = self._stage_target(chat_id, raw, idx)
                try:
                    self._validator.count_faces(staged)
                except Exception as exc:  # noqa: BLE001 — unreadable ⇒ skip, don't crash
                    sess.targets.append(TargetItem(
                        path=str(staged), face_count=0, valid=False,
                        swap_result_path=None, error=f"unreadable: {exc}",
                    ))
                    continue
                # Readable ready photo: its OWN path IS the swap result.
                sess.targets.append(TargetItem(
                    path=str(staged), face_count=0, valid=True,
                    swap_result_path=str(staged), error=None,
                ))
            self._touch(sess)
            self._persist(sess)
            return sess

    def finish_ready_batch(self, chat_id: int) -> BatchSession:
        """Close ready-photo intake → SWAP_DONE, ready for the animate stage.

        Transitions EXPECTING_READY_PHOTOS → SWAP_DONE directly. ``run_swap_phase``
        is never on this path, so the swap ledger is never touched.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_EXPECTING_READY_PHOTOS})
            if not any(t.swap_result_path for t in sess.targets):
                raise OrchestratorError(
                    "Нет готовых фото для анимации — пришли альбом сначала."
                )
            sess.status = STATE_SWAP_DONE
            self._touch(sess)
            self._persist(sess)
            return sess

    def submit_targets(
        self, chat_id: int, target_paths: list[Path]
    ) -> tuple[BatchSession, CostEstimate]:
        """Stage all target photos, validate each, compute cost estimate.

        Moves chat to TARGETS_RECEIVED. Returns (session, estimate).
        """
        if not target_paths:
            raise OrchestratorError("no target photos provided")
        # B-50 defensive dedupe: bot wiring (Telegram media_group buffer)
        # may pass duplicate paths due to retry/race in update delivery.
        # Path-level dedupe preserves order and is O(n).
        target_paths = list(dict.fromkeys(target_paths))
        if len(target_paths) > _SUBMIT_TARGETS_ALBUM_MAX:
            raise OrchestratorError(
                f"Слишком много фото: {len(target_paths)}. Максимум "
                f"{_SUBMIT_TARGETS_ALBUM_MAX} за один батч — пришли меньше и "
                "запусти ещё раз."
            )
        with self._lock:
            sess = self._require(chat_id, {STATE_EXPECTING_TARGETS})
            sess.targets = []
            for idx, raw in enumerate(target_paths):
                staged = self._stage_target(chat_id, raw, idx)
                try:
                    fc = self._validator.count_faces(staged)
                except Exception as exc:  # noqa: BLE001
                    sess.targets.append(
                        TargetItem(
                            path=str(staged), face_count=0, valid=False,
                            error=f"validator failed: {exc}",
                        )
                    )
                    continue
                sess.targets.append(
                    TargetItem(
                        path=str(staged), face_count=fc, valid=fc > 0,
                        error=None if fc > 0 else "no face detected",
                    )
                )

            valid = sum(1 for t in sess.targets if t.valid)
            skipped = len(sess.targets) - valid
            est = estimate_cost(
                valid, skipped,
                swap_usd_per_photo=get_swap_cost_per_photo(),
                cold_start_usd=get_swap_cold_start_usd(),
            )
            sess.cost_estimate = asdict(est)
            sess.status = STATE_TARGETS_RECEIVED
            self._touch(sess)
            self._persist(sess)
            return sess, est

    def add_targets(
        self, chat_id: int, target_paths: list[Path]
    ) -> tuple[BatchSession, CostEstimate]:
        """Append a freshly-flushed album to the batch (multi-album intake).

        Unlike ``submit_targets`` (which resets), this preserves already-staged
        targets so a 100-photo batch arriving as ~10 Telegram albums accumulates
        into one session. Valid from EXPECTING_TARGETS (first album) and
        TARGETS_RECEIVED (subsequent albums). Re-computes the cost estimate over
        the full accumulated set and enforces MAX_TARGETS on the cumulative count.

        ADVISORY validation: the local FaceValidator is informational only.
        - If the validator RAISES (corrupt/unreadable): valid=False — real skip.
        - If the validator returns 0 faces: valid=True — sent to lucataco anyway.
        - If the validator returns >0: valid=True.
        Only unreadable photos are excluded; lucataco is the final face judge.
        """
        if not target_paths:
            raise OrchestratorError("no target photos provided")
        target_paths = list(dict.fromkeys(target_paths))
        with self._lock:
            sess = self._require(
                chat_id, {STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED}
            )
            base = len(sess.targets)
            if base + len(target_paths) > MAX_TARGETS:
                raise OrchestratorError(
                    f"Слишком много фото: {base + len(target_paths)}. Максимум "
                    f"{MAX_TARGETS} за батч. Уже принято {base} — пришли меньше."
                )
            for offset, raw in enumerate(target_paths):
                idx = base + offset
                staged = self._stage_target(chat_id, raw, idx)
                try:
                    fc = self._validator.count_faces(staged)
                except Exception as exc:  # noqa: BLE001
                    # Unreadable/corrupt image — real skip (can't encode for engine).
                    sess.targets.append(TargetItem(
                        path=str(staged), face_count=0, valid=False,
                        error=f"unreadable: {exc}",
                    ))
                    continue
                # Advisory: 0 faces → still valid (lucataco decides).
                sess.targets.append(TargetItem(
                    path=str(staged), face_count=fc, valid=True, error=None,
                ))
            # Bill over ALL readable photos (valid=True); only unreadable skipped.
            valid = sum(1 for t in sess.targets if t.valid)
            skipped = len(sess.targets) - valid
            est = estimate_cost(
                valid, skipped,
                swap_usd_per_photo=get_swap_cost_per_photo(),
                cold_start_usd=get_swap_cold_start_usd(),
            )
            sess.cost_estimate = asdict(est)
            sess.status = STATE_TARGETS_RECEIVED
            self._touch(sess)
            self._persist(sess)
            return sess, est

    def set_quality(
        self, chat_id: int, *, duration_sec: int, fps: int
    ) -> BatchSession:
        """Store per-batch video quality (already validated by the caller).

        Valid whenever a session exists — it is plain configuration the animate
        phase reads later. Raises if there is no session.
        """
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                raise OrchestratorError(
                    f"chat {chat_id} has no batch session; start with "
                    "/swapbatch_source"
                )
            sess.duration_sec = int(duration_sec)
            sess.fps = int(fps)
            self._touch(sess)
            self._persist(sess)
            return sess

    def set_motion_prompt(self, chat_id: int, text: str) -> None:
        """Set the shared batch motion prompt ("" resets to engine default)."""
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                raise OrchestratorError("Нет активного батча.")
            sess.motion_prompt = (text or "").strip()
            self._touch(sess)
            self._persist(sess)

    def set_wardrobe(self, chat_id: int, mode: str) -> None:
        """Set per-batch wardrobe mode (preserve|safe|spicy). Invalid -> safe.

        No-op when there is no active batch (mirrors the handler's own
        no-session guard, so the handler can call this unconditionally)."""
        from app.services.block_m2_video.prompt_assembly import WARDROBE_MODES
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return
            sess.wardrobe_mode = mode if mode in WARDROBE_MODES else "safe"
            self._touch(sess)
            self._persist(sess)

    def set_video_engine(self, chat_id: int, engine_mode: str) -> None:
        """Set per-batch animate engine ("spicy" | "seedance"). No-op w/o session."""
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return
            sess.video_engine = engine_mode
            self._touch(sess)
            self._persist(sess)

    def set_smooth(self, chat_id: int, enabled: bool, multiplier: int = 1) -> None:
        """Set the per-batch RIFE smooth flag. No-op without a session.

        Money-safe: smooth never engages unless explicitly enabled here, and a
        new batch (begin_source) resets it back to OFF via a fresh session.
        ``multiplier`` is reserved for Задача 4 (only ×2 today → defaults 1).
        """
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return
            sess.smooth_enabled = bool(enabled)
            sess.smooth_multiplier = int(multiplier)
            self._touch(sess)
            self._persist(sess)

    def set_animate_quality(
        self, chat_id: int, *, duration: int | None = None, resolution: str | None = None,
    ) -> None:
        """Set managed-engine animate duration/resolution (already validated by
        the caller against the engine's caps)."""
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                raise OrchestratorError("Нет активного батча.")
            if duration is not None:
                sess.duration_sec = duration
            if resolution is not None:
                sess.resolution = resolution
            self._touch(sess)
            self._persist(sess)

    # ── transitions: running phases ─────────────────────────────────────────

    async def confirm_swap(
        self,
        chat_id: int,
        *,
        swap_fn: Callable[
            [Path, list[Path], Callable[[], bool]],
            Awaitable[list[Path | None]],
        ],
        progress_cb: ProgressCb | None = None,
    ) -> list[Path | None]:
        """Run the swap engine on all valid targets.

        ``swap_fn(source, targets, cancel_check) -> list[Path|None]`` —
        injected so the orchestrator does not import FaceSwapEngine directly.
        The bot wiring passes ``engine.swap_batch`` partial-applied with
        ``progress_cb``.

        Transitions: TARGETS_RECEIVED → SWAPPING → SWAP_DONE.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_TARGETS_RECEIVED})
            valid_targets = [
                Path(t.path) for t in sess.targets if t.valid
            ]
            if not valid_targets:
                raise OrchestratorError(
                    "Нет валидных фото для swap. Запусти /swapbatch_cancel и "
                    "начни заново."
                )
            if not sess.source_path:
                raise OrchestratorError("internal: source_path missing")
            source = Path(sess.source_path)
            sess.status = STATE_SWAPPING
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)

        # Engine call runs OUTSIDE the lock.
        def _cancel_check() -> bool:
            with self._lock:
                cur = self._sessions.get(chat_id)
                return bool(cur and cur.cancel_requested)

        results: list[Path | None]
        try:
            results = await swap_fn(source, valid_targets, _cancel_check)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                cur = self._sessions.get(chat_id)
                if cur is not None:
                    cur.last_error = f"swap engine: {exc}"
                    cur.status = STATE_TARGETS_RECEIVED  # allow retry
                    self._touch(cur)
                    self._persist(cur)
            raise

        with self._lock:
            cur = self._sessions.get(chat_id)
            if cur is None:
                # Cancelled and pruned mid-run; nothing to record.
                return results
            valid_iter = iter(results)
            for t in cur.targets:
                if not t.valid:
                    continue
                try:
                    r = next(valid_iter)
                except StopIteration:
                    r = None
                if r is not None:
                    t.swap_result_path = str(r)
                else:
                    t.error = (t.error or "swap failed")
            cur.status = STATE_SWAP_DONE
            self._touch(cur)
            self._persist(cur)
            self._fire(progress_cb, "swap_phase_done", {
                "succeeded": sum(1 for t in cur.targets if t.swap_result_path),
                "failed": sum(
                    1 for t in cur.targets
                    if t.valid and not t.swap_result_path
                ),
            })
        return results

    async def confirm_animate(
        self,
        chat_id: int,
        *,
        animate_fn: Callable[[Path, int, Callable[[], bool]], Awaitable[Path]],
        progress_cb: ProgressCb | None = None,
    ) -> list[Path | None]:
        """Run the video engine on every successfully swapped photo.

        ``animate_fn(swapped_photo, index, cancel_check) -> Path`` — invoked
        once per swapped photo. Errors are caught per-call so a single bad
        animation does not kill the rest.

        Transitions: SWAP_DONE → ANIMATING → DONE.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_SWAP_DONE})
            to_animate: list[tuple[int, Path]] = []
            for idx, t in enumerate(sess.targets):
                if t.swap_result_path:
                    to_animate.append((idx, Path(t.swap_result_path)))
            if not to_animate:
                raise OrchestratorError(
                    "Нет swapped фото для анимации."
                )
            sess.status = STATE_ANIMATING
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)

        def _cancel_check() -> bool:
            with self._lock:
                cur = self._sessions.get(chat_id)
                return bool(cur and cur.cancel_requested)

        results: list[Path | None] = []
        for idx, swapped in to_animate:
            if _cancel_check():
                logger.info(
                    "BatchOrchestrator: animate cancelled at idx=%d", idx
                )
                results.append(None)
                continue
            try:
                video = await animate_fn(swapped, idx, _cancel_check)
                results.append(video)
                with self._lock:
                    cur = self._sessions.get(chat_id)
                    if cur is not None and idx < len(cur.targets):
                        cur.targets[idx].animate_result_path = str(video)
                        self._touch(cur)
                        self._persist(cur)
                self._fire(progress_cb, "animate_step_done", {
                    "index": idx, "video_path": video,
                })
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "BatchOrchestrator: animate idx=%d failed", idx
                )
                results.append(None)
                with self._lock:
                    cur = self._sessions.get(chat_id)
                    if cur is not None and idx < len(cur.targets):
                        cur.targets[idx].error = (
                            (cur.targets[idx].error or "")
                            + f" | animate failed: {exc}"
                        ).strip(" |")
                        self._touch(cur)
                        self._persist(cur)
                self._fire(progress_cb, "animate_step_failed", {
                    "index": idx, "error": str(exc),
                })

        with self._lock:
            cur = self._sessions.get(chat_id)
            if cur is not None:
                cur.status = STATE_DONE
                self._touch(cur)
                self._persist(cur)
        return results

    async def confirm_animate_batch(
        self,
        chat_id: int,
        *,
        animate_fn: Callable[
            [list[Path], Callable[[], bool]], Awaitable[list]
        ],
        progress_cb: ProgressCb | None = None,
    ) -> list:
        """Animate all swapped photos IN PARALLEL via injected ``animate_fn``.

        ``animate_fn(photos, cancel_check) -> list[Path|None]`` aligns to the
        swapped photos in display order; each maps back to its target's
        ``animate_result_path``. Transitions SWAP_DONE -> ANIMATING -> DONE.

        Also accepts AWAITING_CUSTOM_PROMPTS_CONFIRM so the per-photo custom
        flow (Variant A) reuses this single money-safe runner instead of a
        parallel one — the per-photo prompts live in ``sess.custom_prompts`` and
        are read by the bot's ``animate_fn``; this runner is prompt-agnostic.
        """
        with self._lock:
            sess = self._require(
                chat_id,
                {STATE_SWAP_DONE, STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM},
            )
            swapped = [t for t in sess.targets if t.swap_result_path]
            if not swapped:
                raise OrchestratorError("Нет swapped фото для анимации.")
            photos = [Path(t.swap_result_path) for t in swapped]
            sess.status = STATE_ANIMATING
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)

        def _cancel_check() -> bool:
            with self._lock:
                cur = self._sessions.get(chat_id)
                return bool(cur and cur.cancel_requested)

        try:
            results = await animate_fn(photos, _cancel_check)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                cur = self._sessions.get(chat_id)
                if cur is not None:
                    cur.last_error = f"animate engine: {exc}"
                    cur.status = STATE_SWAP_DONE  # allow retry
                    self._touch(cur)
                    self._persist(cur)
            raise

        with self._lock:
            cur = self._sessions.get(chat_id)
            if cur is None:
                return results
            swapped_targets = [t for t in cur.targets if t.swap_result_path]
            for t, r in zip(swapped_targets, results):
                if r is not None:
                    t.animate_result_path = str(r)
            cur.status = STATE_DONE
            self._touch(cur)
            self._persist(cur)
            self._fire(progress_cb, "animate_phase_done", {
                "succeeded": sum(1 for t in cur.targets if t.animate_result_path),
                "failed": sum(1 for t in cur.targets if t.swap_result_path and not t.animate_result_path),
            })
        return results

    def skip_animate(self, chat_id: int) -> BatchSession:
        """User chose /swapbatch_animate_no — terminate at SWAP_DONE → DONE."""
        with self._lock:
            sess = self._require(chat_id, {STATE_SWAP_DONE})
            sess.status = STATE_DONE
            self._touch(sess)
            self._persist(sess)
            return sess

    # ── transitions: custom-prompts sub-flow (Day 6) ────────────────────────

    @staticmethod
    def _swapped_photos(sess: BatchSession) -> list[Path]:
        """Swapped photo paths in display order (the 1-based list shown to the
        user). The Nth entry corresponds to the user's prompt index N."""
        return [
            Path(t.swap_result_path)
            for t in sess.targets
            if t.swap_result_path
        ]

    def start_custom_prompts(self, chat_id: int) -> list[Path]:
        """Enter the custom-prompts flow: SWAP_DONE → AWAITING_CUSTOM_PROMPTS.

        Returns the swapped photos (display order) so the bot can re-display
        them with explicit ``📸 N/M`` numbering before asking for prompts.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_SWAP_DONE})
            photos = self._swapped_photos(sess)
            if not photos:
                raise OrchestratorError("Нет swapped фото для анимации.")
            sess.status = STATE_AWAITING_CUSTOM_PROMPTS
            sess.custom_prompts = None
            sess.prompt_mismatch_info = None
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)
            return photos

    def submit_custom_prompts(
        self, chat_id: int, raw_text: str
    ) -> ParseResult:
        """Parse a numbered-prompt message and advance to the confirm state.

        On success transitions AWAITING_CUSTOM_PROMPTS →
        AWAITING_CUSTOM_PROMPTS_CONFIRM and stores the parsed prompts. On a
        parse error the state is left untouched (still AWAITING) and
        :class:`PromptParseError` propagates for the bot to surface.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_AWAITING_CUSTOM_PROMPTS})
            expected = len(self._swapped_photos(sess))
            # PromptParseError propagates without mutating state → stays AWAITING.
            result = parse_numbered_prompts(raw_text, expected_count=expected)
            sess.custom_prompts = dict(result.prompts)
            sess.prompt_mismatch_info = result.mismatch_info
            sess.status = STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM
            self._touch(sess)
            self._persist(sess)
            return result

    def retry_custom_prompts(self, chat_id: int) -> list[Path]:
        """Discard parsed prompts and return to AWAITING_CUSTOM_PROMPTS.

        Returns the swapped photos again so the bot can re-display them.
        """
        with self._lock:
            sess = self._require(
                chat_id,
                {STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM,
                 STATE_AWAITING_CUSTOM_PROMPTS},
            )
            photos = self._swapped_photos(sess)
            sess.status = STATE_AWAITING_CUSTOM_PROMPTS
            sess.custom_prompts = None
            sess.prompt_mismatch_info = None
            self._touch(sess)
            self._persist(sess)
            return photos

    def cancel_animate(self, chat_id: int) -> BatchSession | None:
        """Exit the animate flow entirely (handles /swapbatch_no and /cancel).

        Closes the session cleanly from any non-engine state: the swapped
        photos have already been sent to the user, so we mark DONE and prune.
        Returns the (pre-prune) session for tally reporting, or ``None`` if no
        session existed.
        """
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return None
            if sess.status in _LOCKABLE_STATES:
                # Engine running — fall back to cooperative cancellation.
                sess.cancel_requested = True
                self._touch(sess)
                self._persist(sess)
                return sess
            sess.status = STATE_DONE
            self._touch(sess)
            self._prune(chat_id)
            return sess

    async def confirm_custom_animate(
        self,
        chat_id: int,
        *,
        animate_fn: Callable[
            [Path, int, "str | None", Callable[[], bool]], Awaitable[Path]
        ],
        progress_cb: ProgressCb | None = None,
    ) -> list[Path | None]:
        """Animate every swapped photo using its stored custom prompt.

        ``animate_fn(swapped_photo, target_idx, prompt, cancel_check) -> Path``
        — invoked once per swapped photo, where ``prompt`` is the user's custom
        prompt for that photo (or ``None`` to use the default). Per-photo errors
        are caught so one bad animation does not halt the batch.

        Transitions: AWAITING_CUSTOM_PROMPTS_CONFIRM → ANIMATING → DONE.
        """
        with self._lock:
            sess = self._require(
                chat_id, {STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM}
            )
            custom = dict(sess.custom_prompts or {})
            to_animate: list[tuple[int, Path]] = []
            for idx, t in enumerate(sess.targets):
                if t.swap_result_path:
                    to_animate.append((idx, Path(t.swap_result_path)))
            if not to_animate:
                raise OrchestratorError("Нет swapped фото для анимации.")
            sess.status = STATE_ANIMATING
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)

        def _cancel_check() -> bool:
            with self._lock:
                cur = self._sessions.get(chat_id)
                return bool(cur and cur.cancel_requested)

        results: list[Path | None] = []
        # display_pos is the user-facing 1-based index; idx is the position in
        # the full targets list (for writing animate_result_path back).
        for display_pos, (idx, swapped) in enumerate(to_animate, start=1):
            prompt = custom.get(display_pos)
            if _cancel_check():
                logger.info(
                    "BatchOrchestrator: custom animate cancelled at idx=%d", idx
                )
                results.append(None)
                continue
            try:
                video = await animate_fn(swapped, idx, prompt, _cancel_check)
                results.append(video)
                with self._lock:
                    cur = self._sessions.get(chat_id)
                    if cur is not None and idx < len(cur.targets):
                        cur.targets[idx].animate_result_path = str(video)
                        self._touch(cur)
                        self._persist(cur)
                self._fire(progress_cb, "animate_step_done", {
                    "index": idx, "video_path": video,
                })
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "BatchOrchestrator: custom animate idx=%d failed", idx
                )
                results.append(None)
                with self._lock:
                    cur = self._sessions.get(chat_id)
                    if cur is not None and idx < len(cur.targets):
                        cur.targets[idx].error = (
                            (cur.targets[idx].error or "")
                            + f" | animate failed: {exc}"
                        ).strip(" |")
                        self._touch(cur)
                        self._persist(cur)
                self._fire(progress_cb, "animate_step_failed", {
                    "index": idx, "error": str(exc),
                })

        with self._lock:
            cur = self._sessions.get(chat_id)
            if cur is not None:
                cur.status = STATE_DONE
                self._touch(cur)
                self._persist(cur)
        return results

    # ── control ─────────────────────────────────────────────────────────────

    def cancel(self, chat_id: int) -> bool:
        """Request cancellation. Returns True if a session existed."""
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return False
            if sess.status in _LOCKABLE_STATES:
                # Signal running coroutine; engine will see it via cancel_check.
                sess.cancel_requested = True
                self._touch(sess)
                self._persist(sess)
                return True
            # Idle / setup state — just delete the session.
            self._prune(chat_id)
            return True

    def prune(self, chat_id: int) -> None:
        """Forget a session (terminal or otherwise) and delete its files."""
        with self._lock:
            self._prune(chat_id)

    # ── persistence ─────────────────────────────────────────────────────────

    def session_dir(self, chat_id: int) -> Path:
        return self._state_root / str(chat_id)

    def session_file(self, chat_id: int) -> Path:
        return self.session_dir(chat_id) / "session.json"

    def reload_from_disk(self) -> list[int]:
        """Scan ``state_root`` and reload any persisted sessions.

        Returns the list of chat_ids restored. Sessions found in a lockable
        state are reset to ``STATE_FAILED_RESUMED`` because we cannot safely
        resume mid-engine.
        """
        restored: list[int] = []
        if not self._state_root.exists():
            return restored
        for child in self._state_root.iterdir():
            if not child.is_dir():
                continue
            sess_file = child / "session.json"
            if not sess_file.exists():
                continue
            try:
                data = json.loads(sess_file.read_text(encoding="utf-8"))
                sess = BatchSession.from_dict(data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reload_from_disk: skipped %s (%s)", sess_file, exc
                )
                continue
            if sess.status in _LOCKABLE_STATES:
                sess.status = STATE_FAILED_RESUMED
                sess.last_error = (
                    "bot restarted while engine was running; manual restart "
                    "required"
                )
            with self._lock:
                self._sessions[sess.chat_id] = sess
            restored.append(sess.chat_id)
        return restored

    # ── private helpers ─────────────────────────────────────────────────────

    def _require(
        self, chat_id: int, allowed: set[str]
    ) -> BatchSession:
        sess = self._sessions.get(chat_id)
        if sess is None:
            raise OrchestratorError(
                f"chat {chat_id} has no batch session; start with "
                "/swapbatch_source"
            )
        if sess.status not in allowed:
            raise OrchestratorError(
                f"chat {chat_id} is in state {sess.status!r}; "
                f"expected one of {sorted(allowed)}"
            )
        return sess

    def _stage_source(self, chat_id: int, source_path: Path) -> Path:
        d = self.session_dir(chat_id)
        d.mkdir(parents=True, exist_ok=True)
        suffix = Path(source_path).suffix or ".jpg"
        target = d / f"source{suffix}"
        shutil.copyfile(source_path, target)
        return target

    def _stage_target(
        self, chat_id: int, target_path: Path, idx: int
    ) -> Path:
        d = self.session_dir(chat_id) / "targets"
        d.mkdir(parents=True, exist_ok=True)
        suffix = Path(target_path).suffix or ".jpg"
        target = d / f"{idx:03d}{suffix}"
        shutil.copyfile(target_path, target)
        return target

    def _persist(self, sess: BatchSession) -> None:
        d = self.session_dir(sess.chat_id)
        d.mkdir(parents=True, exist_ok=True)
        f = d / "session.json"
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(sess.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(f)

    def _touch(self, sess: BatchSession) -> None:
        sess.updated_at_unix = time.time()

    def _prune(self, chat_id: int) -> None:
        self._sessions.pop(chat_id, None)
        self._cleanup_files(chat_id)

    def _cleanup_files(self, chat_id: int) -> None:
        d = self.session_dir(chat_id)
        if d.exists():
            try:
                shutil.rmtree(d)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "BatchOrchestrator: cleanup %s failed: %s", d, exc
                )

    @staticmethod
    def _fire(cb: ProgressCb | None, stage: str, payload: dict) -> None:
        if cb is None:
            return
        try:
            cb(stage, payload)
        except Exception:
            logger.exception("progress_cb stage=%s raised; ignoring", stage)


# Module-level singleton accessor (used by handler + bot wiring).
_SINGLETON: BatchOrchestrator | None = None
_SINGLETON_LOCK = threading.Lock()


def get_orchestrator() -> BatchOrchestrator:
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = BatchOrchestrator()
    return _SINGLETON


def reset_singleton_for_test() -> None:
    """Test-only: reset the module-level singleton so each test gets a fresh
    orchestrator. Production code MUST NOT call this."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None
