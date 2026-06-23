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
    assert "not permitted" in result.summary

    calls = store.list_tool_calls(task.id)
    assert len(calls) == 1
    assert calls[0].status == "denied"
    assert calls[0].tool_name == "artifact.create"
