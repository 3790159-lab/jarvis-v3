# -*- coding: utf-8 -*-
"""DEV-3 money-preflight standard: assert-запрет submit с пустым/неполным payload.

Последняя линия защиты, которую вызывают все fal/replicate/wavespeed клиенты
ПЕРЕД платным submit (см. CLAUDE.md, раздел "Money-preflight"). Вызывающий код
всё равно обязан строить payload правильно — это НЕ замена валидации, а
защита от бага, который иначе тихо отправит пустой/неполный payload и
потратит деньги, чтобы узнать об этом от провайдера через 4xx/бракованный
результат.
"""
from __future__ import annotations

from typing import Iterable

_EMPTY = (None, "", [], {}, ())


def preflight_check(
    endpoint: str,
    payload: dict | None,
    required_keys: Iterable[str] = (),
) -> None:
    """Assert ``payload`` (и перечисленные ``required_keys``) непустые.

    Raises:
        AssertionError: ``payload`` пуст/``None``, либо один из
            ``required_keys`` отсутствует или пуст.
    """
    assert payload, f"money-preflight: refusing empty payload for {endpoint}"
    body = payload.get("input", payload) if isinstance(payload, dict) else payload
    assert isinstance(body, dict) and body, (
        f"money-preflight: refusing empty payload body for {endpoint}"
    )
    for key in required_keys:
        assert body.get(key) not in _EMPTY, (
            f"money-preflight: refusing incomplete payload for {endpoint} "
            f"(missing/empty required field {key!r})"
        )
