"""Агент без транспорта обязан КРАСНЕТЬ, а не отчитываться «готово».

Найдено 06.09.2026 живым прогоном smart_router: агент, у которого в каталоге
нет `endpoint`, получает от `_call_agent` строку-заглушку с `_stub: True`.
Заглушка не ставит `_error`, поэтому `execute_plan` писал шагу
`status="done"`, поднимал `any_ok` и рапортовал «✅ готово (0.0s)».

Замер до фикса, запрос «Что такое prompt caching»:

    ✅ [1/1] Perplexity sonar-pro: готово (0.0s)
    ОТВЕТ: [perplexity_researcher] не имеет HTTP endpoint — требует прямого вызова
    _stub=True  _error=None  СТАТУС ШАГА: done

Это зелёное на месте, где работы не было. Сторожа ниже исполняют БОЕВУЮ ветку:
берут настоящий agent_id из каталога и настоящий `_call_agent`, без подмен, —
подмена обошла бы именно тот код, ради которого сторож написан.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.smart_router import (  # noqa: E402
    MeshExecutionError,
    _call_agent,
    build_execution_plan,
    execute_plan,
    step_failed,
)

# Агент без HTTP-эндпоинта и без особой ветки в `_call_agent`: обращается в
# файловую систему, сети не касается — поэтому годится как боевой образец
# заглушки в тесте.
STUB_AGENT = "obsidian_writer"


def test_call_agent_still_returns_a_stub_for_transportless_agent():
    """Предпосылка сторожей. Если она отвалится, остальные тесты бессмысленны."""
    result = _call_agent(STUB_AGENT, "проверка транспорта")
    assert result.get("_stub") is True
    assert result.get("_error") is None


def test_step_failed_counts_stub_as_failure():
    assert step_failed({"_stub": True, "answer": "нет транспорта"}) is True


def test_step_failed_counts_error_as_failure():
    assert step_failed({"_error": "HTTP Error 401: Unauthorized"}) is True


def test_step_failed_passes_a_real_answer():
    assert step_failed({"ok": True, "answer": "$3 за 1 млн входных токенов"}) is False


def test_stub_step_is_marked_error_not_done():
    plan = build_execution_plan("проверка транспорта", [STUB_AGENT])
    with pytest.raises(MeshExecutionError):
        execute_plan(plan)
    assert [s.status for s in plan.steps] == ["error"]


def test_plan_of_only_stubs_does_not_report_success():
    """`any_ok` не должен подниматься заглушкой: план обязан упасть громко."""
    plan = build_execution_plan("проверка транспорта", [STUB_AGENT])
    with pytest.raises(MeshExecutionError) as exc:
        execute_plan(plan)
    assert plan.status == "error"
    assert plan.final_result is None
    assert STUB_AGENT in str(exc.value) or "транспорт" in str(exc.value).lower()


def test_stub_never_reaches_synthesis():
    """Синтез не должен получать заглушку как содержательный результат."""
    plan = build_execution_plan("проверка транспорта", [STUB_AGENT])
    with pytest.raises(MeshExecutionError):
        execute_plan(plan)
    assert not any(
        (s.result or {}).get("_stub") and s.status == "done" for s in plan.steps
    )
