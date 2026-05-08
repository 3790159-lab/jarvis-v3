from app.planner import plan_task


def test_plan_task_creates_write_file_and_critic():
    tasks = plan_task({"objective": "Create a file with execution result", "depth": 0})

    assert len(tasks) == 2
    assert tasks[0]["task_type"] == "write_file"
    assert tasks[0]["assigned_agent"] == "executor_agent"
    assert tasks[1]["task_type"] == "critic_check"
    assert tasks[1]["assigned_agent"] == "critic_agent"
