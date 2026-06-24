import asyncio
import os
from dataclasses import dataclass, field
from uuid import uuid4

import acp
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    AuthEnvVar,
    EnvVarAuthMethod,
    Implementation,
    InitializeResponse,
    ModelInfo,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    SessionConfigOptionBoolean,
    SessionMode,
    SessionModeState,
    SessionModelState,
)

from ..config import load_backend_env
from ..llm import create_llm_client
from ..models import TaskDetail, TaskEvent, TaskStatus
from ..runner import TaskRunner
from ..storage import TaskStore


AGENT_LABELS = {
    "manager": "管理器",
    "planner": "规划员",
    "researcher": "研究员",
    "executor": "执行员",
    "reviewer": "审查员",
    "writer": "撰写员",
}


@dataclass
class AcpSessionState:
    cwd: str
    task_id: str | None = None
    last_event_seq: int = 0
    sent_tool_call_ids: set[str] = field(default_factory=set)


class MiniMaxAcpAgent:
    """ACP stdio adapter that hosts the existing MiniMax workflow runner."""

    def __init__(self, store: TaskStore | None = None) -> None:
        self.store = store or TaskStore()
        self.client: acp.Client | None = None
        self.sessions: dict[str, AcpSessionState] = {}

    def on_connect(self, conn: acp.Client) -> None:
        self.client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities=None,
        client_info=None,
        **kwargs,
    ) -> InitializeResponse:
        del protocol_version, client_capabilities, client_info, kwargs
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(
                name="minimax-agent",
                title="MiniMax Agent 工作流",
                version="0.1.0",
            ),
            agent_capabilities=AgentCapabilities(
                load_session=False,
                prompt_capabilities=PromptCapabilities(embedded_context=True),
            ),
            auth_methods=[
                EnvVarAuthMethod(
                    type="env_var",
                    id="minimax-env",
                    name="MiniMax API Key",
                    description="MiniMax anthropic/v1 接口凭据，通常写入 backend/.env。",
                    vars=[
                        AuthEnvVar(
                            name="MINIMAX_API_KEY",
                            label="MiniMax API Key",
                            secret=True,
                        ),
                    ],
                )
            ],
        )

    async def authenticate(self, method_id: str, **kwargs) -> None:
        del method_id, kwargs
        return None

    async def new_session(
        self,
        cwd: str,
        additional_directories=None,
        mcp_servers=None,
        **kwargs,
    ) -> NewSessionResponse:
        del additional_directories, mcp_servers, kwargs
        session_id = str(uuid4())
        self.sessions[session_id] = AcpSessionState(cwd=cwd)
        return NewSessionResponse(
            session_id=session_id,
            modes=SessionModeState(
                current_mode_id="evidence-first-workflow",
                available_modes=[
                    SessionMode(
                        id="evidence-first-workflow",
                        name="证据优先工作流",
                        description="固定 Manager -> Planner -> Researcher -> Executor -> Reviewer -> Writer 流程。",
                    )
                ],
            ),
            models=SessionModelState(
                current_model_id=os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
                available_models=[
                    ModelInfo(
                        model_id=os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
                        name=os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
                        description="通过 MiniMax 官方 anthropic/v1 messages 接口调用。",
                    )
                ],
            ),
            config_options=[
                SessionConfigOptionBoolean(
                    type="boolean",
                    id="strict_evidence",
                    name="严格证据门禁",
                    description="硬件结论缺少官方来源或实测记录时，不标记为成功。",
                    current_value=True,
                )
            ],
        )

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs,
    ) -> None:
        del kwargs
        self._require_session(session_id)
        if mode_id != "evidence-first-workflow":
            raise acp.RequestError.invalid_params({"mode_id": mode_id})
        return None

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs,
    ) -> None:
        del kwargs
        self._require_session(session_id)
        if config_id != "strict_evidence" or value is not True:
            raise acp.RequestError.invalid_params(
                {"config_id": config_id, "value": value}
            )
        return None

    async def prompt(
        self,
        prompt,
        session_id: str,
        message_id: str | None = None,
        **kwargs,
    ) -> PromptResponse:
        del kwargs
        session = self._require_session(session_id)
        goal = extract_prompt_text(prompt).strip()
        if not goal:
            await self._send_agent_text(
                session_id,
                "我没有收到可执行的文本任务。请用文字描述目标。",
            )
            return PromptResponse(stop_reason="refusal", user_message_id=message_id)

        await self._send_user_text(session_id, goal, message_id)

        task = self.store.create_task(str(uuid4()), goal)
        session.task_id = task.id
        session.last_event_seq = 0
        session.sent_tool_call_ids.clear()

        await self._send_agent_text(
            session_id,
            f"已创建内部任务 `{task.id}`，开始运行证据优先多 Agent 工作流。\n\n",
        )

        runner = TaskRunner(self.store, create_llm_client())
        run = asyncio.create_task(runner.run_task(task.id))
        stop_reason = "end_turn"
        try:
            while True:
                await self._flush_events(session_id)
                if run.done():
                    await run
                    await self._flush_events(session_id)
                    break
                current = self.store.get_task(task.id)
                if current and current.status == TaskStatus.CANCEL_REQUESTED:
                    stop_reason = "cancelled"
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self.store.request_cancel(task.id)
            stop_reason = "cancelled"
        finally:
            if not run.done():
                await run

        latest = self.store.get_task(task.id)
        if latest and latest.status == TaskStatus.CANCELLED:
            stop_reason = "cancelled"
        return PromptResponse(stop_reason=stop_reason, user_message_id=message_id)

    async def cancel(self, session_id: str, **kwargs) -> None:
        del kwargs
        session = self.sessions.get(session_id)
        if session and session.task_id:
            self.store.request_cancel(session.task_id)

    async def _flush_events(self, session_id: str) -> None:
        session = self._require_session(session_id)
        if session.task_id is None:
            return

        events = [
            event
            for event in self.store.list_events(session.task_id)
            if event.seq > session.last_event_seq
        ]
        for event in events:
            session.last_event_seq = event.seq
            await self._send_event(session_id, event)

    async def _send_event(self, session_id: str, event: TaskEvent) -> None:
        payload = event.payload
        if event.type == "agent.started":
            agent = payload.get("agent", "")
            await self._send_agent_text(
                session_id,
                f"\n\n### {self._agent_label(agent)}开始\n",
            )
            return

        if event.type == "agent.token":
            token = payload.get("token", "")
            if token:
                await self._send_agent_text(session_id, token)
            return

        if event.type == "agent.completed":
            agent = payload.get("agent", "")
            await self._send_agent_text(
                session_id,
                f"\n### {self._agent_label(agent)}完成\n",
            )
            return

        if event.type == "approval.required":
            reason = payload.get("reason", "需要人工处理。")
            await self._send_current_tool_records(session_id)
            await self._send_agent_text(
                session_id,
                f"\n\n**需要人工批准**：{reason}\n",
            )
            return

        if event.type in {"task.completed", "task.failed", "task.cancelled"}:
            await self._send_task_summary(session_id, event)
            return

    async def _send_task_summary(self, session_id: str, event: TaskEvent) -> None:
        session = self._require_session(session_id)
        if session.task_id is None:
            return
        detail = self.store.task_detail(session.task_id)
        if detail is None:
            return

        await self._send_tool_records(session_id, detail)
        status_text = {
            "task.completed": "任务已完成。",
            "task.failed": f"任务失败：{event.payload.get('error', '')}",
            "task.cancelled": "任务已取消。",
        }.get(event.type, event.type)
        await self._send_agent_text(
            session_id,
            "\n\n---\n"
            f"{status_text}\n"
            f"- 工具调用：{len(detail.tool_calls)} 条\n"
            f"- 证据记录：{len(detail.evidence)} 条\n"
            f"- 假设记录：{len(detail.assumptions)} 条\n"
            f"- 审查记录：{len(detail.reviews)} 条\n"
            f"- 产物记录：{len(detail.artifacts)} 条\n",
        )

    async def _send_current_tool_records(self, session_id: str) -> None:
        session = self._require_session(session_id)
        if session.task_id is None:
            return
        detail = self.store.task_detail(session.task_id)
        if detail is not None:
            await self._send_tool_records(session_id, detail)

    async def _send_tool_records(self, session_id: str, detail: TaskDetail) -> None:
        session = self._require_session(session_id)
        records: list[tuple[str, str, str, dict]] = []
        records.extend(
            (
                f"tool-{call.id}",
                f"{call.agent_name}: {call.tool_name}",
                "completed" if call.status == "ok" else "failed",
                {
                    "args": call.args,
                    "summary": call.result_summary,
                    "stdout": call.stdout,
                    "stderr": call.stderr,
                },
            )
            for call in detail.tool_calls
        )
        records.extend(
            (
                f"artifact-{artifact.id}",
                f"产物：{artifact.title}",
                "completed",
                {
                    "kind": artifact.kind,
                    "path": artifact.path,
                    "metadata": artifact.metadata,
                },
            )
            for artifact in detail.artifacts
        )
        records.extend(
            (
                f"review-{review.id}",
                f"审查：{review.status}",
                "completed" if review.status in {"passed", "needs_human"} else "failed",
                {
                    "summary": review.summary,
                    "checks": review.checks,
                    "retry_instructions": review.retry_instructions,
                },
            )
            for review in detail.reviews
        )
        records.extend(
            (
                f"evidence-{evidence.id}",
                f"证据：{evidence.claim}",
                "completed",
                {
                    "source_type": evidence.source_type,
                    "source_title": evidence.source_title,
                    "url": evidence.url,
                    "version_or_date": evidence.version_or_date,
                    "section_or_page": evidence.section_or_page,
                    "confidence": evidence.confidence,
                    "notes": evidence.notes,
                },
            )
            for evidence in detail.evidence
        )
        records.extend(
            (
                f"assumption-{assumption.id}",
                f"假设：{assumption.claim}",
                "completed"
                if assumption.status in {"confirmed_by_human", "confirmed"}
                else "failed",
                {
                    "scope": assumption.scope,
                    "reason": assumption.reason,
                    "risk": assumption.risk,
                    "status": assumption.status,
                    "requires_user_confirmation": assumption.requires_user_confirmation,
                },
            )
            for assumption in detail.assumptions
        )
        records.extend(
            (
                f"hardware-{validation.id}",
                f"硬件验证：{validation.name}",
                "completed" if validation.status not in {"failed"} else "failed",
                {
                    "status": validation.status,
                    "evidence": validation.evidence,
                },
            )
            for validation in detail.hardware_validations
        )

        for record_id, title, status, raw_output in records:
            if record_id in session.sent_tool_call_ids:
                continue
            session.sent_tool_call_ids.add(record_id)
            await self._send_tool_call(session_id, record_id, title, status, raw_output)

    async def _send_user_text(
        self,
        session_id: str,
        text: str,
        message_id: str | None,
    ) -> None:
        if self.client is None:
            return
        update = acp.update_user_message_text(text)
        update.message_id = message_id
        await self.client.session_update(
            session_id=session_id,
            update=update,
        )

    async def _send_agent_text(self, session_id: str, text: str) -> None:
        if self.client is None:
            return
        await self.client.session_update(
            session_id=session_id,
            update=acp.update_agent_message_text(text),
        )

    async def _send_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        title: str,
        status: str,
        raw_output: dict,
    ) -> None:
        if self.client is None:
            return
        await self.client.session_update(
            session_id=session_id,
            update=acp.start_tool_call(
                tool_call_id,
                title,
                kind="other",
                status=status,
                raw_output=raw_output,
            ),
        )

    def _require_session(self, session_id: str) -> AcpSessionState:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise acp.RequestError.resource_not_found(session_id) from exc

    @staticmethod
    def _agent_label(agent_name: str) -> str:
        label = AGENT_LABELS.get(agent_name, agent_name or "Agent")
        return f"{label}（{agent_name}）"


def extract_prompt_text(prompt) -> str:
    parts: list[str] = []
    for block in prompt:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(block.text)
            continue
        if block_type == "resource_link":
            parts.append(f"\n[资源链接] {block.name}: {block.uri}")
            continue
        if block_type == "resource":
            resource = block.resource
            text = getattr(resource, "text", None)
            if text:
                parts.append(f"\n[嵌入资源] {resource.uri}\n{text}")
    return "\n".join(parts)


async def main() -> None:
    load_backend_env()
    await acp.run_agent(MiniMaxAcpAgent())


if __name__ == "__main__":
    asyncio.run(main())
