"""Unit tests for app.services.error_translator."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.error_translator import translate_exception


def test_key_error_extracts_key_without_quotes():
    msg = translate_exception(KeyError("user_id"))
    assert msg == "Не хватает данных: user_id"
    assert "'" not in msg
    assert '"' not in msg


def test_key_error_empty_args():
    msg = translate_exception(KeyError())
    assert msg == "Не хватает данных: "


def test_file_not_found():
    assert translate_exception(FileNotFoundError("missing.txt")) == "Файл не найден"


def test_permission_error():
    assert translate_exception(PermissionError("denied")) == "Нет доступа"


def test_value_error():
    assert translate_exception(ValueError("bad value")) == "Неверное значение"


def test_type_error():
    assert translate_exception(TypeError("wrong type")) == "Неверный тип данных"


def test_asyncio_timeout_error():
    assert translate_exception(asyncio.TimeoutError()) == "Превышено время ожидания"


def test_builtin_timeout_error():
    # Python 3.11+ aliases asyncio.TimeoutError -> builtins.TimeoutError.
    # Either form must resolve to the timeout message.
    assert translate_exception(TimeoutError()) == "Превышено время ожидания"


def test_json_decode_error_takes_priority_over_value_error():
    # JSONDecodeError is a subclass of ValueError — must not fall through.
    exc = json.JSONDecodeError("expected value", "abc", 0)
    assert translate_exception(exc) == "Не смог разобрать данные"


def test_default_for_unmapped_exception():
    class CustomError(Exception):
        pass

    assert (
        translate_exception(CustomError("boom"))
        == "Что-то пошло не так. Подробности в логах."
    )


def test_runtime_error_falls_through_to_default():
    assert (
        translate_exception(RuntimeError("oops"))
        == "Что-то пошло не так. Подробности в логах."
    )


def test_subclass_of_mapped_type_still_resolves():
    class MyValueError(ValueError):
        pass

    assert translate_exception(MyValueError("x")) == "Неверное значение"


def test_subclass_of_file_not_found():
    class MyFileNotFound(FileNotFoundError):
        pass

    assert translate_exception(MyFileNotFound()) == "Файл не найден"


def test_aiohttp_client_error_when_available():
    aiohttp = pytest.importorskip("aiohttp")
    assert translate_exception(aiohttp.ClientError("net")) == "Сетевая ошибка"


def test_aiohttp_client_error_subclass_when_available():
    aiohttp = pytest.importorskip("aiohttp")
    # ClientConnectionError is a subclass of ClientError.
    assert translate_exception(aiohttp.ClientConnectionError()) == "Сетевая ошибка"


def test_httpx_http_error_when_available():
    httpx = pytest.importorskip("httpx")
    assert translate_exception(httpx.HTTPError("net")) == "Сетевая ошибка"
