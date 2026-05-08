"""Phase H9.4: Night Autonomy scheduling via correct scheduler API."""
from __future__ import annotations

import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# cmd_night_status — correct key lookup
# ---------------------------------------------------------------------------

def test_night_status_finds_tasks_by_action(monkeypatch, tmp_path):
    """cmd_night_status must filter by 'action' key, not 'id'."""
    import tools.jarvis_smart_telegram_control as mod

    tasks_json = tmp_path / "scheduled_tasks.json"
    tasks_json.write_text(json.dumps([
        {"task_id": "abc123", "action": "night_winddown", "cron": "0 22 * * *", "active": True},
        {"task_id": "def456", "action": "night_deep_work", "cron": "0 23 * * *", "active": True},
    ]), encoding="utf-8")

    sent = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: sent.append(text))

    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value=tasks_json.read_text(encoding="utf-8")):
        mod.cmd_night_status("test_chat")

    assert sent, "Nothing sent"
    assert "НЕ активирована" not in sent[0], f"Should find tasks, got: {sent[0]}"
    assert "night_winddown" in sent[0] or "night_deep_work" in sent[0]


def test_night_status_not_activated_when_no_night_tasks(monkeypatch, tmp_path):
    """cmd_night_status shows НЕ активирована when no night_* action tasks."""
    import tools.jarvis_smart_telegram_control as mod

    tasks_json = tmp_path / "scheduled_tasks.json"
    tasks_json.write_text(json.dumps([
        {"task_id": "xyz", "action": "remind", "cron": "0 9 * * *", "active": True},
    ]), encoding="utf-8")

    sent = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: sent.append(text))

    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value=tasks_json.read_text(encoding="utf-8")):
        mod.cmd_night_status("test_chat")

    assert any("НЕ активирована" in m for m in sent)


def test_night_status_no_file(monkeypatch):
    """cmd_night_status gracefully handles missing file."""
    import tools.jarvis_smart_telegram_control as mod

    sent = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: sent.append(text))

    with patch.object(Path, "exists", return_value=False):
        mod.cmd_night_status("test_chat")

    assert any("не активирована" in m.lower() for m in sent)


# ---------------------------------------------------------------------------
# schedule_all_phases — writes to scheduled_tasks.json
# ---------------------------------------------------------------------------

def test_schedule_all_phases_writes_json(tmp_path):
    """schedule_all_phases should persist tasks to scheduled_tasks.json."""
    from app.services.night_workflows import NightWorkflow
    from app.services import scheduler as sched_mod

    tasks_file = tmp_path / "scheduled_tasks.json"
    original_path = sched_mod.STATE_PATH
    sched_mod.STATE_PATH = tasks_file

    try:
        nw = NightWorkflow()
        task_ids = nw.schedule_all_phases()
        assert len(task_ids) == 5, f"Expected 5 task IDs, got {len(task_ids)}"
        assert tasks_file.exists(), "scheduled_tasks.json not created"
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        actions = [t.get("action") for t in tasks]
        for expected in ["night_winddown", "night_deep_work", "night_self_improve",
                         "night_morning_prep", "night_wakeup"]:
            assert expected in actions, f"{expected} not in {actions}"
    finally:
        sched_mod.STATE_PATH = original_path


def test_schedule_all_phases_preserves_existing_tasks(tmp_path):
    """schedule_all_phases must not overwrite existing tasks."""
    from app.services.night_workflows import NightWorkflow
    from app.services import scheduler as sched_mod

    tasks_file = tmp_path / "scheduled_tasks.json"
    existing = [{"task_id": "remind001", "action": "remind", "active": True,
                 "schedule_type": "once", "run_at": "2026-06-01T09:00:00+00:00",
                 "params": {"text": "test"}, "chat_id": "123",
                 "created_at": "2026-05-01T00:00:00+00:00"}]
    tasks_file.write_text(json.dumps(existing), encoding="utf-8")
    original_path = sched_mod.STATE_PATH
    sched_mod.STATE_PATH = tasks_file

    try:
        nw = NightWorkflow()
        nw.schedule_all_phases()
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        actions = [t.get("action") for t in tasks]
        assert "remind" in actions, "Existing remind task was overwritten!"
    finally:
        sched_mod.STATE_PATH = original_path


def test_schedule_all_phases_returns_5_ids():
    from app.services.night_workflows import NightWorkflow

    mock_scheduler = MagicMock()
    mock_scheduler.add_task.side_effect = ["id1", "id2", "id3", "id4", "id5"]

    nw = NightWorkflow()
    ids = nw.schedule_all_phases(scheduler=mock_scheduler)
    assert len(ids) == 5
    assert mock_scheduler.add_task.call_count == 5


def test_schedule_all_phases_uses_correct_crons():
    from app.services.night_workflows import NightWorkflow

    calls = []
    mock_scheduler = MagicMock()
    mock_scheduler.add_task.side_effect = lambda **kw: calls.append(kw) or "tid"

    nw = NightWorkflow()
    nw.schedule_all_phases(scheduler=mock_scheduler)

    crons = {c["action"]: c["cron"] for c in calls}
    assert crons.get("night_winddown") == "0 22 * * *"
    assert crons.get("night_wakeup") == "0 6 * * *"
