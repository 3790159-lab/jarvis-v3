from __future__ import annotations

"""Phase 14: Telegram multi-step task executor.

Executes TaskPlan steps sequentially, reporting progress via a callback.
The send_progress callable receives (text: str) for each status update.

Usage:
    from app.services.telegram_task_executor import execute_plan
    execute_plan(plan, run_step_fn, send_progress=print)
"""

from typing import Callable, Optional

from app.services.task_planner import TaskPlan, TaskStep


def execute_plan(
    plan: TaskPlan,
    run_step: Callable[[TaskStep], str],
    send_progress: Optional[Callable[[str], None]] = None,
) -> TaskPlan:
    """Execute all steps of plan in order.

    run_step(step) -> result text
    send_progress(text) called before and after each step
    """
    _notify = send_progress or (lambda _: None)
    _notify(plan.summary())

    for step in plan.steps:
        plan.current_step = step.step_number
        _notify(f"▶️ Шаг {step.step_number}/{len(plan.steps)}: {step.description}...")
        try:
            result = run_step(step)
            step.result = result
            step.done = True
        except Exception as exc:
            step.error = str(exc)
            step.done = True
            _notify(f"⚠️ Шаг {step.step_number} завершился с ошибкой: {exc}")

    plan.completed = True
    return plan
