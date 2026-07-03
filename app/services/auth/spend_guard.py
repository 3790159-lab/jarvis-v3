"""Единый money-гейт для платных операций.

Инкапсулирует канонический паттерн (эталон ``face_swap_handler``):
``check_limit`` СТРОГО до траты; ``do_spend()`` выполняется только если allowed;
``record_cost`` ТОЛЬКО при truthy-результате (провал/None → не платим).

Гейтить на границе хендлера, где есть ``chat_id`` (== user_id). Сервисы генерации
не трогаем — оборачиваем их вызов через ``do_spend``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Tuple

from app.services.auth.access_control import check_limit
from app.services.audit import cost_tracker

logger = logging.getLogger(__name__)


def guard_spend(
    user_id: Any,
    username: Optional[str],
    estimated_usd: float,
    do_spend: Callable[[], Any],
) -> Tuple[Any, Optional[str]]:
    """Гейтит платную операцию.

    Возврат ``(result, error_reason)``:
      * ``error_reason`` != ``None`` → заблокировано лимитом; ``do_spend`` НЕ вызывался,
        ничего не потрачено и не записано в леджер.
      * ``error_reason`` == ``None`` → гейт пройден; ``result`` — то, что вернул
        ``do_spend`` (может быть falsy при сбое сервиса — тогда леджер не пишется).

    admin безлимитен (``check_limit`` вернёт allowed), но ``record_cost`` пишется и ему,
    чтобы траты попадали в ``/costs``.
    """
    allowed, reason = check_limit(user_id, estimated_usd=estimated_usd)
    if not allowed:
        return None, reason
    result = do_spend()
    if result:
        try:
            cost_tracker.record_cost(user_id, username, estimated_usd)
        except Exception as exc:  # noqa: BLE001 — учёт не должен ронять доставку
            logger.warning("guard_spend: record_cost failed: %s", exc)
    return result, None
