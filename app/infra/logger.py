from __future__ import annotations

import logging
import os

try:
    from app.core.config import Settings
except Exception:
    Settings = None


def _resolve_log_level() -> int:
    value = os.getenv("LOG_LEVEL", "INFO")

    if Settings is not None:
        try:
            settings = Settings()
            candidate = getattr(settings, "LOG_LEVEL", None)
            if candidate:
                value = str(candidate)
        except Exception:
            pass

    value = str(value).strip().upper()
    return getattr(logging, value, logging.INFO)


def setup_logger(name: str = "jarvis") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        logger.setLevel(_resolve_log_level())
        return logger

    logger.setLevel(_resolve_log_level())

    handler = logging.StreamHandler()
    handler.setLevel(_resolve_log_level())

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = setup_logger()
