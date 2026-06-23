# app/services/auth/access_control.py
# -*- coding: utf-8 -*-
"""Лимит-гейт ДО траты. Чистая склейка users_store (лимит/override) и
audit.cost_tracker (потрачено сегодня). admin → безлимит; неизвестный → блок.

Возвращает (allowed, reason). reason пуст при allowed=True; иначе — мягкий
русский текст для пользователя.
"""
from __future__ import annotations

from typing import Tuple

from app.services.audit import cost_tracker as _ct
from app.services.auth import users_store as _us


def check_limit(user_id: int, *, estimated_usd: float) -> Tuple[bool, str]:
    role = _us.get_role(user_id)
    if role is None:
        return False, "Нет доступа."
    if role == "admin":
        return True, ""
    limit = _us.get_limit(user_id)
    if limit is None:
        return True, ""
    spent_today = float(_ct.get_user_stats(user_id).get("today", 0.0))
    effective = _us.effective_spent(user_id, spent_today=spent_today)
    if effective + float(estimated_usd) > limit:
        return False, (
            f"Дневной лимит ${limit:.2f} исчерпан "
            f"(потрачено ${effective:.2f}). Напиши Даниилу, если нужно больше."
        )
    return True, ""
