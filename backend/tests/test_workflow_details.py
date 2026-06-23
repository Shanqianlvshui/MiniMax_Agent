from fastapi.testclient import TestClient

from app.main import create_app


def test_non_hardware_task_runs_full_agent_sequence(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())

    task = client.post("/tasks", json={"goal": "Run the workflow smoke test"}).json()

    with client.stream("GET", f"/tasks/{task['id']}/events") as response:
        assert response.status_code == 200
        list(response.iter_lines())

    detail = client.get(f"/tasks/{task['id']}/details").json()

    assert detail["task"]["status"] == "completed"
    started_agents = [
        event["payload"]["agent"]
        for event in detail["events"]
        if event["type"] == "agent.started"
    ]
    assert started_agents == [
        "manager",
        "planner",
        "researcher",
        "executor",
        "reviewer",
        "writer",
    ]
    assert {artifact["kind"] for artifact in detail["artifacts"]} >= {
        "plan",
        "execution_summary",
        "final_report",
    }
    assert detail["reviews"][-1]["status"] == "passed"


def test_stm32_usb_task_requires_human_approval_until_board_facts_are_confirmed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())

    task = client.post(
        "/tasks",
        json={"goal": "Develop STM32F103C8T6 USB CDC driver with CubeMX"},
    ).json()

    with client.stream("GET", f"/tasks/{task['id']}/events") as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert any("event: approval.required" in line for line in lines)

    detail = client.get(f"/tasks/{task['id']}/details").json()

    assert detail["task"]["status"] == "waiting_human_input"
    assert detail["reviews"][-1]["status"] == "needs_human"
    assert detail["hardware_validations"][-1]["status"] == "not_run"
    assert any(
        assumption["status"] == "needs_human_confirmation"
        for assumption in detail["assumptions"]
    )
    assert any(
        call["tool_name"] == "cubemx.plan" and call["status"] == "ok"
        for call in detail["tool_calls"]
    )


def test_human_approval_finalizes_waiting_task_without_claiming_hardware_pass(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    client = TestClient(create_app())

    task = client.post(
        "/tasks",
        json={"goal": "Develop STM32F103C8T6 USB CDC driver with CubeMX"},
    ).json()
    with client.stream("GET", f"/tasks/{task['id']}/events") as response:
        assert response.status_code == 200
        list(response.iter_lines())

    response = client.post(
        f"/tasks/{task['id']}/approval",
        json={"decision": "approve", "notes": "Approve assumptions for draft output."},
    )

    assert response.status_code == 200

    detail = client.get(f"/tasks/{task['id']}/details").json()

    assert detail["task"]["status"] == "completed"
    assert detail["hardware_validations"][-1]["status"] == "not_run"
    assert all(
        assumption["status"] != "needs_human_confirmation"
        for assumption in detail["assumptions"]
    )
    assert detail["artifacts"][-1]["kind"] == "final_report"
