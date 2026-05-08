"""Phase H8.3: Anti-hallucination guard in system prompts."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _get_quick_answer_system_prompt() -> str:
    """Extract the system prompt string from quick_answer.py source."""
    import inspect
    from app.services import quick_answer
    src = inspect.getsource(quick_answer.quick_answer)
    return src


def test_antihallucination_no_fake_metrics():
    src = _get_quick_answer_system_prompt()
    assert "NEVER invent metrics" in src or "ANTI-HALLUCINATION" in src, \
        "System prompt must contain anti-hallucination rule about fake metrics"


def test_antihallucination_no_fake_services():
    src = _get_quick_answer_system_prompt()
    assert "PostgreSQL" in src or "don't exist" in src or "does NOT have" in src, \
        "System prompt must warn about non-existent services"


def test_antihallucination_no_fake_action():
    src = _get_quick_answer_system_prompt()
    assert "pretend" in src or "executed an action" in src or "NEVER pretend" in src, \
        "System prompt must prohibit fake action reports"


def test_antihallucination_honest_no_data():
    src = _get_quick_answer_system_prompt()
    assert "нет данных" in src or "no data" in src or "no real data" in src or "нет данных" in src, \
        "System prompt must instruct to say 'no data' when no data available"


def test_antihallucination_rules_in_system_prompt():
    src = _get_quick_answer_system_prompt()
    assert "ANTI-HALLUCINATION" in src or "NEVER invent" in src, \
        "Anti-hallucination section must be present in system prompt"


def test_quick_answer_module_importable():
    from app.services.quick_answer import quick_answer, is_simple_question
    assert callable(quick_answer)
    assert callable(is_simple_question)


def test_is_simple_question_not_status_questions():
    from app.services.quick_answer import is_simple_question
    # "ты работаешь" should not be treated as a simple factual question
    assert not is_simple_question("ты работаешь?")


def test_system_prompt_contains_capabilities_list():
    src = _get_quick_answer_system_prompt()
    # Must still have capabilities
    assert "Generate images" in src or "Obsidian" in src, \
        "System prompt must still contain capabilities list"
