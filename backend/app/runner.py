from .llm import LLMClient
from .models import TaskStatus
from .storage import TaskStore
from .tools.gateway import ToolGateway


AGENT_SEQUENCE = ["manager", "planner", "researcher", "executor", "reviewer", "writer"]


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
            await self._manager(task_id)
            if self._should_stop(task_id):
                return
            await self._planner(task_id, task.goal)
            if self._should_stop(task_id):
                return
            await self._researcher(task_id, task.goal)
            if self._should_stop(task_id):
                return
            await self._executor(task_id, task.goal)
            if self._should_stop(task_id):
                return
            should_wait = await self._reviewer(task_id, task.goal)
            if should_wait:
                self.store.set_waiting_for_human(task_id, "reviewer")
                self.store.append_event(
                    task_id,
                    "approval.required",
                    {
                        "agent": "reviewer",
                        "reason": "审查员发现仍有未验证的硬件假设。",
                    },
                )
                return
            if self._should_stop(task_id):
                return
            await self._writer(task_id)
            self.store.complete_task(task_id)
            self.store.append_event(task_id, "task.completed", {"status": "completed"})
        except Exception as exc:
            self.store.fail_task(task_id, str(exc))
            self.store.append_event(
                task_id,
                "agent.failed",
                {"agent": self.store.get_task(task_id).current_agent if self.store.get_task(task_id) else None, "error": str(exc)},
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
            self.store.record_review(
                task_id=task_id,
                status="rejected_by_human",
                summary="人工审查打回了当前运行。",
                checks=[
                    {
                        "name": "human_gate",
                        "status": "fail",
                        "detail": notes or "没有提供打回说明。",
                    }
                ],
                retry_instructions=notes or "请修改任务指令后重新运行。",
            )
            self.store.fail_task(task_id, "人工审查打回了当前运行。")
            self.store.append_event(
                task_id,
                "task.failed",
                {"error": "人工审查打回了当前运行。"},
            )
            return

        self.store.confirm_pending_assumptions(task_id)
        self.store.resume_task(task_id, "writer")
        await self._writer(
            task_id,
            extra_note="人工已批准显式假设；实际板级测试仍未标记为通过。",
        )
        self.store.complete_task(task_id)
        self.store.append_event(task_id, "task.completed", {"status": "completed"})

    async def _manager(self, task_id: str) -> None:
        await self._run_static_agent(
            task_id,
            "manager",
            [
                "已接收任务范围。\n",
                "已选择固定流程：规划员 -> 研究员 -> 执行员 -> 审查员 -> 撰写员。\n",
            ],
            "工作流路径已固定，状态已初始化。",
        )

    async def _planner(self, task_id: str, goal: str) -> None:
        self.store.set_current_agent(task_id, "planner")
        self.store.append_event(task_id, "agent.started", {"agent": "planner"})
        output = []
        async for token in self.llm.stream_planner(goal):
            output.append(token)
            self.store.append_event(
                task_id,
                "agent.token",
                {"agent": "planner", "token": token},
            )
        planner_output = "".join(output)
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
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "planner", "summary": planner_output},
        )

    async def _researcher(self, task_id: str, goal: str) -> None:
        await self._start_agent(task_id, "researcher")
        if self._is_stm32_usb_goal(goal):
            self._emit_token(
                task_id,
                "researcher",
                "正在记录 STM32 USB CDC 任务的来源要求和显式未知项。\n",
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
                    "notes": "这是本项目的本地要求，不是芯片事实。",
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
        else:
            self._emit_token(task_id, "researcher", "正在记录任务范围证据。\n")
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
                    "notes": "这次 smoke 运行不需要外部技术事实。",
                },
            )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "researcher", "summary": "证据和假设已记录。"},
        )

    async def _executor(self, task_id: str, goal: str) -> None:
        await self._start_agent(task_id, "executor")
        if self._is_stm32_usb_goal(goal):
            self._emit_token(
                task_id,
                "executor",
                "正在准备 STM32F103C8T6 USB CDC 的 CubeMX 配置意图；当前不会修改缺失的 .ioc 文件。\n",
            )
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
                    },
                },
            )
        else:
            self._emit_token(task_id, "executor", "正在创建工作流摘要产物。\n")
            self.gateway.invoke(
                task_id,
                "executor",
                "artifact.create",
                {
                    "kind": "execution_summary",
                    "title": "执行摘要",
                    "path": "generated/execution-summary.md",
                    "metadata": {"status": "simulated"},
                },
            )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "executor", "summary": "执行产物已记录。"},
        )

    async def _reviewer(self, task_id: str, goal: str) -> bool:
        await self._start_agent(task_id, "reviewer")
        if self._is_stm32_usb_goal(goal):
            self._emit_token(
                task_id,
                "reviewer",
                "必须完成硬件验证后，才能把这个任务标记为完成。\n",
            )
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
                            "detail": "目前只记录了项目要求；生成代码前必须从官方文档获取芯片和工具事实。",
                        },
                        {
                            "name": "hardware_success",
                            "status": "fail",
                            "detail": "尚未在真实板子上测试 USB CDC 枚举。",
                        },
                    ],
                    "retry_instructions": "请提供 .ioc 路径和板级验证证据；或者批准显式假设，仅生成草案性质的最终报告。",
                },
            )
            self.store.append_event(
                task_id,
                "agent.completed",
                {"agent": "reviewer", "summary": "进入撰写员前需要人工批准。"},
            )
            return True

        self._emit_token(task_id, "reviewer", "工作流 smoke 运行审查通过。\n")
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
                    }
                ],
            },
        )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "reviewer", "summary": "审查通过。"},
        )
        return False

    async def _writer(self, task_id: str, extra_note: str = "") -> None:
        await self._start_agent(task_id, "writer")
        self._emit_token(task_id, "writer", "正在撰写最终任务报告。\n")
        metadata = {"status": "complete"}
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
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "writer", "summary": "最终报告已记录。"},
        )

    async def _run_static_agent(
        self,
        task_id: str,
        agent_name: str,
        tokens: list[str],
        summary: str,
    ) -> None:
        await self._start_agent(task_id, agent_name)
        for token in tokens:
            self._emit_token(task_id, agent_name, token)
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": agent_name, "summary": summary},
        )

    async def _start_agent(self, task_id: str, agent_name: str) -> None:
        self.store.set_current_agent(task_id, agent_name)
        self.store.append_event(task_id, "agent.started", {"agent": agent_name})

    def _emit_token(self, task_id: str, agent_name: str, token: str) -> None:
        self.store.append_event(
            task_id,
            "agent.token",
            {"agent": agent_name, "token": token},
        )

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
