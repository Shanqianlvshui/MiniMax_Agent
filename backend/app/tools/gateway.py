from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..storage import TaskStore


@dataclass(frozen=True)
class GatewayResult:
    ok: bool
    summary: str
    stdout: str = ""
    stderr: str = ""


ToolHandler = Callable[[dict[str, Any]], GatewayResult]


class ToolGateway:
    def __init__(self, store: TaskStore):
        self.store = store
        self._permissions: dict[str, set[str]] = {
            "manager": {"workflow.skills.select"},
            "planner": {"artifact.plan"},
            "researcher": {"evidence.record", "assumption.record"},
            "executor": {"cubemx.plan", "artifact.create"},
            "reviewer": {"review.record", "hardware.validation"},
            "writer": {"artifact.create"},
        }
        self._handlers: dict[str, ToolHandler] = {
            "artifact.plan": self._record_plan_artifact,
            "artifact.create": self._record_artifact,
            "evidence.record": self._record_evidence,
            "assumption.record": self._record_assumption,
            "cubemx.plan": self._record_cubemx_plan,
            "review.record": self._record_review,
            "hardware.validation": self._record_hardware_validation,
            "workflow.skills.select": self._record_workflow_skills,
        }

    def invoke(
        self,
        task_id: str,
        agent_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> GatewayResult:
        if tool_name not in self._permissions.get(agent_name, set()):
            result = GatewayResult(
                ok=False,
                summary=f"{agent_name} 没有权限调用 {tool_name}",
            )
            self.store.record_tool_call(
                task_id=task_id,
                agent_name=agent_name,
                tool_name=tool_name,
                args=args,
                status="denied",
                result_summary=result.summary,
            )
            return result

        handler = self._handlers[tool_name]
        result = handler({"task_id": task_id, **args})
        self.store.record_tool_call(
            task_id=task_id,
            agent_name=agent_name,
            tool_name=tool_name,
            args=args,
            status="ok" if result.ok else "failed",
            result_summary=result.summary,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return result

    def _record_plan_artifact(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_artifact(
            task_id=args["task_id"],
            kind="plan",
            title=args["title"],
            path=args["path"],
            metadata={"summary": args.get("summary", "")},
        )
        return GatewayResult(ok=True, summary="计划产物已记录")

    def _record_workflow_skills(self, args: dict[str, Any]) -> GatewayResult:
        skills = args.get("skills", [])
        self.store.record_artifact(
            task_id=args["task_id"],
            kind="skill_selection",
            title="Workflow Skills 选择记录",
            path="generated/workflow-skills.json",
            metadata={
                "source": args.get("source", "workflow_skill_registry"),
                "selection_reason": args.get("selection_reason", ""),
                "goal_excerpt": args.get("goal_excerpt", ""),
                "skills": skills,
            },
        )
        return GatewayResult(
            ok=True,
            summary=f"已选择 {len(skills)} 个 Workflow Skills",
        )

    def _record_artifact(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_artifact(
            task_id=args["task_id"],
            kind=args["kind"],
            title=args["title"],
            path=args["path"],
            metadata=args.get("metadata", {}),
        )
        return GatewayResult(ok=True, summary="产物已记录")

    def _record_evidence(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_evidence(
            task_id=args["task_id"],
            claim=args["claim"],
            source_type=args["source_type"],
            source_title=args["source_title"],
            url=args.get("url"),
            version_or_date=args.get("version_or_date"),
            section_or_page=args.get("section_or_page"),
            confidence=args["confidence"],
            notes=args.get("notes", ""),
        )
        return GatewayResult(ok=True, summary="证据已记录")

    def _record_assumption(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_assumption(
            task_id=args["task_id"],
            claim=args["claim"],
            scope=args["scope"],
            reason=args["reason"],
            risk=args["risk"],
            status=args["status"],
            requires_user_confirmation=args["requires_user_confirmation"],
        )
        return GatewayResult(ok=True, summary="假设已记录")

    def _record_cubemx_plan(self, args: dict[str, Any]) -> GatewayResult:
        target = args.get("target", "")
        if target != "stm32f103c8t6_usb_cdc":
            return GatewayResult(
                ok=False,
                summary="CubeMX 计划目标不在 v1 白名单内",
            )
        self.store.record_artifact(
            task_id=args["task_id"],
            kind="cubemx_plan",
            title="CubeMX USB CDC 配置意图",
            path="generated/cubemx-intent.json",
            metadata={
                "target": target,
                "ioc_required": True,
                "changes": args.get("changes", []),
            },
        )
        return GatewayResult(
            ok=True,
            summary="CubeMX 配置意图已记录；未修改 .ioc 文件",
        )

    def _record_review(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_review(
            task_id=args["task_id"],
            status=args["status"],
            summary=args["summary"],
            checks=args["checks"],
            retry_instructions=args.get("retry_instructions"),
        )
        return GatewayResult(ok=True, summary="审查结果已记录")

    def _record_hardware_validation(self, args: dict[str, Any]) -> GatewayResult:
        self.store.record_hardware_validation(
            task_id=args["task_id"],
            name=args["name"],
            status=args["status"],
            evidence=args["evidence"],
        )
        return GatewayResult(ok=True, summary="硬件验证已记录")
