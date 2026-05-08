from app.router import route_task


def test_router_routes_executor_tasks():
    assert route_task("write_file") == "executor_agent"
    assert route_task("echo") == "executor_agent"


def test_router_routes_shell_tasks():
    assert route_task("run_shell_command") == "shell_agent"


def test_router_routes_plan_tasks():
    assert route_task("plan") == "planner_agent"


def test_router_routes_critic_tasks():
    assert route_task("critic_check") == "critic_agent"
