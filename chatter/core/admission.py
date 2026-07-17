"""Гейт допуска (арка 3C): кому Аня отвечает.

ПЕРЕВОРОТ гейта (требование владельца): на боевом аккаунте лиды — это
НЕЗНАКОМЦЫ, поэтому Аня отвечает СТРАНГЕРАМ, а знакомым (Telethon `User.contact`)
— НИКОГДА (вместо ответа — уведомление владельцу в пульт). Плюс denylist.

Чистая функция — ноль Telethon, ноль сети, ноль состояния. Раннер достаёт
`is_contact` из entity и передаёт булевым аргументом.

Предусловие (спека): переворот безопасен ТОЛЬКО на аккаунте, ВЫДЕЛЕННОМ под
воронку. Поэтому по умолчанию `funnel_gate=False` — старое поведение (отвечаем
только allowlist), а переворот включает оператор явно, тем самым подтверждая,
что аккаунт выделенный.
"""
from __future__ import annotations


def admission_decision(
    *, sender_id: int, is_contact: bool,
    allowlist: frozenset[int], denylist: frozenset[int], funnel_gate: bool,
) -> str:
    """'answer' | 'ignore' | 'block_denylist' | 'notify_owner'.

    funnel_gate=False → старое поведение: отвечаем только allowlist, остальных
    тихо игнорируем (ничего из арок 2/3A/3B не ломается).

    funnel_gate=True → переворот, приоритет: denylist > allowlist > contact >
    stranger. allowlist остаётся ПЕРЕОПРЕДЕЛЕНИЕМ «всегда отвечать» (тест/VIP-лид,
    который оказался контактом), denylist его перебивает (явный блок сильнее)."""
    if not funnel_gate:
        return "answer" if sender_id in allowlist else "ignore"
    if sender_id in denylist:
        return "block_denylist"
    if sender_id in allowlist:
        return "answer"
    if is_contact:
        return "notify_owner"
    return "answer"
