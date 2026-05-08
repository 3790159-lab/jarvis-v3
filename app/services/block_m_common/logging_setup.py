# -*- coding: utf-8 -*-
"""Rotating file logger for all Block M services.

Usage anywhere in the block_m stack:
    from app.services.block_m_common.logging_setup import get_logger
    logger = get_logger("persona_creator")   # → block_m.persona_creator
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOG_FILE = _ROOT / "logs" / "block_m.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 3
_FMT = "%(asctime)s %(levelname)-8s %(name)-35s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_SENTINEL = "block_m._setup_done"


def setup_block_m_logging(
    log_file: Path | None = None,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """Attach a RotatingFileHandler to the root ``block_m`` logger.

    Safe to call multiple times — the handler is added only once.

    Args:
        log_file: Override log path (defaults to logs/block_m.log).
        level: Log level for the block_m namespace (default DEBUG).

    Returns:
        The configured ``block_m`` logger.
    """
    root_logger = logging.getLogger("block_m")

    # Idempotent: skip if already configured
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root_logger.handlers):
        return root_logger

    root_logger.setLevel(level)
    path = log_file or _LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root_logger.addHandler(handler)

    root_logger.info("Block M file logging initialised → %s", path)
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``block_m`` hierarchy.

    Ensures the file handler is always attached before returning.

    Args:
        name: Short module name, e.g. ``"persona_creator"``.

    Returns:
        ``logging.Logger`` named ``block_m.<name>``.
    """
    setup_block_m_logging()
    return logging.getLogger(f"block_m.{name}")
