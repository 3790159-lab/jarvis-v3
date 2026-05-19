"""Centralized translation of exceptions to short user-friendly Russian messages.

Callers are responsible for logging the full exception (logger.exception or
exc_info=True) inside their except block. This module is a pure function and
performs no side effects.
"""

from __future__ import annotations

import asyncio
import json

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


_DEFAULT = "Что-то пошло не так. Подробности в логах."


def translate_exception(exc: BaseException) -> str:
    """Map an exception to a short user-friendly Russian message.

    Pure function — no side effects, no logging. Callers retain
    responsibility for logger.exception() in their except block.
    """
    if isinstance(exc, KeyError):
        # KeyError('foo').args[0] == 'foo' (no quotes).
        # str(KeyError('foo')) == "'foo'" — avoid that.
        if exc.args:
            key = exc.args[0]
        else:
            key = ""
        return f"Не хватает данных: {key}"

    if isinstance(exc, FileNotFoundError):
        return "Файл не найден"

    if isinstance(exc, PermissionError):
        return "Нет доступа"

    if isinstance(exc, json.JSONDecodeError):
        # JSONDecodeError is a subclass of ValueError — check it first.
        return "Не смог разобрать данные"

    if isinstance(exc, ValueError):
        return "Неверное значение"

    if isinstance(exc, TypeError):
        return "Неверный тип данных"

    # On Python 3.11+, asyncio.TimeoutError is an alias for builtins.TimeoutError.
    # isinstance against both covers older code paths that still raise the asyncio one.
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Превышено время ожидания"

    if _HAS_AIOHTTP and isinstance(exc, aiohttp.ClientError):
        return "Сетевая ошибка"

    if _HAS_HTTPX and isinstance(exc, httpx.HTTPError):
        return "Сетевая ошибка"

    return _DEFAULT
