from fastapi.testclient import TestClient

from app.main import create_app


def test_user_can_create_and_read_a_task(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = TestClient(create_app())

    created = client.post("/tasks", json={"goal": "Build a USB CDC demo"}).json()

    assert created["goal"] == "Build a USB CDC demo"
    assert created["status"] == "running"
    assert created["cancel_requested"] is False
    assert created["current_agent"] is None
    assert created["id"]

    fetched = client.get(f"/tasks/{created['id']}").json()

    assert fetched == created


def test_user_can_request_task_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = TestClient(create_app())
    task = client.post("/tasks", json={"goal": "Run the workflow"}).json()

    cancelled = client.post(f"/tasks/{task['id']}/cancel").json()

    assert cancelled["id"] == task["id"]
    assert cancelled["status"] == "cancel_requested"
    assert cancelled["cancel_requested"] is True

    fetched = client.get(f"/tasks/{task['id']}").json()
    assert fetched["status"] == "cancel_requested"
    assert fetched["cancel_requested"] is True


def test_empty_goal_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = TestClient(create_app())

    response = client.post("/tasks", json={"goal": "  "})

    assert response.status_code == 422


def test_unknown_task_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = TestClient(create_app())

    response = client.get("/tasks/not-a-real-task")

    assert response.status_code == 404
