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


def should_auto_resume(contact_row: dict, *, now: float, auto_resume_hours: float) -> bool:
    """Пора ли снять паузу самостоятельно (спека §8)."""
    if not contact_row.get("paused"):
        return False
    until = contact_row.get("pause_until")
    if until is not None:
        return now >= float(until)
    if contact_row.get("pause_source") != "human_takeover":
        # /pause без длительности = бессрочно. Явную команду владельца таймер
        # отменять не вправе.
        return False
    # Отсчёт от ПОСЛЕДНЕГО ручного сообщения владельца, а не от начала паузы:
    # иначе диалог, где он активно переписывается второй час, разморозится у
    # него под руками. Явная проверка `is not None` (а не `or`) — иначе
    # last_human_out_ts == 0.0 (эпоха 1970) ошибочно считался бы отсутствующим
    # и отсчёт съезжал бы на paused_at.
    last = contact_row.get("last_human_out_ts")
    if last is None:
        # Тот же класс поля (эпоховый timestamp), тот же риск, что и строкой
        # выше: `or` совпадает с `is None` только пока дефолт == 0.0.
        # paused_at здесь сегодня недостижим как None (Store.mute() пишет его
        # в том же UPDATE, что и pause_source, а легаси-строка с paused_at=NULL
        # обрывается предыдущей веткой) — но явный `is None` не полагается на
        # это и не откроется молча при будущем рефакторинге Store.
        paused_at = contact_row.get("paused_at")
        last = 0.0 if paused_at is None else paused_at
    return (now - float(last)) >= auto_resume_hours * 3600.0
