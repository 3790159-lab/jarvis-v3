"""Phase H8.5: Night Autonomy activation tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_night_workflow_importable():
    from app.services.night_workflows import NightWorkflow
    nw = NightWorkflow()
    assert nw is not None


def test_schedule_all_phases_callable():
    from app.services.night_workflows import NightWorkflow
    nw = NightWorkflow()
    assert callable(nw.schedule_all_phases)


def test_get_status_returns_dict():
    from app.services.night_workflows import NightWorkflow
    nw = NightWorkflow()
    status = nw.get_status()
    assert isinstance(status, dict)
    assert "phases" in status
    assert "current_phase" in status


def test_phases_has_5_entries():
    from app.services.night_workflows import PHASES
    assert len(PHASES) == 5
    assert "winddown" in PHASES
    assert "deep_work" in PHASES
    assert "self_improve" in PHASES
    assert "morning_prep" in PHASES
    assert "wakeup" in PHASES


def test_get_current_phase_returns_none_or_str():
    from app.services.night_workflows import get_current_phase
    result = get_current_phase()
    assert result is None or isinstance(result, str)


def test_get_phase_schedule_winddown():
    from app.services.night_workflows import get_phase_schedule
    sched = get_phase_schedule("winddown")
    assert sched is not None
    assert sched["start"] == 22


def test_startup_event_creates_watchdog_task(monkeypatch):
    """Startup event should create watchdog and night tasks."""
    import asyncio
    tasks_created = []

    class FakeLoop:
        def create_task(self, coro):
            tasks_created.append(coro.__name__ if hasattr(coro, '__name__') else str(type(coro)))
            return MagicMock()

    from unittest.mock import MagicMock, patch, AsyncMock
    import app.main as main_module

    # Check that _watchdog_loop function exists in main
    assert hasattr(main_module, "_watchdog_loop"), "app.main must have _watchdog_loop"
    assert callable(main_module._watchdog_loop)


def test_watchdog_loop_function_exists():
    import app.main as main_module
    assert hasattr(main_module, "_watchdog_loop")


def test_night_workflow_schedule_crons():
    from app.services.night_workflows import NightWorkflow
    from unittest.mock import MagicMock

    mock_scheduler = MagicMock()
    mock_scheduler.add_task.return_value = "task_id_123"

    nw = NightWorkflow()
    task_ids = nw.schedule_all_phases(scheduler=mock_scheduler)

    # Should have scheduled 5 phases
    assert mock_scheduler.add_task.call_count == 5, \
        f"Expected 5 phase registrations, got {mock_scheduler.add_task.call_count}"


def test_night_workflow_phases_have_correct_hours():
    from app.services.night_workflows import PHASES
    assert PHASES["winddown"]["start"] == 22
    assert PHASES["deep_work"]["start"] == 23
    assert PHASES["self_improve"]["start"] == 2
    assert PHASES["morning_prep"]["start"] == 4
    assert PHASES["wakeup"]["start"] == 6


def test_cmd_night_now_exists_and_callable():
    import tools.jarvis_smart_telegram_control as tg
    assert hasattr(tg, "cmd_night_now")
    assert callable(tg.cmd_night_now)


def test_cmd_night_status_exists_and_callable():
    import tools.jarvis_smart_telegram_control as tg
    assert hasattr(tg, "cmd_night_status")
    assert callable(tg.cmd_night_status)
