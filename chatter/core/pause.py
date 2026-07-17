"""Чистые решения о глушении диалога (арка 3A).

Ноль Telethon, ноль SQLite, ноль сети: раннер приносит сюда строку контакта и
состояние рубильника, получает булев ответ. Поэтому вся логика перехвата
тестируется без аккаунта."""
from __future__ import annotations


def is_muted(contact_row: dict | None, *, kill_switch: bool, now: float) -> bool:
    """Молчит ли Аня в этом диалоге прямо сейчас."""
    if kill_switch:
        return True
    if not contact_row or not contact_row.get("paused"):
        return False
    until = contact_row.get("pause_until")
    # Истёкший дедлайн = не заглушено, даже если периодическая задача ещё не
    # добежала (или умерла). Защита в глубину: /pause 1h не имеет права
    # превратиться в вечную тишину из-за мёртвого таймера.
    if until is not None and now >= float(until):
        return False
    return True


def is_attributed(contact_row: dict | None) -> bool:
    """False = пауза стоит, а причины нет. Это баг-класс (спека §4), а не
    мелочь: самозаглушка тиха и вечна, и без атрибуции на вопрос «почему Аня
    молчит» нет ответа.

    contact_row=None (контакт неизвестен раннеру) трактуется как «не на
    паузе» — приравниваем к контракту is_muted(None) = False, чтобы вызывающая
    сторона не обязана была отдельно проверять None перед обоими вызовами."""
    if not contact_row or not contact_row.get("paused"):
        return True
    return bool(contact_row.get("pause_source"))
