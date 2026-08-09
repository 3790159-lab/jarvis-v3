from __future__ import annotations

STATES = {"new", "qualifying", "hot", "escalated", "closed", "dead"}
_TERMINAL = {"closed", "dead"}

# Карта переходов. Отсутствие ребра = сигнал НЕ меняет состояние; для
# нетерминального состояния это либо намеренный no-op, либо дыра. Разница:
#
#   НАМЕРЕННЫЕ no-op (менять нельзя):
#     escalated + engaged/interested/needs_human/unknown_info — 'escalated'
#       принадлежит ЧЕЛОВЕКУ; сигнал модели не вправе вытащить лида обратно.
#     new/qualifying + bought — 'bought' от модели догадка, а не факт. Воронку
#       по оплате закрывает только владелец: advance_funnel(bought=True).
#     hot + engaged/interested, qualifying + engaged — движение назад.
#
#   ДЫРА (закрыта 2026-08-09): из 'new' работал ровно один прогрессный сигнал
#     из пяти. Лид, заявивший о себе ПЕРВЫМ сообщением ("хочу заказать, почём?"
#     → interested), застревал в 'new' навсегда: следующего 'engaged' могло уже
#     не быть. Рёбра-близнецы из 'qualifying' существовали, значит это пропуск,
#     а не решение. Три ребра ниже помечены (дыра-2026-08-09) и повторяют
#     исходы 'qualifying' один-в-один.
_TRANSITIONS: dict[str, dict[str, str]] = {
    "new": {"engaged": "qualifying", "ghosted": "dead",
            "interested": "hot",             # дыра-2026-08-09
            "needs_human": "escalated",      # дыра-2026-08-09
            "unknown_info": "escalated"},    # дыра-2026-08-09
    "qualifying": {"interested": "hot", "unknown_info": "escalated",
                   "needs_human": "escalated", "ghosted": "dead"},
    "hot": {"needs_human": "escalated", "unknown_info": "escalated",
            "bought": "closed", "ghosted": "dead"},
    "escalated": {"bought": "closed", "ghosted": "dead"},
}

def next_state(current: str, signal: str) -> str:
    if current not in STATES:
        raise ValueError(f"unknown state: {current}")
    if current in _TERMINAL:
        return current
    return _TRANSITIONS.get(current, {}).get(signal, current)
