from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WorkflowSkill:
    id: str
    name: str
    trigger: str
    agents: tuple[str, ...]
    protocol: str
    acceptance_checks: tuple[str, ...]

    def to_record(self) -> dict:
        return asdict(self)


SKILL_REGISTRY: tuple[WorkflowSkill, ...] = (
    WorkflowSkill(
        id="skill-router",
        name="Skill Router",
        trigger="Always active. Select the smallest useful skill set before work starts.",
        agents=("manager", "planner"),
        protocol=(
            "先选择少量适用技能，再把技能摘要传给后续 Agent；不要把所有流程规则"
            "无差别塞进上下文。"
        ),
        acceptance_checks=(
            "任务开始时必须有技能选择记录。",
            "Planner 只按已选技能扩展步骤。",
        ),
    ),
    WorkflowSkill(
        id="grill-before-risky-work",
        name="Grill Before Risky Work",
        trigger="Risky hardware, ambiguous scope, production parameters, or missing board facts.",
        agents=("manager", "planner", "reviewer"),
        protocol=(
            "先暴露不确定性和必须追问的问题；缺少答案时进入人工确认或明确标记为假设。"
        ),
        acceptance_checks=(
            "关键假设必须进入 assumption ledger。",
            "Reviewer 不能让未确认假设伪装成已验证事实。",
        ),
    ),
    WorkflowSkill(
        id="domain-language",
        name="Domain Language",
        trigger="Tasks with domain terms, board names, chips, registers, protocols, or workflow jargon.",
        agents=("manager", "planner", "writer"),
        protocol=(
            "使用一致术语描述对象和状态；输出里保留目标芯片、工具链、接口、验证门禁等"
            "规范名称。"
        ),
        acceptance_checks=(
            "最终产物使用同一套术语。",
            "不得用含糊词替代芯片、接口和工具链名称。",
        ),
    ),
    WorkflowSkill(
        id="tdd-feedback-loop",
        name="TDD Feedback Loop",
        trigger="Implementation, driver, bug fix, or testable software changes.",
        agents=("planner", "executor", "reviewer"),
        protocol=(
            "把工作拆成可测试纵切片；能自动化验证的地方先定义失败用例，再实现，再回归。"
        ),
        acceptance_checks=(
            "Planner 必须写出验证标准。",
            "Reviewer 必须指出缺失测试或不可自动化验证的风险。",
        ),
    ),
    WorkflowSkill(
        id="evidence-first-research",
        name="Evidence First Research",
        trigger="Hardware facts, chip data, register behavior, official SDKs, errata, or toolchain behavior.",
        agents=("researcher", "executor", "reviewer", "writer"),
        protocol=(
            "硬件和工具事实优先查官方或一手来源；第三方资料只能作为线索；无法联网或未查证"
            "时必须说清楚。"
        ),
        acceptance_checks=(
            "证据记录必须区分官方、用户要求、假设和实测。",
            "未验证硬件行为不能写成成功。",
        ),
    ),
    WorkflowSkill(
        id="deep-module-design",
        name="Deep Module Design",
        trigger="Architecture, adapter, API, orchestration, or tool gateway changes.",
        agents=("planner", "executor", "reviewer"),
        protocol=(
            "把复杂行为藏在清晰接口后面；优先复用既有边界；避免让 Agent 直接绕过编排层。"
        ),
        acceptance_checks=(
            "新能力必须有明确接口和调用边界。",
            "Reviewer 必须检查是否扩大了权限或绕过审计。",
        ),
    ),
    WorkflowSkill(
        id="review-findings-first",
        name="Review Findings First",
        trigger="Always active before final output.",
        agents=("reviewer", "writer"),
        protocol=(
            "审查先列阻塞项、证据缺口和剩余风险，再给摘要；Writer 只能总结已通过或已标注的内容。"
        ),
        acceptance_checks=(
            "Reviewer 输出必须包含通过、警告或失败状态。",
            "Writer 不能把 Reviewer 的警告降级成成功结论。",
        ),
    ),
)


ALWAYS_ON_SKILLS = {"skill-router", "review-findings-first"}

HARDWARE_TERMS = {
    "stm32",
    "usb",
    "cdc",
    "chip",
    "register",
    "寄存器",
    "芯片",
    "硬件",
    "板子",
    "cube",
    "cubemx",
    "cubeide",
    "driver",
    "驱动",
    "fpga",
    "vivado",
}

IMPLEMENTATION_TERMS = {
    "implement",
    "build",
    "develop",
    "fix",
    "driver",
    "test",
    "实现",
    "开发",
    "修复",
    "测试",
    "驱动",
}

DESIGN_TERMS = {
    "architecture",
    "adapter",
    "api",
    "workflow",
    "orchestration",
    "toolgateway",
    "架构",
    "接口",
    "编排",
    "设计",
    "适配",
}

AMBIGUITY_TERMS = {
    "假设",
    "不确定",
    "生产",
    "参数",
    "最小系统",
    "实际",
    "验证",
    "risk",
}


def select_workflow_skills(goal: str) -> list[WorkflowSkill]:
    normalized = goal.lower()
    selected_ids = set(ALWAYS_ON_SKILLS)
    if _contains_any(normalized, HARDWARE_TERMS):
        selected_ids.update(
            {
                "evidence-first-research",
                "grill-before-risky-work",
                "domain-language",
            }
        )
    if _contains_any(normalized, IMPLEMENTATION_TERMS):
        selected_ids.add("tdd-feedback-loop")
    if _contains_any(normalized, DESIGN_TERMS):
        selected_ids.add("deep-module-design")
    if _contains_any(normalized, AMBIGUITY_TERMS):
        selected_ids.add("grill-before-risky-work")

    return [skill for skill in SKILL_REGISTRY if skill.id in selected_ids]


def skill_selection_record(goal: str, skills: list[WorkflowSkill]) -> dict:
    return {
        "source": "mattpocock/skills philosophy",
        "selection_reason": (
            "Use small, composable, task-routed engineering skills; preserve user control "
            "and keep reusable discipline outside the main prompt."
        ),
        "goal_excerpt": goal[:240],
        "skills": [skill.to_record() for skill in skills],
    }


def workflow_skills_by_id(skill_ids: set[str]) -> list[WorkflowSkill]:
    return [skill for skill in SKILL_REGISTRY if skill.id in skill_ids]


def format_skill_context(skills: list[WorkflowSkill], agent_name: str) -> str:
    applicable = [
        skill
        for skill in skills
        if agent_name in skill.agents or skill.id in ALWAYS_ON_SKILLS
    ]
    if not applicable:
        return "无适用于当前 Agent 的 Workflow Skills。"

    lines = ["Workflow Skills（只列当前 Agent 需要执行的纪律）："]
    for skill in applicable:
        checks = "；".join(skill.acceptance_checks)
        lines.append(f"- {skill.name} ({skill.id})：{skill.protocol} 验收：{checks}")
    return "\n".join(lines)


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)
