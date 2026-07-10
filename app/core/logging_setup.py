# -*- coding: utf-8 -*-
"""Application-wide file logging for Jarvis V3 (M.1.5 #2).

Attaches a RotatingFileHandler to the root logger so every module's
output is captured in logs/<filename>.log.

Block M services keep their own dedicated logger configured via
app.services.block_m_common.logging_setup -- this module does NOT
touch the `block_m` namespace.

Usage (call ONCE at startup, as early as possible):

    from app.core.logging_setup import setup_app_logging
    setup_app_logging()                  # backend -> logs/jarvis.log
    setup_app_logging("jarvis_bot.log")  # bot     -> logs/jarvis_bot.log

Environment overrides:
    JARVIS_LOG_LEVEL    DEBUG | INFO | WARNING | ERROR (file level)
    JARVIS_LOG_DIR      override default logs/ directory
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 5

_TEST_LOG_FILENAME = "test_run.log"
_PRODUCTION_LOG_NAMES = {"jarvis.log", "jarvis_bot.log"}

# project root: app/core/logging_setup.py -> app/core -> app -> ROOT
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _running_under_pytest() -> bool:
    # PYTEST_CURRENT_TEST is only set once a test is actually executing
    # (setup/call/teardown); PYTEST_VERSION is set by pytest's own bootstrap
    # for the whole process, including collection -- before any module-level
    # `setup_app_logging(...)` call (e.g. app/main.py) would otherwise slip
    # a line into the production log ahead of the first test running.
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"))


def strip_production_file_handlers() -> list[str]:
    """Remove any root-logger RotatingFileHandler that targets a production
    log file (jarvis.log / jarvis_bot.log).

    Safety net for handlers attached some other way than
    ``setup_app_logging`` (e.g. a stray ``logging.FileHandler`` pointed
    directly at a production path). See root conftest.py.

    Returns the basenames removed.
    """
    root = logging.getLogger()
    removed: list[str] = []
    for handler in list(root.handlers):
        if not isinstance(handler, logging.handlers.RotatingFileHandler):
            continue
        try:
            name = Path(handler.baseFilename).name
        except Exception:
            continue
        if name in _PRODUCTION_LOG_NAMES:
            root.removeHandler(handler)
            handler.close()
            removed.append(name)
    return removed


def _logs_dir() -> Path:
    override = os.getenv("JARVIS_LOG_DIR", "").strip()
    if override:
        return Path(override)
    return _PROJECT_ROOT / "logs"


def _resolved_file_level(default: int = logging.DEBUG) -> int:
    env = os.getenv("JARVIS_LOG_LEVEL", "").strip().upper()
    if env in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return getattr(logging, env)
    return default


def setup_app_logging(
    log_filename: str = "jarvis.log",
    file_level: int | None = None,
    console_level: int = logging.INFO,
) -> Path:
    """Attach a RotatingFileHandler to the root logger.

    Idempotent: a second call with the same filename is a no-op.

    Returns:
        Absolute path to the log file.
    """
    if _running_under_pytest():
        log_filename = _TEST_LOG_FILENAME

    log_path = _logs_dir() / log_filename
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if file_level is None:
        file_level = _resolved_file_level()

    root = logging.getLogger()

    # Idempotent: if a RotatingFileHandler for this exact path is already there, exit
    target = str(log_path.resolve()).lower()
    for h in root.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            try:
                existing = str(Path(h.baseFilename).resolve()).lower()
            except Exception:
                continue
            if existing == target:
                return log_path

    # Lower root level so DEBUG/INFO can actually reach handlers
    if root.level == 0 or root.level > file_level:
        root.setLevel(file_level)

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Add console handler only if there isn't one already
    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # Tame noisy third-party libs
    for noisy in ("urllib3", "httpx", "httpcore", "watchfiles", "telebot", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.info(
        "App logging initialised: %s (file=%s, console=%s)",
        log_path,
        logging.getLevelName(file_level),
        logging.getLevelName(console_level),
    )
    return log_path