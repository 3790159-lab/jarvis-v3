"""Brand-safety: запрещённые термины на клиента (валюта/банк/платёжка).

Для украинского бизнеса: рубли, российские банки и платёжные системы. Ляпнуть
такое покупателю клиента = уничтожить репутацию клиента, а виноват поставщик.
Это НЕ косметика — это сеть безопасности того же класса, что выдуманная цена.

Чистая функция, ноль сети. Denylist задаёт клиент в settings (`forbidden_terms`),
не хардкод. Живёт ОТДЕЛЬНО от `guardrails.py` (core-файл, не трогаем): подключается
через `escalation.deterministic_escalation`.
"""
from __future__ import annotations


def forbidden_mention(text: str, forbidden_terms) -> str | None:
    """Первый запрещённый термин, упомянутый в тексте (casefold-подстрока), иначе
    None. Пустой denylist → None (клиент не задал — слой выключен)."""
    low = (text or "").casefold()
    for term in forbidden_terms:
        t = (term or "").casefold()
        if t and t in low:
            return term
    return None
