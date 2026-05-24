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
from .face_validator import FaceValidator

logger = logging.getLogger(__name__)


# Single source of truth for state-machine names.
STATE_IDLE = "IDLE"
STATE_EXPECTING_SOURCE = "EXPECTING_SOURCE"
STATE_SOURCE_RECEIVED = "SOURCE_RECEIVED"
STATE_EXPECTING_TARGETS = "EXPECTING_TARGETS"
STATE_TARGETS_RECEIVED = "TARGETS_RECEIVED"
STATE_SWAPPING = "SWAPPING"
STATE_SWAP_DONE = "SWAP_DONE"
STATE_ANIMATING = "ANIMATING"
STATE_DONE = "DONE"
STATE_FAILED_RESUMED = "FAILED_RESUMED"

_TERMINAL_STATES = {STATE_DONE, STATE_FAILED_RESUMED}
_LOCKABLE_STATES = {STATE_SWAPPING, STATE_ANIMATING}


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
    created_at_unix: float = field(default_factory=time.time)
    updated_at_unix: float = field(default_factory=time.time)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchSession":
        targets = [TargetItem(**t) for t in data.get("targets", [])]
        return cls(
            chat_id=int(data["chat_id"]),
            status=data.get("status", STATE_IDLE),
            source_path=data.get("source_path"),
            source_face_count=int(data.get("source_face_count", 0)),
            targets=targets,
            cost_estimate=data.get("cost_estimate"),
            cancel_requested=bool(data.get("cancel_requested", False)),
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
            est = estimate_cost(valid, skipped)
            sess.cost_estimate = asdict(est)
            sess.status = STATE_TARGETS_RECEIVED
            self._touch(sess)
            self._persist(sess)
            return sess, est

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

    def skip_animate(self, chat_id: int) -> BatchSession:
        """User chose /swapbatch_animate_no — terminate at SWAP_DONE → DONE."""
        with self._lock:
            sess = self._require(chat_id, {STATE_SWAP_DONE})
            sess.status = STATE_DONE
            self._touch(sess)
            self._persist(sess)
            return sess

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
        target = d / f"{idx:02d}{suffix}"
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
