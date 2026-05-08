from app.mission_utils import get_mission_task_summary


def test_mission_summary_function_exists():
    summary = get_mission_task_summary("missing_mission_id")
    assert summary["mission_id"] == "missing_mission_id"
    assert summary["task_count"] == 0
