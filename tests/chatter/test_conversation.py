from __future__ import annotations
import pytest
from chatter.core.conversation import next_state, STATES

def test_states_set():
    assert STATES == {"new", "qualifying", "hot", "escalated", "closed", "dead"}

@pytest.mark.parametrize("start,signal,expected", [
    ("new", "engaged", "qualifying"),
    ("qualifying", "interested", "hot"),
    ("qualifying", "unknown_info", "escalated"),
    ("hot", "needs_human", "escalated"),
    ("hot", "bought", "closed"),
    ("qualifying", "ghosted", "dead"),
])
def test_transitions(start, signal, expected):
    assert next_state(start, signal) == expected

# --- дыра воронки: сигналы прогресса из `new` уходили в никуда ---------------
# Факт до фикса: из `new` работал ровно один сигнал из шести (`engaged`), плюс
# `ghosted`. `interested`/`needs_human`/`unknown_info` возвращали `new` — лид,
# заявивший о себе первым же сообщением, застревал навсегда. Рёбра-близнецы из
# `qualifying` существовали, то есть это пропуск, а не решение.

@pytest.mark.parametrize("signal,expected", [
    ("interested", "hot"),          # горячий лид с первого сообщения
    ("needs_human", "escalated"),   # просит человека, ещё не квалифицирован
    ("unknown_info", "escalated"),  # спросил то, чего бот не знает
])
def test_progress_signals_from_new_are_not_dead_ends(signal, expected):
    assert next_state("new", signal) == expected


def test_new_mirrors_qualifying_for_escalating_signals():
    """Рёбра из `new` заведены по образцу `qualifying`, а не выдуманы: у
    одинакового сигнала одинаковый исход. Если кто-то поменяет одну сторону,
    тест назовёт расхождение."""
    for signal in ("needs_human", "unknown_info", "ghosted"):
        assert next_state("new", signal) == next_state("qualifying", signal)


# --- сторожа НАМЕРЕННЫХ глухарей (их чинить нельзя) --------------------------

@pytest.mark.parametrize("state,signal", [
    ("escalated", "engaged"), ("escalated", "interested"),
    ("escalated", "needs_human"), ("escalated", "unknown_info"),
])
def test_escalated_is_not_pulled_back_by_model_signals(state, signal):
    """`escalated` — состояние, которым владеет ЧЕЛОВЕК. Сигнал модели не имеет
    права вытащить лида из рук оператора обратно в воронку. Это намеренный
    глухарь, а не пропущенное ребро."""
    assert next_state(state, signal) == "escalated"


@pytest.mark.parametrize("state", ["new", "qualifying"])
def test_model_bought_does_not_close_funnel_early(state):
    """`bought` от модели — догадка; закрывает воронку только ФАКТ владельца
    (advance_funnel(bought=True), тап карточки). Ребра new/qualifying→closed
    нет намеренно — см. комментарий в advance_funnel."""
    assert next_state(state, "bought") == state


def test_unknown_signal_keeps_state():
    assert next_state("hot", "smalltalk") == "hot"

def test_terminal_states_are_sticky():
    assert next_state("closed", "engaged") == "closed"
    assert next_state("dead", "interested") == "dead"

def test_invalid_state_raises():
    with pytest.raises(ValueError):
        next_state("bogus", "engaged")
