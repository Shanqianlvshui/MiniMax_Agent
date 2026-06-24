from fastapi.testclient import TestClient

from app.main import create_app
from app.llm import FakeLLMClient, LLMClient
from app.models import TaskStatus
from app.runner import REVIEW_PASSED, REVIEW_RETRY, TaskRunner
from app.storage import TaskStore


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
    token_agents = {
        event["payload"]["agent"]
        for event in detail["events"]
        if event["type"] == "agent.token"
    }
    assert token_agents == {
        "manager",
        "planner",
        "researcher",
        "executor",
        "reviewer",
        "writer",
    }
    assert {artifact["kind"] for artifact in detail["artifacts"]} >= {
        "skill_selection",
        "plan",
        "execution_summary",
        "final_report",
    }
    skill_artifact = next(
        artifact
        for artifact in detail["artifacts"]
        if artifact["kind"] == "skill_selection"
    )
    selected_skill_ids = {
        skill["id"] for skill in skill_artifact["metadata"]["skills"]
    }
    assert selected_skill_ids >= {"skill-router", "review-findings-first"}
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
    skill_artifact = next(
        artifact
        for artifact in detail["artifacts"]
        if artifact["kind"] == "skill_selection"
    )
    selected_skill_ids = {
        skill["id"] for skill in skill_artifact["metadata"]["skills"]
    }
    assert selected_skill_ids >= {
        "skill-router",
        "grill-before-risky-work",
        "domain-language",
        "tdd-feedback-loop",
        "evidence-first-research",
        "review-findings-first",
    }
    assert any(
        event["type"] == "workflow.skills.selected"
        for event in detail["events"]
    )
    assert any(
        call["tool_name"] == "workflow.skills.select" and call["status"] == "ok"
        for call in detail["tool_calls"]
    )
    source_artifact = next(
        artifact
        for artifact in detail["artifacts"]
        if artifact["kind"] == "source_lookup"
    )
    source_titles = {
        source["title"] for source in source_artifact["metadata"]["sources"]
    }
    assert "STM32F103x8/B datasheet" in source_titles
    assert "RM0008 STM32F10xxx reference manual" in source_titles
    assert any(
        call["tool_name"] == "source.lookup" and call["status"] == "ok"
        for call in detail["tool_calls"]
    )
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


def test_retryable_review_returns_to_executor_before_writer(tmp_path):
    class RetryOnceRunner(TaskRunner):
        def __init__(self, store, llm):
            super().__init__(store, llm)
            self.review_count = 0

        def _record_reviewer_outputs(
            self,
            task_id,
            goal,
            reviewer_output,
            retry_attempt=0,
        ):
            self.review_count += 1
            if self.review_count == 1:
                self.store.record_review(
                    task_id=task_id,
                    status="retry",
                    summary="审查员要求执行员修正一次。",
                    checks=[
                        {
                            "name": "simulated_retry",
                            "status": "fail",
                            "detail": reviewer_output[:120],
                        }
                    ],
                    retry_instructions="重新执行并保留审查反馈。",
                )
                return REVIEW_RETRY
            self.store.record_review(
                task_id=task_id,
                status="passed",
                summary="重试后通过。",
                checks=[
                    {
                        "name": "retry_attempt",
                        "status": "pass",
                        "detail": str(retry_attempt),
                    }
                ],
            )
            return REVIEW_PASSED

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Run retryable workflow smoke test")
    runner = RetryOnceRunner(store, FakeLLMClient())

    import asyncio

    asyncio.run(runner.run_task(task.id))

    detail = store.task_detail(task.id)
    assert detail is not None
    assert detail.task.status == TaskStatus.COMPLETED
    started_agents = [
        event.payload["agent"]
        for event in detail.events
        if event.type == "agent.started"
    ]
    assert started_agents == [
        "manager",
        "planner",
        "researcher",
        "executor",
        "reviewer",
        "executor",
        "reviewer",
        "writer",
    ]
    assert any(event.type == "workflow.retry.started" for event in detail.events)
    assert [review.status for review in detail.reviews][-2:] == ["retry", "passed"]


def test_retry_exhaustion_records_human_review_gate(tmp_path):
    class AlwaysRetryRunner(TaskRunner):
        def _record_reviewer_outputs(
            self,
            task_id,
            goal,
            reviewer_output,
            retry_attempt=0,
        ):
            self.store.record_review(
                task_id=task_id,
                status="retry",
                summary="审查员仍要求重试。",
                checks=[
                    {
                        "name": "simulated_retry",
                        "status": "fail",
                        "detail": str(retry_attempt),
                    }
                ],
                retry_instructions="继续重做。",
            )
            return REVIEW_RETRY

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Run retry exhaustion workflow smoke test")
    runner = AlwaysRetryRunner(store, FakeLLMClient())

    import asyncio

    asyncio.run(runner.run_task(task.id))

    detail = store.task_detail(task.id)
    assert detail is not None
    assert detail.task.status == TaskStatus.WAITING_HUMAN_INPUT
    assert [review.status for review in detail.reviews][-3:] == [
        "retry",
        "retry",
        "needs_human",
    ]
    assert any(event.type == "workflow.retry.exhausted" for event in detail.events)
    assert any(event.type == "approval.required" for event in detail.events)


def test_human_rejection_reruns_from_planner_instead_of_failing(
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
        json={"decision": "reject", "notes": "补齐官方来源和板级假设后重做。"},
    )

    assert response.status_code == 200

    detail = client.get(f"/tasks/{task['id']}/details").json()
    started_agents = [
        event["payload"]["agent"]
        for event in detail["events"]
        if event["type"] == "agent.started"
    ]

    assert detail["task"]["status"] == "waiting_human_input"
    assert detail["reviews"][-1]["status"] == "needs_human"
    assert "task.failed" not in {event["type"] for event in detail["events"]}
    assert any(
        event["type"] == "workflow.rerun.started"
        for event in detail["events"]
    )
    assert started_agents.count("planner") == 2
    assert started_agents.count("executor") == 2
    assert started_agents.count("reviewer") == 2


def test_empty_llm_agent_output_gets_visible_audit_token(tmp_path):
    class EmptyLLM(LLMClient):
        async def stream_agent(self, agent_name, system_prompt, user_content):
            if False:
                yield agent_name

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Run empty output fallback")
    runner = TaskRunner(store, EmptyLLM())

    import asyncio

    output = asyncio.run(
        runner._run_llm_agent(task.id, "manager", "空输出测试")
    )

    events = store.list_events(task.id)
    assert output
    assert any(event.type == "agent.empty_output" for event in events)
    assert any(
        event.type == "agent.token" and event.payload["agent"] == "manager"
        for event in events
    )


def test_agent_context_includes_applicable_workflow_skills(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Develop STM32F103C8T6 USB CDC driver")
    runner = TaskRunner(store, LLMClient())
    skills = runner._select_and_record_skills(task.id, task.goal)

    planner_context = runner._agent_context(
        task_id=task.id,
        goal=task.goal,
        agent_name="planner",
        prior_output="manager output",
        skills=skills,
    )
    researcher_context = runner._agent_context(
        task_id=task.id,
        goal=task.goal,
        agent_name="researcher",
        prior_output="planner output",
        skills=skills,
    )

    assert "Workflow Skills" in planner_context
    assert "输出协议" in planner_context
    assert "Skill Router" in planner_context
    assert "TDD Feedback Loop" in planner_context
    assert "Evidence First Research" in researcher_context
    assert "Review Findings First" in researcher_context
