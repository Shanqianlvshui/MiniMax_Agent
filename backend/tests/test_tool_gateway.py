from app.storage import TaskStore
from app.tools.gateway import ToolGateway


def test_tool_gateway_records_denied_calls(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Run least privilege check")
    gateway = ToolGateway(store)

    result = gateway.invoke(
        task_id=task.id,
        agent_name="researcher",
        tool_name="artifact.create",
        args={"kind": "report", "title": "Denied", "path": "generated/denied.md"},
    )

    assert result.ok is False

    calls = store.list_tool_calls(task.id)
    assert len(calls) == 1
    assert calls[0].status == "denied"
    assert calls[0].tool_name == "artifact.create"


def test_source_lookup_is_researcher_only_and_records_artifact(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task("task-1", "Develop STM32 USB CDC")
    gateway = ToolGateway(store)

    denied = gateway.invoke(
        task_id=task.id,
        agent_name="executor",
        tool_name="source.lookup",
        args={"sources": []},
    )
    allowed = gateway.invoke(
        task_id=task.id,
        agent_name="researcher",
        tool_name="source.lookup",
        args={
            "intent": task.goal,
            "policy": "official first",
            "sources": [{"title": "RM0008 STM32F10xxx reference manual"}],
        },
    )

    assert denied.ok is False
    assert allowed.ok is True

    calls = store.list_tool_calls(task.id)
    assert calls[0].status == "denied"
    assert calls[1].status == "ok"
    assert calls[1].tool_name == "source.lookup"

    artifacts = store.list_artifacts(task.id)
    assert artifacts[-1].kind == "source_lookup"
    assert artifacts[-1].metadata["sources"][0]["title"] == (
        "RM0008 STM32F10xxx reference manual"
    )
