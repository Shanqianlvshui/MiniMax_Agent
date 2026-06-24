from typing import Literal

from .llm import LLMClient
from .models import TaskStatus
from .source_registry import source_lookup
from .storage import TaskStore
from .tools.gateway import ToolGateway
from .workflow_skills import (
    WorkflowSkill,
    format_skill_context,
    select_workflow_skills,
    skill_selection_record,
    workflow_skills_by_id,
)


AGENT_SEQUENCE = ["manager", "planner", "researcher", "executor", "reviewer", "writer"]
ReviewDecision = Literal["passed", "retry", "needs_human"]
REVIEW_PASSED: ReviewDecision = "passed"
REVIEW_RETRY: ReviewDecision = "retry"
REVIEW_NEEDS_HUMAN: ReviewDecision = "needs_human"
MAX_REVIEW_RETRIES = 1

AGENT_PROMPTS = {
    "manager": (
        "你是 Manager Agent。你负责确认任务范围、选择固定工作流、读取已选 Workflow Skills、"
        "指出必须保留的验证门禁。不要执行业务工具，不要声称硬件已经验证。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
    "planner": (
        "你是 Planner Agent。你负责按已选 Workflow Skills 把任务拆成可验证步骤。"
        "每一步必须包含验证标准，硬件相关结论必须标记需要官方来源或实测证据。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
    "researcher": (
        "你是 Researcher Agent。你负责列出需要查证的官方资料、证据缺口和显式假设。"
        "不能把未查证内容写成事实。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
    "executor": (
        "你是 Executor Agent。你负责提出受控执行方案。当前不能直接改文件、跑 shell 或改 .ioc；"
        "只能说明需要通过后端 ToolGateway 执行的动作。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
    "reviewer": (
        "你是 Reviewer Agent。你负责按证据优先原则审查任务。没有官方来源、构建日志、"
        "烧录日志或板级验证时，必须指出不能标记为硬件成功。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
    "writer": (
        "你是 Writer Agent。你负责把已验证事实、未验证假设、产物和下一步整理成最终报告。"
        "不能把未验证硬件行为写成成功。输出中文。"
        "最终必须输出 3 到 8 行可见中文文本，不能只输出 thinking。"
    ),
}

OUTPUT_PROTOCOL = (
    "输出协议：必须使用以下 4 个小节，每节最多 3 条：\n"
    "1. 已验证事实：只写来自用户输入、已记录证据或已执行工具的事实。\n"
    "2. 待查证：列出需要官方资料、日志或实测确认的点。\n"
    "3. 显式假设：列出临时假设；没有则写“无”。\n"
    "4. 下一步：只写当前 Agent 允许推动的后续动作。\n"
    "禁止把猜测写成事实；禁止因为常识或模型记忆声称硬件已成功。"
)


class TaskRunner:
    def __init__(self, store: TaskStore, llm: LLMClient):
        self.store = store
        self.llm = llm
        self.gateway = ToolGateway(store)

    async def run_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            return

        self.store.append_event(task_id, "task.started", {"status": task.status.value})
        try:
            skills = self._select_and_record_skills(task_id, task.goal)
            manager_output = await self._run_llm_agent(
                task_id,
                "manager",
                self._agent_context(task_id, task.goal, "manager", "", skills),
            )
            if self._should_stop(task_id):
                return

            planning = await self._run_planning_and_research(
                task_id,
                task.goal,
                skills,
                manager_output,
            )
            if planning is None or self._should_stop(task_id):
                return
            _planner_output, researcher_output = planning

            decision = await self._run_execution_review_loop(
                task_id,
                task.goal,
                skills,
                researcher_output,
            )
            if self._should_stop(task_id):
                return
            if decision != REVIEW_PASSED:
                self._request_human_approval(
                    task_id,
                    "审查员发现仍有未验证的问题，需要人工确认后继续。",
                )
                return

            await self._writer(task_id, task.goal)
            self.store.complete_task(task_id)
            self.store.append_event(task_id, "task.completed", {"status": "completed"})
        except Exception as exc:
            latest = self.store.get_task(task_id)
            self.store.fail_task(task_id, str(exc))
            self.store.append_event(
                task_id,
                "agent.failed",
                {"agent": latest.current_agent if latest else None, "error": str(exc)},
            )
            self.store.append_event(task_id, "task.failed", {"error": str(exc)})
        finally:
            latest = self.store.get_task(task_id)
            if latest and latest.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.CANCEL_REQUESTED,
            }:
                self.store.set_current_agent(task_id, None)

    async def _run_planning_and_research(
        self,
        task_id: str,
        goal: str,
        skills: list[WorkflowSkill],
        prior_output: str,
    ) -> tuple[str, str] | None:
        planner_output = await self._run_llm_agent(
            task_id,
            "planner",
            self._agent_context(task_id, goal, "planner", prior_output, skills),
        )
        self._record_planner_artifact(task_id, planner_output)
        if self._should_stop(task_id):
            return None

        researcher_output = await self._run_llm_agent(
            task_id,
            "researcher",
            self._agent_context(task_id, goal, "researcher", planner_output, skills),
        )
        self._record_researcher_outputs(task_id, goal, researcher_output)
        if self._should_stop(task_id):
            return None

        return planner_output, researcher_output

    async def _run_execution_review_loop(
        self,
        task_id: str,
        goal: str,
        skills: list[WorkflowSkill],
        researcher_output: str,
        retry_note: str = "",
    ) -> ReviewDecision:
        current_retry_note = retry_note
        for retry_attempt in range(MAX_REVIEW_RETRIES + 1):
            executor_prior = researcher_output
            if current_retry_note:
                executor_prior = "\n\n".join(
                    [
                        researcher_output,
                        "审查反馈，要求执行员重做：",
                        current_retry_note,
                    ]
                )

            executor_output = await self._run_llm_agent(
                task_id,
                "executor",
                self._agent_context(task_id, goal, "executor", executor_prior, skills),
            )
            self._record_executor_outputs(task_id, goal, executor_output)
            if self._should_stop(task_id):
                return REVIEW_NEEDS_HUMAN

            reviewer_output = await self._run_llm_agent(
                task_id,
                "reviewer",
                self._agent_context(task_id, goal, "reviewer", executor_output, skills),
            )
            decision = self._record_reviewer_outputs(
                task_id,
                goal,
                reviewer_output,
                retry_attempt=retry_attempt,
            )
            if decision != REVIEW_RETRY:
                return decision

            if retry_attempt >= MAX_REVIEW_RETRIES:
                self.store.record_review(
                    task_id=task_id,
                    status="needs_human",
                    summary="审查重试次数已用完，需要人工处理。",
                    checks=[
                        {
                            "name": "retry_budget",
                            "status": "fail",
                            "detail": f"已尝试 {retry_attempt + 1} 次执行/审查循环。",
                        },
                        {
                            "name": "reviewer_agent_output",
                            "status": "recorded",
                            "detail": reviewer_output[:800],
                        },
                    ],
                    retry_instructions="请人工批准、打回并补充说明，或补充缺失证据后继续。",
                )
                self.store.append_event(
                    task_id,
                    "workflow.retry.exhausted",
                    {
                        "from": "reviewer",
                        "to": "human",
                        "attempts": retry_attempt + 1,
                        "reason": reviewer_output[:800],
                    },
                )
                return REVIEW_NEEDS_HUMAN

            current_retry_note = reviewer_output[:1200]
            self.store.append_event(
                task_id,
                "workflow.retry.started",
                {
                    "from": "reviewer",
                    "to": "executor",
                    "attempt": retry_attempt + 1,
                    "max_retries": MAX_REVIEW_RETRIES,
                    "reason": current_retry_note,
                },
            )

        return REVIEW_NEEDS_HUMAN

    def _request_human_approval(self, task_id: str, reason: str) -> None:
        self.store.set_waiting_for_human(task_id, "reviewer")
        self.store.append_event(
            task_id,
            "approval.required",
            {
                "agent": "reviewer",
                "reason": reason,
            },
        )

    async def finalize_after_approval(
        self,
        task_id: str,
        decision: str,
        notes: str,
    ) -> None:
        task = self.store.get_task(task_id)
        if task is None or task.status != TaskStatus.WAITING_HUMAN_INPUT:
            return

        self.store.append_event(
            task_id,
            "approval.recorded",
            {"decision": decision, "notes": notes},
        )
        if decision == "reject":
            rerun_note = notes or "人工打回，要求重新规划并重新执行。"
            self.store.record_review(
                task_id=task_id,
                status="rejected_by_human",
                summary="人工审查打回了当前运行，编排器将回到规划阶段。",
                checks=[
                    {
                        "name": "human_gate",
                        "status": "fail",
                        "detail": rerun_note,
                    }
                ],
                retry_instructions=rerun_note,
            )
            self.store.resume_task(task_id, "planner")
            self.store.append_event(
                task_id,
                "workflow.rerun.started",
                {
                    "from": "human",
                    "to": "planner",
                    "reason": rerun_note,
                },
            )
            skills = self._skills_from_artifacts(task_id)
            if not skills:
                skills = self._select_and_record_skills(task_id, task.goal)
            planning = await self._run_planning_and_research(
                task_id,
                task.goal,
                skills,
                f"人工打回说明：{rerun_note}",
            )
            if planning is None or self._should_stop(task_id):
                return
            _planner_output, researcher_output = planning
            review_decision = await self._run_execution_review_loop(
                task_id,
                task.goal,
                skills,
                researcher_output,
                retry_note=rerun_note,
            )
            if self._should_stop(task_id):
                return
            if review_decision != REVIEW_PASSED:
                self._request_human_approval(
                    task_id,
                    "人工打回后的重跑仍有审查问题，需要再次处理。",
                )
                return
            await self._writer(
                task_id,
                task.goal,
                extra_note=f"本次输出来自人工打回后的重跑。打回说明：{rerun_note}",
            )
            self.store.complete_task(task_id)
            self.store.append_event(task_id, "task.completed", {"status": "completed"})
            return

        self.store.confirm_pending_assumptions(task_id)
        self.store.resume_task(task_id, "writer")
        await self._writer(
            task_id,
            task.goal,
            extra_note="人工已批准显式假设；实际板级测试仍未标记为通过。",
        )
        self.store.complete_task(task_id)
        self.store.append_event(task_id, "task.completed", {"status": "completed"})

    async def _writer(
        self,
        task_id: str,
        goal: str,
        extra_note: str = "",
    ) -> None:
        skills = self._skills_from_artifacts(task_id)
        writer_output = await self._run_llm_agent(
            task_id,
            "writer",
            self._agent_context(task_id, goal, "writer", extra_note, skills),
        )
        metadata = {"status": "complete", "summary": writer_output}
        if extra_note:
            metadata["note"] = extra_note
        self.gateway.invoke(
            task_id,
            "writer",
            "artifact.create",
            {
                "kind": "final_report",
                "title": "最终工作流报告",
                "path": "generated/final-report.md",
                "metadata": metadata,
            },
        )

    async def _run_llm_agent(
        self,
        task_id: str,
        agent_name: str,
        user_content: str,
    ) -> str:
        self.store.set_current_agent(task_id, agent_name)
        self.store.append_event(task_id, "agent.started", {"agent": agent_name})

        output: list[str] = []
        system_prompt = "\n".join([AGENT_PROMPTS[agent_name], OUTPUT_PROTOCOL])
        async for token in self.llm.stream_agent(
            agent_name,
            system_prompt,
            user_content,
        ):
            output.append(token)
            self.store.append_event(
                task_id,
                "agent.token",
                {"agent": agent_name, "token": token},
            )

        summary = "".join(output)
        if not summary.strip():
            fallback = f"{agent_name} 已完成 M3 调用，但本次没有返回可见文本；请查看后续审计记录。"
            output.append(fallback)
            summary = fallback
            self.store.append_event(
                task_id,
                "agent.empty_output",
                {"agent": agent_name},
            )
            self.store.append_event(
                task_id,
                "agent.token",
                {"agent": agent_name, "token": fallback},
            )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": agent_name, "summary": summary},
        )
        return summary

    def _select_and_record_skills(
        self,
        task_id: str,
        goal: str,
    ) -> list[WorkflowSkill]:
        skills = select_workflow_skills(goal)
        record = skill_selection_record(goal, skills)
        self.gateway.invoke(
            task_id=task_id,
            agent_name="manager",
            tool_name="workflow.skills.select",
            args=record,
        )
        self.store.append_event(
            task_id,
            "workflow.skills.selected",
            {
                "skills": [skill.id for skill in skills],
                "count": len(skills),
            },
        )
        return skills

    def _record_planner_artifact(self, task_id: str, planner_output: str) -> None:
        self.gateway.invoke(
            task_id=task_id,
            agent_name="planner",
            tool_name="artifact.plan",
            args={
                "title": "规划员任务拆解",
                "path": "generated/planner-plan.md",
                "summary": planner_output,
            },
        )

    def _record_researcher_outputs(
        self,
        task_id: str,
        goal: str,
        researcher_output: str,
    ) -> None:
        if self._is_stm32_usb_goal(goal):
            official_sources = source_lookup(goal)
            self.gateway.invoke(
                task_id,
                "researcher",
                "source.lookup",
                {
                    "title": "STM32F103C8T6 USB CDC 官方来源候选清单",
                    "path": "generated/stm32-usb-cdc-sources.json",
                    "intent": goal,
                    "policy": "官方或一手来源优先；候选清单不等于已摘录证据。",
                    "sources": [source.to_record() for source in official_sources],
                },
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "evidence.record",
                {
                    "claim": "硬件开发事实必须来自官方或一手来源。",
                    "source_type": "project_requirement",
                    "source_title": "用户提出的证据优先工作流要求",
                    "url": None,
                    "version_or_date": None,
                    "section_or_page": "对话",
                    "confidence": "high",
                    "notes": researcher_output[:800],
                },
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "evidence.record",
                {
                    "claim": "本 demo 要求使用 STM32Cube 官方工具链作为配置和生成路径。",
                    "source_type": "project_requirement",
                    "source_title": "用户指定的 STM32CubeIDE / STM32CubeMX 工作流要求",
                    "url": None,
                    "version_or_date": None,
                    "section_or_page": "对话",
                    "confidence": "high",
                    "notes": "运行器将其记录为用户指定的工具链约束。",
                },
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "assumption.record",
                {
                    "claim": "在声称 USB CDC 驱动可用前，必须确认实际板子的晶振、USB D+/D- 连接、上拉实现以及启动/调试接线。",
                    "scope": "STM32F103C8T6 最小系统板 USB CDC 验证",
                    "reason": "这些值取决于具体板子，不能只根据 MCU 型号安全推断。",
                    "risk": "错误的时钟或 USB 电气假设可能让固件能编译，但在真实硬件上无法枚举。",
                    "status": "needs_human_confirmation",
                    "requires_user_confirmation": True,
                },
            )
            return

        self.gateway.invoke(
            task_id,
            "researcher",
            "evidence.record",
            {
                "claim": "工作流状态来自用户提交的任务目标。",
                "source_type": "user_request",
                "source_title": "任务目标",
                "url": None,
                "version_or_date": None,
                "section_or_page": "任务载荷",
                "confidence": "medium",
                "notes": researcher_output[:800],
            },
        )

    def _record_executor_outputs(
        self,
        task_id: str,
        goal: str,
        executor_output: str,
    ) -> None:
        if self._is_stm32_usb_goal(goal):
            self.gateway.invoke(
                task_id,
                "executor",
                "cubemx.plan",
                {
                    "target": "stm32f103c8t6_usb_cdc",
                    "changes": [
                        "启用 USB Device 外设 Full Speed 模式",
                        "启用 USB CDC Class 中间件",
                        "要求根据真实板子确认时钟树",
                        "提供 .ioc 路径后用官方 STM32Cube 工具生成工程",
                    ],
                    "agent_summary": executor_output[:800],
                },
            )
            self.gateway.invoke(
                task_id,
                "executor",
                "artifact.create",
                {
                    "kind": "firmware_plan",
                    "title": "STM32 USB CDC 实现计划",
                    "path": "generated/stm32-usb-cdc-plan.md",
                    "metadata": {
                        "target": "STM32F103C8T6",
                        "toolchain": "STM32Cube 官方工具链",
                        "status": "ready_for_ioc_binding",
                        "summary": executor_output,
                    },
                },
            )
            return

        self.gateway.invoke(
            task_id,
            "executor",
            "artifact.create",
            {
                "kind": "execution_summary",
                "title": "执行摘要",
                "path": "generated/execution-summary.md",
                "metadata": {"status": "simulated", "summary": executor_output},
            },
        )

    def _record_reviewer_outputs(
        self,
        task_id: str,
        goal: str,
        reviewer_output: str,
        retry_attempt: int = 0,
    ) -> ReviewDecision:
        if self._is_stm32_usb_goal(goal):
            self.gateway.invoke(
                task_id,
                "reviewer",
                "hardware.validation",
                {
                    "name": "真实 STM32F103C8T6 板子的 USB CDC 枚举",
                    "status": "not_run",
                    "evidence": "当前运行没有提供真实板子、.ioc 文件、构建日志、烧录日志或主机 USB 枚举日志。",
                },
            )
            self.gateway.invoke(
                task_id,
                "reviewer",
                "review.record",
                {
                    "status": "needs_human",
                    "summary": "实现意图已经记录，但板级假设和硬件验证尚未解决。",
                    "checks": [
                        {
                            "name": "official_sources",
                            "status": "warn",
                            "detail": "已记录官方来源候选清单，但尚未下载/摘录具体章节并绑定到每个芯片/工具事实。",
                        },
                        {
                            "name": "hardware_success",
                            "status": "fail",
                            "detail": "尚未在真实板子上测试 USB CDC 枚举。",
                        },
                        {
                            "name": "reviewer_agent_output",
                            "status": "recorded",
                            "detail": reviewer_output[:800],
                        },
                    ],
                    "retry_instructions": "请提供 .ioc 路径和板级验证证据；或者批准显式假设，仅生成草案性质的最终报告。",
                },
            )
            return REVIEW_NEEDS_HUMAN

        self.gateway.invoke(
            task_id,
            "reviewer",
            "review.record",
            {
                "status": "passed",
                "summary": "Smoke 工作流已经产出证据、工具调用记录和产物。",
                "checks": [
                    {
                        "name": "audit_records",
                        "status": "pass",
                        "detail": "本次运行已产出事件并持久化任务详情。",
                    },
                    {
                        "name": "reviewer_agent_output",
                        "status": "recorded",
                        "detail": reviewer_output[:800],
                    },
                    {
                        "name": "retry_attempt",
                        "status": "recorded",
                        "detail": str(retry_attempt),
                    },
                ],
            },
        )
        return REVIEW_PASSED

    def _agent_context(
        self,
        task_id: str,
        goal: str,
        agent_name: str,
        prior_output: str,
        skills: list[WorkflowSkill] | None = None,
    ) -> str:
        detail = self.store.task_detail(task_id)
        if detail is None:
            return "\n".join(
                [
                    f"任务目标：{goal}",
                    f"当前 Agent：{agent_name}",
                    format_skill_context(skills or [], agent_name),
                ]
            )

        event_count = len(detail.events)
        tool_count = len(detail.tool_calls)
        artifact_count = len(detail.artifacts)
        evidence_count = len(detail.evidence)
        assumption_count = len(detail.assumptions)
        review_count = len(detail.reviews)
        hardware_count = len(detail.hardware_validations)

        return "\n".join(
            [
                f"任务目标：{goal}",
                f"当前 Agent：{agent_name}",
                f"事件数：{event_count}",
                f"工具调用数：{tool_count}",
                f"产物数：{artifact_count}",
                f"证据数：{evidence_count}",
                f"假设数：{assumption_count}",
                f"审查记录数：{review_count}",
                f"硬件验证记录数：{hardware_count}",
                format_skill_context(skills or [], agent_name),
                OUTPUT_PROTOCOL,
                "上一个 Agent 输出摘要：",
                prior_output[:2000],
                "约束：不要声称未验证硬件已经成功；硬件事实必须有官方来源或实测证据。",
                "输出要求：必须输出可见中文文本，不能只进行 thinking。",
            ]
        )

    def _skills_from_artifacts(self, task_id: str) -> list[WorkflowSkill]:
        detail = self.store.task_detail(task_id)
        if detail is None:
            return []
        skill_ids: set[str] = set()
        for artifact in detail.artifacts:
            if artifact.kind != "skill_selection":
                continue
            for skill in artifact.metadata.get("skills", []):
                skill_id = skill.get("id")
                if skill_id:
                    skill_ids.add(skill_id)
        if not skill_ids:
            return []
        return workflow_skills_by_id(skill_ids)

    def _should_stop(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if task is None:
            return True
        if task.status == TaskStatus.CANCEL_REQUESTED:
            self.store.cancel_task(task_id)
            self.store.append_event(task_id, "task.cancelled", {"status": "cancelled"})
            return True
        return task.status in {TaskStatus.CANCELLED, TaskStatus.FAILED}

    @staticmethod
    def _is_stm32_usb_goal(goal: str) -> bool:
        normalized = goal.lower()
        return "stm32" in normalized and ("usb" in normalized or "cdc" in normalized)
