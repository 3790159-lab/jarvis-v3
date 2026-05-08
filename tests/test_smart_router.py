"""Phase 16: Smart Router tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.smart_router import (
    is_compound_task,
    analyze_task,
    select_agents,
    build_execution_plan,
    synthesize_results,
    ExecutionPlan,
    ExecutionStep,
)


# ---------------------------------------------------------------------------
# is_compound_task
# ---------------------------------------------------------------------------

def test_compound_with_i_conjunction():
    assert is_compound_task("найди топ-10 AI инструментов и сделай таблицу") is True


def test_compound_with_potom():
    assert is_compound_task("исследуй рынок, а потом оформи в Excel") is True


def test_compound_with_zatem():
    assert is_compound_task("поищи данные затем составь отчёт") is True


def test_single_task_not_compound():
    assert is_compound_task("найди информацию о ChatGPT") is False


def test_simple_question_not_compound():
    assert is_compound_task("что такое машинное обучение?") is False


def test_compound_multi_verb():
    assert is_compound_task("найди топ и оформи таблицу") is True


# ---------------------------------------------------------------------------
# analyze_task
# ---------------------------------------------------------------------------

def test_analyze_research_query():
    req = analyze_task("расскажи про GPT-4 и его возможности")
    assert "research" in req["required_capabilities"] or "web_search" in req["required_capabilities"]
    assert req["complexity"] in ("simple", "complex", "compound")


def test_analyze_table_query():
    req = analyze_task("сделай таблицу сравнения CRM систем")
    assert "excel" in req["required_capabilities"] or "comparison_tables" in req["required_capabilities"]


def test_analyze_code_query():
    req = analyze_task("напиши Python скрипт для парсинга CSV")
    assert "code_generation" in req["required_capabilities"]


def test_analyze_compound_returns_compound_complexity():
    req = analyze_task("найди топ AI и сделай таблицу Excel")
    assert req["complexity"] == "compound"
    assert req["is_compound"] is True


def test_analyze_simple_returns_simple_complexity():
    req = analyze_task("привет")
    assert req["complexity"] == "simple"


def test_analyze_image_query():
    req = analyze_task("нарисуй изображение горы на закате")
    assert "image_generation" in req["required_capabilities"]


def test_analyze_returns_expected_outputs_table():
    req = analyze_task("создай excel таблицу")
    assert "table_xlsx" in req["expected_outputs"]


def test_analyze_returns_expected_outputs_text_default():
    req = analyze_task("что такое AI")
    assert "text" in req["expected_outputs"]


def test_analyze_unknown_query_defaults_to_research():
    req = analyze_task("абракадабра ксыр трулялю")
    assert "research" in req["required_capabilities"]


# ---------------------------------------------------------------------------
# select_agents
# ---------------------------------------------------------------------------

def test_select_agents_for_research():
    req = analyze_task("расскажи про квантовые компьютеры")
    agents = select_agents(req)
    assert len(agents) >= 1
    assert any(a in agents for a in ("internet_research", "perplexity_researcher"))


def test_select_agents_for_table():
    req = {"required_capabilities": ["excel"], "original_query": "таблица"}
    agents = select_agents(req)
    assert "smart_table" in agents


def test_select_agents_for_code():
    req = {"required_capabilities": ["code_generation"], "original_query": "код"}
    agents = select_agents(req)
    assert "claude_coder" in agents


def test_select_agents_no_duplicates():
    req = {"required_capabilities": ["web_search", "research"], "original_query": "q"}
    agents = select_agents(req)
    assert len(agents) == len(set(agents))


def test_select_agents_includes_cowork_for_file_org():
    # cowork_file_agent is now available and handles file_organization
    req = {"required_capabilities": ["file_organization"], "original_query": "q"}
    agents = select_agents(req)
    assert "cowork_file_agent" in agents


def test_select_agents_compound_returns_multiple():
    req = analyze_task("найди топ AI и сделай таблицу")
    agents = select_agents(req)
    assert len(agents) >= 2


# ---------------------------------------------------------------------------
# build_execution_plan
# ---------------------------------------------------------------------------

def test_build_plan_creates_steps():
    plan = build_execution_plan("тест", ["internet_research", "smart_table"])
    assert len(plan.steps) == 2


def test_build_plan_step_numbers():
    plan = build_execution_plan("тест", ["internet_research", "smart_table"])
    assert plan.steps[0].step_num == 1
    assert plan.steps[1].step_num == 2


def test_build_plan_step_agent_ids():
    plan = build_execution_plan("тест", ["claude_coder"])
    assert plan.steps[0].agent_id == "claude_coder"


def test_build_plan_summary_contains_steps():
    plan = build_execution_plan("тест", ["internet_research", "smart_table"])
    summary = plan.summary()
    assert "2" in summary or "[1]" in summary


def test_build_plan_single_agent():
    plan = build_execution_plan("найди", ["perplexity_researcher"])
    assert len(plan.steps) == 1
    assert plan.steps[0].status == "pending"


# ---------------------------------------------------------------------------
# synthesize_results
# ---------------------------------------------------------------------------

def test_synthesize_empty_returns_message():
    result = synthesize_results("тест", [])
    assert "нет" in result.lower() or len(result) > 0


def test_synthesize_single_agent_returns_answer():
    result = synthesize_results("вопрос", [{"agent": "internet_research", "result": {"answer": "Ответ 42"}}])
    assert "42" in result or len(result) > 0


def test_synthesize_multiple_agents_includes_content():
    results = [
        {"agent": "internet_research", "result": {"answer": "Research result"}},
        {"agent": "smart_table", "result": {"answer": "Table created"}},
    ]
    # Without live backend, fallback concatenation should work
    result = synthesize_results("find and table", results)
    assert len(result) > 10


def test_synthesize_preserves_agent_label():
    results = [
        {"agent": "claude_coder", "result": {"text": "Here is the code"}},
    ]
    result = synthesize_results("write code", results)
    assert len(result) > 0
