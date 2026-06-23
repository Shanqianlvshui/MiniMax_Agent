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
                        "reason": "Reviewer found unverified hardware assumptions.",
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
                summary="Human reviewer rejected the current run.",
                checks=[
                    {
                        "name": "human_gate",
                        "status": "fail",
                        "detail": notes or "No rejection notes were provided.",
                    }
                ],
                retry_instructions=notes or "Revise task instructions and run again.",
            )
            self.store.fail_task(task_id, "Human reviewer rejected the current run.")
            self.store.append_event(
                task_id,
                "task.failed",
                {"error": "Human reviewer rejected the current run."},
            )
            return

        self.store.confirm_pending_assumptions(task_id)
        self.store.resume_task(task_id, "writer")
        await self._writer(
            task_id,
            extra_note="Human approved the explicit assumptions. Hardware board testing is still not marked as passed.",
        )
        self.store.complete_task(task_id)
        self.store.append_event(task_id, "task.completed", {"status": "completed"})

    async def _manager(self, task_id: str) -> None:
        await self._run_static_agent(
            task_id,
            "manager",
            [
                "Scope accepted.\n",
                "Fixed workflow selected: Planner -> Researcher -> Executor -> Reviewer -> Writer.\n",
            ],
            "Workflow route fixed and state initialized.",
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
                "title": "Planner task breakdown",
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
                "Recording source requirements and explicit unknowns for STM32 USB CDC work.\n",
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "evidence.record",
                {
                    "claim": "MiniMax Agent should use official or primary sources for hardware-development facts.",
                    "source_type": "project_requirement",
                    "source_title": "Evidence-driven workflow requirement from user",
                    "url": None,
                    "version_or_date": None,
                    "section_or_page": "conversation",
                    "confidence": "high",
                    "notes": "This is a local project requirement, not a silicon fact.",
                },
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "evidence.record",
                {
                    "claim": "STM32Cube tooling is the required configuration/generation path for this demo.",
                    "source_type": "project_requirement",
                    "source_title": "STM32CubeIDE / STM32CubeMX workflow requirement from user",
                    "url": None,
                    "version_or_date": None,
                    "section_or_page": "conversation",
                    "confidence": "high",
                    "notes": "The runner records this as the requested toolchain constraint.",
                },
            )
            self.gateway.invoke(
                task_id,
                "researcher",
                "assumption.record",
                {
                    "claim": "Board oscillator, USB D+/D- wiring, pull-up implementation, and boot/debug wiring must be confirmed on the actual board before claiming a working driver.",
                    "scope": "STM32F103C8T6 minimum-system board USB CDC validation",
                    "reason": "Those values depend on the physical board and cannot be inferred safely from the MCU part number alone.",
                    "risk": "Wrong clock or USB electrical assumptions can produce firmware that compiles but fails enumeration on real hardware.",
                    "status": "needs_human_confirmation",
                    "requires_user_confirmation": True,
                },
            )
        else:
            self._emit_token(task_id, "researcher", "Recording task scope evidence.\n")
            self.gateway.invoke(
                task_id,
                "researcher",
                "evidence.record",
                {
                    "claim": "The workflow state is derived from the submitted task goal.",
                    "source_type": "user_request",
                    "source_title": "Task goal",
                    "url": None,
                    "version_or_date": None,
                    "section_or_page": "task payload",
                    "confidence": "medium",
                    "notes": "No external technical claims were needed for this smoke run.",
                },
            )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "researcher", "summary": "Evidence and assumptions recorded."},
        )

    async def _executor(self, task_id: str, goal: str) -> None:
        await self._start_agent(task_id, "executor")
        if self._is_stm32_usb_goal(goal):
            self._emit_token(
                task_id,
                "executor",
                "Preparing CubeMX intent for STM32F103C8T6 USB CDC without mutating a missing .ioc file.\n",
            )
            self.gateway.invoke(
                task_id,
                "executor",
                "cubemx.plan",
                {
                    "target": "stm32f103c8t6_usb_cdc",
                    "changes": [
                        "Enable USB device peripheral in Full Speed mode",
                        "Enable USB CDC class middleware",
                        "Require clock tree confirmation from the real board",
                        "Generate project with official STM32Cube tooling after .ioc path is provided",
                    ],
                },
            )
            self.gateway.invoke(
                task_id,
                "executor",
                "artifact.create",
                {
                    "kind": "firmware_plan",
                    "title": "STM32 USB CDC implementation plan",
                    "path": "generated/stm32-usb-cdc-plan.md",
                    "metadata": {
                        "target": "STM32F103C8T6",
                        "toolchain": "STM32Cube official tooling",
                        "status": "ready_for_ioc_binding",
                    },
                },
            )
        else:
            self._emit_token(task_id, "executor", "Creating workflow summary artifact.\n")
            self.gateway.invoke(
                task_id,
                "executor",
                "artifact.create",
                {
                    "kind": "execution_summary",
                    "title": "Execution summary",
                    "path": "generated/execution-summary.md",
                    "metadata": {"status": "simulated"},
                },
            )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "executor", "summary": "Execution artifacts recorded."},
        )

    async def _reviewer(self, task_id: str, goal: str) -> bool:
        await self._start_agent(task_id, "reviewer")
        if self._is_stm32_usb_goal(goal):
            self._emit_token(
                task_id,
                "reviewer",
                "Hardware validation is required before this can be marked done.\n",
            )
            self.gateway.invoke(
                task_id,
                "reviewer",
                "hardware.validation",
                {
                    "name": "USB CDC enumeration on physical STM32F103C8T6 board",
                    "status": "not_run",
                    "evidence": "No board, .ioc file, build log, flash log, or host USB enumeration log was provided to this runner.",
                },
            )
            self.gateway.invoke(
                task_id,
                "reviewer",
                "review.record",
                {
                    "status": "needs_human",
                    "summary": "Implementation intent is recorded, but board-specific assumptions and hardware validation are unresolved.",
                    "checks": [
                        {
                            "name": "official_sources",
                            "status": "warn",
                            "detail": "Only project requirements are recorded so far; silicon/tool facts must be pulled from official docs before code generation.",
                        },
                        {
                            "name": "hardware_success",
                            "status": "fail",
                            "detail": "USB CDC enumeration has not been tested on the physical board.",
                        },
                    ],
                    "retry_instructions": "Provide the .ioc path and board validation evidence, or approve the explicit assumptions to produce a draft-only final report.",
                },
            )
            self.store.append_event(
                task_id,
                "agent.completed",
                {"agent": "reviewer", "summary": "Needs human approval before Writer."},
            )
            return True

        self._emit_token(task_id, "reviewer", "Review passed for workflow smoke run.\n")
        self.gateway.invoke(
            task_id,
            "reviewer",
            "review.record",
            {
                "status": "passed",
                "summary": "Smoke workflow produced evidence, tool-call records, and artifacts.",
                "checks": [
                    {
                        "name": "audit_records",
                        "status": "pass",
                        "detail": "The run produced events and persisted task details.",
                    }
                ],
            },
        )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "reviewer", "summary": "Review passed."},
        )
        return False

    async def _writer(self, task_id: str, extra_note: str = "") -> None:
        await self._start_agent(task_id, "writer")
        self._emit_token(task_id, "writer", "Writing final task report.\n")
        metadata = {"status": "complete"}
        if extra_note:
            metadata["note"] = extra_note
        self.gateway.invoke(
            task_id,
            "writer",
            "artifact.create",
            {
                "kind": "final_report",
                "title": "Final workflow report",
                "path": "generated/final-report.md",
                "metadata": metadata,
            },
        )
        self.store.append_event(
            task_id,
            "agent.completed",
            {"agent": "writer", "summary": "Final report recorded."},
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
