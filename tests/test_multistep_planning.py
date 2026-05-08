from __future__ import annotations

"""Phase 14: Multi-step task planning tests.

Covers:
1. is_compound_task — detects compound queries
2. decompose_task — heuristic fallback decomposition
3. TaskPlan.summary() / results_summary()
4. execute_plan — runs steps, calls progress
5. compound_task intent routing via classify_message
6. _handle_compound_task plan_only mode via /plan command
7. /tasks command with no plan
8. _parse_steps_json — JSON parsing
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.task_planner import (
    is_compound_task,
    decompose_task,
    TaskPlan,
    TaskStep,
    _heuristic_decompose,
    _parse_steps_json,
)
from app.services.telegram_task_executor import execute_plan


# ---------------------------------------------------------------------------
# is_compound_task
# ---------------------------------------------------------------------------

def test_compound_detected_and_then():
    assert is_compound_task("найди информацию об OpenAI и затем создай таблицу") is True


def test_compound_detected_potom():
    assert is_compound_task("исследуй рынок AI а потом напиши отчёт") is True


def test_compound_detected_also():
    assert is_compound_task("найди топ инструменты также найди цены") is True


def test_compound_not_simple_query():
    assert is_compound_task("что такое AI") is False


def test_compound_not_short():
    assert is_compound_task("привет") is False


def test_compound_not_single_step():
    assert is_compound_task("создай таблицу сравнения топ AI сервисов") is False


def test_compound_detected_vo_pervykh():
    assert is_compound_task("во-первых найди данные, во-вторых создай таблицу") is True


# ---------------------------------------------------------------------------
# _heuristic_decompose
# ---------------------------------------------------------------------------

def test_heuristic_splits_i_zatem():
    steps = _heuristic_decompose("найди новости и затем подведи итоги")
    assert len(steps) == 2
    assert "найди новости" in steps[0]["query"]


def test_heuristic_splits_comma_i():
    steps = _heuristic_decompose("исследуй OpenAI, и найди цены, и сравни с Anthropic")
    assert len(steps) >= 2


def test_heuristic_single_part_fallback():
    steps = _heuristic_decompose("простой запрос")
    assert len(steps) == 1
    assert steps[0]["query"] == "простой запрос"


# ---------------------------------------------------------------------------
# _parse_steps_json
# ---------------------------------------------------------------------------

def test_parse_steps_json_valid():
    text = '{"steps": [{"intent": "research", "query": "AI news", "description": "Шаг 1"}]}'
    steps = _parse_steps_json(text)
    assert steps is not None
    assert steps[0]["intent"] == "research"


def test_parse_steps_json_with_markdown_fence():
    text = '```json\n{"steps": [{"intent": "brain", "query": "analyse", "description": "Analyse"}]}\n```'
    steps = _parse_steps_json(text)
    assert steps is not None
    assert steps[0]["intent"] == "brain"


def test_parse_steps_json_invalid_intent_normalised():
    text = '{"steps": [{"intent": "unknown_intent", "query": "q", "description": "d"}]}'
    steps = _parse_steps_json(text)
    assert steps[0]["intent"] == "chat"


def test_parse_steps_json_empty_returns_none():
    assert _parse_steps_json("{}") is None
    assert _parse_steps_json("not json") is None


# ---------------------------------------------------------------------------
# decompose_task (heuristic path — no LLM keys needed)
# ---------------------------------------------------------------------------

def test_decompose_returns_task_plan(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    plan = decompose_task("исследуй OpenAI и затем создай таблицу")
    assert isinstance(plan, TaskPlan)
    assert len(plan.steps) >= 2


def test_decompose_step_numbers_sequential(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    plan = decompose_task("найди данные а потом напиши отчёт")
    for i, step in enumerate(plan.steps):
        assert step.step_number == i + 1


# ---------------------------------------------------------------------------
# TaskPlan.summary / results_summary
# ---------------------------------------------------------------------------

def _make_plan():
    return TaskPlan(
        original_query="test query",
        steps=[
            TaskStep(1, "research", "find AI", "Поиск AI"),
            TaskStep(2, "table", "make table", "Создать таблицу"),
        ],
    )


def test_plan_summary_contains_query():
    plan = _make_plan()
    summary = plan.summary()
    assert "test query" in summary


def test_plan_summary_shows_all_steps():
    plan = _make_plan()
    summary = plan.summary()
    assert "Поиск AI" in summary
    assert "Создать таблицу" in summary


def test_plan_summary_marks_done_steps():
    plan = _make_plan()
    plan.steps[0].done = True
    summary = plan.summary()
    assert "✅" in summary


def test_results_summary_empty():
    plan = _make_plan()
    assert "Нет результатов" in plan.results_summary()


def test_results_summary_includes_step_result():
    plan = _make_plan()
    plan.steps[0].done = True
    plan.steps[0].result = "Found: GPT-4, Claude, Gemini"
    summary = plan.results_summary()
    assert "Found: GPT-4" in summary


# ---------------------------------------------------------------------------
# execute_plan
# ---------------------------------------------------------------------------

def test_execute_plan_calls_run_step():
    plan = _make_plan()
    called = []

    def run_step(step):
        called.append(step.step_number)
        return f"result {step.step_number}"

    execute_plan(plan, run_step=run_step)
    assert called == [1, 2]
    assert plan.completed is True


def test_execute_plan_sends_progress():
    plan = _make_plan()
    messages = []

    def run_step(step):
        return "ok"

    execute_plan(plan, run_step=run_step, send_progress=messages.append)
    assert len(messages) >= 2  # initial summary + per-step
    assert any("Шаг" in m for m in messages)


def test_execute_plan_handles_step_error():
    plan = _make_plan()

    def run_step(step):
        if step.step_number == 1:
            raise RuntimeError("network error")
        return "ok"

    execute_plan(plan, run_step=run_step)
    assert plan.steps[0].error == "network error"
    assert plan.steps[0].done is True
    assert plan.steps[1].done is True
    assert plan.completed is True


def test_execute_plan_marks_step_done():
    plan = _make_plan()

    def run_step(step):
        return "done"

    execute_plan(plan, run_step=run_step)
    assert all(s.done for s in plan.steps)
    assert all(s.result == "done" for s in plan.steps)


# ---------------------------------------------------------------------------
# classify_message — compound_task intent
# ---------------------------------------------------------------------------

def test_classify_compound_task_intent():
    from tools.jarvis_smart_telegram_control import classify_message
    result = classify_message("найди информацию об AI и затем создай таблицу", {})
    assert result["intent"] == "compound_task"


def test_classify_simple_not_compound():
    from tools.jarvis_smart_telegram_control import classify_message
    result = classify_message("создай таблицу топ AI", {})
    assert result["intent"] != "compound_task"


# ---------------------------------------------------------------------------
# /plan command — plan_only mode
# ---------------------------------------------------------------------------

def test_plan_command_without_query_sends_help(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("123", "/plan", "", state)
    assert len(sent) == 1
    assert "/plan" in sent[0]


def test_plan_command_with_query_sends_plan(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    # Also stub _handle_compound_task to avoid actual LLM calls
    monkeypatch.setattr(mod, "_handle_compound_task", lambda cid, q, s, **kw: sent.append(f"PLAN:{q}"))
    state = mod.default_state()
    mod.handle_command("123", "/plan", "найди данные и затем создай таблицу", state)
    assert any("PLAN:" in m or "план" in m.lower() for m in sent)


# ---------------------------------------------------------------------------
# /tasks command — no plan
# ---------------------------------------------------------------------------

def test_tasks_command_no_plan_sends_message(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("123", "/tasks", "", state)
    assert len(sent) == 1
    assert "задач" in sent[0].lower() or "/plan" in sent[0]
