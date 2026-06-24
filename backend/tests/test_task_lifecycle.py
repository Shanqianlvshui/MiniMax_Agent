from fastapi.testclient import TestClient

from app.main import create_app


def test_user_can_create_and_read_a_task(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())

    created = client.post("/tasks", json={"goal": "Build a USB CDC demo"}).json()

    assert created["goal"] == "Build a USB CDC demo"
    assert created["status"] == "running"
    assert created["cancel_requested"] is False
    assert created["current_agent"] is None
    assert created["id"]

    fetched = client.get(f"/tasks/{created['id']}").json()

    assert fetched["id"] == created["id"]
    assert fetched["goal"] == created["goal"]
    assert fetched["status"] in {"running", "completed"}


def test_completed_task_cannot_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())
    task = client.post("/tasks", json={"goal": "Run the workflow"}).json()
    with client.stream("GET", f"/tasks/{task['id']}/events") as response:
        assert response.status_code == 200
        list(response.iter_lines())

    response = client.post(f"/tasks/{task['id']}/cancel")

    assert response.status_code == 409

    fetched = client.get(f"/tasks/{task['id']}").json()
    assert fetched["status"] == "completed"
    assert fetched["cancel_requested"] is False


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


def test_task_runs_planner_and_streams_events(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())

    task = client.post("/tasks", json={"goal": "Plan a USB CDC task"}).json()

    with client.stream("GET", f"/tasks/{task['id']}/events") as response:
        lines = [line for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert any("event: task.started" in line for line in lines)
    assert any("event: agent.started" in line for line in lines)
    assert any("event: agent.token" in line for line in lines)
    assert any("agent.token" in line and "planner" in line for line in lines)
    assert any("event: agent.completed" in line for line in lines)
    assert any("event: task.completed" in line for line in lines)

    fetched = client.get(f"/tasks/{task['id']}").json()
    assert fetched["status"] == "completed"
    assert fetched["current_agent"] is None
