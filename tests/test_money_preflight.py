# -*- coding: utf-8 -*-
"""DEV-3: money-preflight helper — assert-запрет submit с пустым/неполным payload.

``preflight_check(endpoint, payload, required_keys=...)`` — последняя линия
защиты ПЕРЕД любым платным submit (fal/replicate/wavespeed): пустой payload
или payload без обязательного поля должен упасть AssertionError'ом до сети,
а не потратить деньги, чтобы узнать об этом от провайдера.
"""
import pytest

from app.services.money_preflight import preflight_check


def test_rejects_empty_dict_payload():
    with pytest.raises(AssertionError):
        preflight_check("some/endpoint", {})


def test_rejects_none_payload():
    with pytest.raises(AssertionError):
        preflight_check("some/endpoint", None)


def test_accepts_nonempty_flat_payload():
    preflight_check("some/endpoint", {"prompt": "hello"})  # no raise


def test_rejects_empty_input_wrapper():
    # Replicate-style payloads wrap the real body under "input".
    with pytest.raises(AssertionError):
        preflight_check("owner/model", {"input": {}})


def test_accepts_nonempty_input_wrapper():
    preflight_check("owner/model", {"input": {"prompt": "hi", "image": "url"}})


def test_rejects_missing_required_key():
    with pytest.raises(AssertionError):
        preflight_check(
            "owner/model",
            {"input": {"prompt": "hi"}},
            required_keys=("prompt", "image"),
        )


def test_rejects_empty_string_required_key():
    with pytest.raises(AssertionError):
        preflight_check(
            "owner/model",
            {"input": {"prompt": "hi", "image": ""}},
            required_keys=("prompt", "image"),
        )


def test_accepts_all_required_keys_present():
    preflight_check(
        "owner/model",
        {"input": {"prompt": "hi", "image": "url"}},
        required_keys=("prompt", "image"),
    )
