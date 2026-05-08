from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service_status"] == "ok"


def test_register_agent():
    response = client.post(
        "/agents/register",
        json={
            "name": "planner_agent",
            "role": "Planner",
            "capabilities": ["plan", "route"],
            "enabled": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "planner_agent"


def test_create_task():
    response = client.post(
        "/tasks",
        json={
            "task_type": "echo",
            "payload": {"message": "hello"},
            "assigned_agent": "planner_agent",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "echo"
    assert data["status"] == "queued"


def test_list_tasks():
    response = client.get("/tasks")
    assert response.status_code == 200
    assert "tasks" in response.json()
