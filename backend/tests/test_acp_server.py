import asyncio

import acp
from acp.schema import (
    EmbeddedResourceContentBlock,
    ResourceContentBlock,
    TextResourceContents,
)

from app.adapters.acp_server import (
    MiniMaxAcpAgent,
    extract_prompt_text,
    render_workflow_diagram,
)
from app.models import TaskStatus
from app.storage import TaskStore


class RecordingClient:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update, kwargs))


def update_texts(client: RecordingClient) -> list[str]:
    texts = []
    for _, update, _ in client.updates:
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        if text:
            texts.append(text)
    return texts


def tool_updates(client: RecordingClient):
    return [
        update
        for _, update, _ in client.updates
        if getattr(update, "session_update", None) == "tool_call"
    ]


def test_extract_prompt_text_accepts_text_links_and_embedded_resources():
    prompt = [
        acp.text_block("实现 USB CDC"),
        ResourceContentBlock(
            type="resource_link",
            name="官方文档",
            uri="https://example.com/reference.pdf",
        ),
        EmbeddedResourceContentBlock(
            type="resource",
            resource=TextResourceContents(uri="file:///note.md", text="板级说明"),
        ),
    ]

    text = extract_prompt_text(prompt)

    assert "实现 USB CDC" in text
    assert "[资源链接] 官方文档: https://example.com/reference.pdf" in text
    assert "[嵌入资源] file:///note.md" in text
    assert "板级说明" in text


def test_render_workflow_diagram_marks_completed_current_and_pending():
    diagram = render_workflow_diagram(
        completed_agents={"manager", "planner"},
        current_agent="researcher",
        note="规划员已完成",
    )

    assert "```mermaid" in diagram
    assert 'manager["管理器<br/>完成"]' in diagram
    assert 'planner["规划员<br/>完成"]' in diagram
    assert 'researcher["研究员<br/>当前"]' in diagram
    assert 'executor["执行员<br/>待执行"]' in diagram
    assert "manager --> planner" in diagram
    assert "class researcher current" in diagram


def test_acp_agent_initializes_and_creates_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M3")
    agent = MiniMaxAcpAgent(TaskStore(tmp_path / "tasks.db"))

    init = asyncio.run(agent.initialize(protocol_version=1))
    session = asyncio.run(agent.new_session(cwd=str(tmp_path)))

    assert init.protocol_version == 1
    assert init.agent_info.name == "minimax-agent"
    assert init.agent_capabilities.prompt_capabilities.embedded_context is True
    assert init.auth_methods[0].vars[0].name == "MINIMAX_API_KEY"
    assert session.session_id
    assert session.modes.current_mode_id == "evidence-first-workflow"
    assert session.models.current_model_id == "MiniMax-M3"
    assert session.config_options[0].id == "strict_evidence"
    assert (
        asyncio.run(
            agent.set_session_mode(
                mode_id="evidence-first-workflow",
                session_id=session.session_id,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            agent.set_config_option(
                config_id="strict_evidence",
                session_id=session.session_id,
                value=True,
            )
        )
        is None
    )
    assert asyncio.run(agent.authenticate(method_id="minimax-env")) is None


def test_acp_prompt_runs_workflow_and_streams_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = RecordingClient()
    store = TaskStore(tmp_path / "tasks.db")
    agent = MiniMaxAcpAgent(store)
    agent.on_connect(client)

    session = asyncio.run(agent.new_session(cwd=str(tmp_path)))
    response = asyncio.run(
        agent.prompt(
            prompt=[acp.text_block("Run the workflow smoke test")],
            session_id=session.session_id,
            message_id="00000000-0000-0000-0000-000000000001",
        )
    )

    state = agent.sessions[session.session_id]
    task = store.get_task(state.task_id)
    texts = "".join(update_texts(client))

    assert response.stop_reason == "end_turn"
    assert response.user_message_id == "00000000-0000-0000-0000-000000000001"
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert "管理器（manager）开始" in texts
    assert "撰写员（writer）完成" in texts
    assert "任务已完成" in texts
    assert texts.count("```mermaid") >= 7
    assert 'manager["管理器<br/>完成"]' in texts
    assert 'writer["撰写员<br/>完成"]' in texts
    assert tool_updates(client)


def test_acp_hardware_prompt_surfaces_human_approval_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_AGENT_LLM_MODE", "fake")
    monkeypatch.setenv("MINIMAX_AGENT_DB_PATH", str(tmp_path / "tasks.db"))
    client = RecordingClient()
    store = TaskStore(tmp_path / "tasks.db")
    agent = MiniMaxAcpAgent(store)
    agent.on_connect(client)

    session = asyncio.run(agent.new_session(cwd=str(tmp_path)))
    response = asyncio.run(
        agent.prompt(
            prompt=[
                acp.text_block(
                    "Develop STM32F103C8T6 USB CDC driver with CubeMX"
                )
            ],
            session_id=session.session_id,
        )
    )

    state = agent.sessions[session.session_id]
    task = store.get_task(state.task_id)
    texts = "".join(update_texts(client))
    tools = tool_updates(client)

    assert response.stop_reason == "end_turn"
    assert task is not None
    assert task.status == TaskStatus.WAITING_HUMAN_INPUT
    assert "需要人工批准" in texts
    assert "审查员要求人工处理" in texts
    assert 'reviewer["审查员<br/>完成"]' in texts
    assert any("审查" in update.title for update in tools)
    assert any("证据" in update.title for update in tools)
    assert any("假设" in update.title for update in tools)
    assert any("硬件验证" in update.title for update in tools)
