#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU sniper: poll RunPod EU-RO-1 for A100 supply and spawn a pod on catch.

Solves the "manually-clicking-Deploy-loses-the-race" problem for scarce
regions. Loops on :meth:`RunpodClient.start_pod` (which already cascades
through ``RUNPOD_GPU_TYPE_ID`` -> ``RUNPOD_GPU_FALLBACK_ID`` internally),
catches :class:`RunpodSupplyError`, jitters the sleep to avoid sync-poll
patterns, and writes ``state/runpod_sniper_status.json`` every iteration
so the main session can monitor progress.

Hard caps: 2-hour max duration, 360 attempts, 20s nominal poll interval.

Telegram alerts are fire-and-forget — if the bot fails, the sniper still
records its outcome in the status file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.runpod.runpod_client import (  # noqa: E402
    PodInfo,
    RunpodApiError,
    RunpodClient,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (  # noqa: E402
    RunpodConfig,
    get_runpod_config,
)
from app.services.block_m2_video.runpod.pod_provisioner import (  # noqa: E402
    ProvisionOutcome,
    ProvisionResult,
    wait_for_pod_ready,
)
from app.services.notifications import send_alert  # noqa: E402

logger = logging.getLogger("runpod_gpu_sniper")

DEFAULT_MAX_DURATION_MIN = 120
DEFAULT_POLL_INTERVAL_SEC = 20
HARD_ATTEMPT_CAP = 360
JITTER_SEC = 5.0

STATUS_PATH = _ROOT / "state" / "runpod_sniper_status.json"
LOG_PATH = _ROOT / "state" / "logs" / "runpod_sniper.log"


_interrupted = False


def _sigint_handler(_signum: int, _frame: Any) -> None:
    global _interrupted
    _interrupted = True
    print("[sigint] Ctrl+C received - will exit after current iteration", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(payload: dict[str, Any]) -> None:
    """Atomic-ish status write: write to .tmp then replace."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def _safe_notify(text: str, *, enabled: bool) -> None:
    """Fire-and-forget Telegram alert. Never raises."""
    if not enabled:
        logger.info("[notify] suppressed (--no-notify): %s", text)
        return
    try:
        ok = send_alert(text)
        if not ok:
            logger.warning("[notify] send_alert returned False (not configured?)")
    except Exception as exc:  # noqa: BLE001 - alerting must never crash sniper
        logger.warning("[notify] failed: %s", exc)


def _pod_to_status(pod: PodInfo) -> dict[str, Any]:
    runtime = pod.runtime or {}
    ports = runtime.get("ports") or []
    public_url = None
    for entry in ports:
        if entry.get("privatePort") == 8188 and entry.get("type") == "http":
            public_url = f"https://{pod.id}-8188.proxy.runpod.net"
            break
    if public_url is None and pod.id:
        public_url = f"https://{pod.id}-8188.proxy.runpod.net"
    return {
        "pod_id": pod.id,
        "pod_name": pod.name,
        "pod_desired_status": pod.desired_status,
        "pod_public_url": public_url,
    }


async def _snipe(
    *,
    max_duration_min: int,
    poll_interval_sec: int,
    notify: bool,
    dry_run: bool,
    config: RunpodConfig | None = None,
    client: RunpodClient | None = None,
    sleeper: Any = None,
    clock: Any = None,
) -> int:
    """Core sniper loop. Returns process exit code.

    ``config`` / ``client`` / ``sleeper`` / ``clock`` are injectable for
    tests — production code passes ``None`` and gets defaults.
    """
    cfg = config or get_runpod_config()
    sleep = sleeper if sleeper is not None else asyncio.sleep
    monotonic = clock if clock is not None else time.monotonic

    deadline_sec = max_duration_min * 60
    start = monotonic()
    started_iso = _now_iso()
    name = f"jarvis-i2v-sniper-{int(time.time())}"

    base_status: dict[str, Any] = {
        "status": "polling",
        "started_at": started_iso,
        "last_attempt_at": started_iso,
        "attempts": 0,
        "pod_id": None,
        "pod_public_url": None,
        "pod_name": name,
        "error_detail": None,
        "config": {
            "datacenter": cfg.datacenter,
            "gpu_primary": cfg.gpu_type_id,
            "gpu_fallback": cfg.gpu_fallback_id,
            "max_duration_min": max_duration_min,
            "poll_interval_sec": poll_interval_sec,
            "dry_run": dry_run,
        },
    }
    _write_status(base_status)

    own_client = client is None
    rp_client = client or RunpodClient(config=cfg)
    try:
        for attempt in range(1, HARD_ATTEMPT_CAP + 1):
            if _interrupted:
                base_status.update(
                    status="error",
                    error_detail="interrupted by SIGINT",
                    attempts=attempt - 1,
                    last_attempt_at=_now_iso(),
                )
                _write_status(base_status)
                logger.info("[sniper] interrupted, exiting")
                return 130

            elapsed = monotonic() - start
            if elapsed >= deadline_sec:
                base_status.update(
                    status="timeout",
                    error_detail=(
                        f"deadline reached after {elapsed:.0f}s "
                        f"({attempt - 1} attempts)"
                    ),
                    attempts=attempt - 1,
                    last_attempt_at=_now_iso(),
                )
                _write_status(base_status)
                logger.warning("[sniper] timeout after %.0fs", elapsed)
                _safe_notify(
                    f"⏰ RunPod sniper timeout: no A100 in {cfg.datacenter} "
                    f"after {elapsed/60:.0f} min ({attempt - 1} attempts)",
                    enabled=notify,
                )
                return 2

            base_status["attempts"] = attempt
            base_status["last_attempt_at"] = _now_iso()
            _write_status(base_status)

            if dry_run:
                logger.info(
                    "[sniper] DRY-RUN attempt %d/%d (would call start_pod name=%s)",
                    attempt,
                    HARD_ATTEMPT_CAP,
                    name,
                )
                # In dry-run, simulate a single supply-empty cycle then exit cleanly.
                base_status.update(
                    status="caught",
                    pod_id="DRY-RUN-NO-POD",
                    pod_public_url=None,
                    error_detail="dry-run: no real pod was created",
                )
                _write_status(base_status)
                return 0

            try:
                pod = await rp_client.start_pod(name=name)
            except RunpodSupplyError as exc:
                wait = max(
                    1.0,
                    poll_interval_sec + random.uniform(-JITTER_SEC, JITTER_SEC),
                )
                logger.info(
                    "[sniper] supply empty (attempt %d/%d): %s | sleeping %.1fs",
                    attempt,
                    HARD_ATTEMPT_CAP,
                    exc,
                    wait,
                )
                await sleep(wait)
                continue
            except RunpodApiError as exc:
                base_status.update(
                    status="error",
                    error_detail=f"RunpodApiError: {exc}",
                    last_attempt_at=_now_iso(),
                )
                _write_status(base_status)
                logger.error("[sniper] non-supply API error, exiting: %s", exc)
                _safe_notify(
                    f"❌ RunPod sniper aborted (API error): {exc}",
                    enabled=notify,
                )
                return 1
            except Exception as exc:  # noqa: BLE001
                base_status.update(
                    status="error",
                    error_detail=f"{type(exc).__name__}: {exc}",
                    last_attempt_at=_now_iso(),
                )
                _write_status(base_status)
                logger.exception("[sniper] unexpected error, exiting")
                _safe_notify(
                    f"❌ RunPod sniper crashed: {type(exc).__name__}: {exc}",
                    enabled=notify,
                )
                return 1

            # Caught a pod
            pod_status = _pod_to_status(pod)
            base_status.update(
                status="caught",
                last_attempt_at=_now_iso(),
                **pod_status,
            )
            _write_status(base_status)
            logger.info(
                "[sniper] CAUGHT pod=%s url=%s after %d attempts",
                pod_status["pod_id"],
                pod_status["pod_public_url"],
                attempt,
            )
            # Existing catch alert (with tweak — append the bootstrap-wait note)
            _safe_notify(
                (
                    f"🎯 RunPod sniper caught A100 in {cfg.datacenter}!\n"
                    f"pod_id: {pod_status['pod_id']}\n"
                    f"url: {pod_status['pod_public_url']}\n"
                    f"attempts: {attempt}\n"
                    f"(waiting for ComfyUI bootstrap, will alert when ready)"
                ),
                enabled=notify,
            )

            # Provisioning callback — passive readiness observation
            result = await wait_for_pod_ready(
                rp_client,
                pod,
                interrupted=lambda: _interrupted,
            )

            if result.outcome is ProvisionOutcome.READY:
                _safe_notify(
                    f"✅ Pod ready: {result.public_url}\n"
                    f"pod_id: {result.pod_id}\n"
                    f"{result.detail}",
                    enabled=notify,
                )
                return 0

            # Tasks 8-9 add CONTAINER_EXITED / TIMEOUT / SIGINT branches.
            return 0

        # Hard attempt cap reached without timeout (unusual: very short poll interval)
        base_status.update(
            status="timeout",
            error_detail=f"hard attempt cap reached ({HARD_ATTEMPT_CAP})",
            last_attempt_at=_now_iso(),
        )
        _write_status(base_status)
        _safe_notify(
            f"⏰ RunPod sniper hit hard attempt cap ({HARD_ATTEMPT_CAP})",
            enabled=notify,
        )
        return 2
    finally:
        if own_client:
            await rp_client.aclose()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Poll RunPod for scarce A100 supply and spawn a pod on catch."
    )
    p.add_argument(
        "--max-duration-min",
        type=int,
        default=DEFAULT_MAX_DURATION_MIN,
        help="Hard deadline in minutes (default: 120)",
    )
    p.add_argument(
        "--poll-interval-sec",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help="Nominal seconds between polls (default: 20)",
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip Telegram alerts (useful for testing)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen but don't actually call start_pod",
    )
    return p


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def _load_env_files() -> None:
    """Populate os.environ from .env / .env.runpod so send_alert can find
    TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_ID.

    pydantic-settings reads .env into RunpodConfig but does NOT touch
    os.environ, and TelegramNotifier uses os.getenv directly — without
    this call the catch alert silently fails (returns False).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning(
            "[sniper] python-dotenv not installed; Telegram alerts may not work"
        )
        return
    for env_file in (".env", ".env.runpod"):
        path = _ROOT / env_file
        if path.exists():
            load_dotenv(path, override=False)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging()
    _load_env_files()
    signal.signal(signal.SIGINT, _sigint_handler)
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "[sniper] starting: max=%dmin interval=%ds notify=%s dry_run=%s",
        args.max_duration_min,
        args.poll_interval_sec,
        not args.no_notify,
        args.dry_run,
    )
    return asyncio.run(
        _snipe(
            max_duration_min=args.max_duration_min,
            poll_interval_sec=args.poll_interval_sec,
            notify=not args.no_notify,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
