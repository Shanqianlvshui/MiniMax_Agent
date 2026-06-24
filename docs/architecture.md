# Architecture: MiniMax-M3 Multi-Agent Workflow System

## System Overview

The system has a FastAPI backend, a Next.js/React frontend, a LangGraph workflow runtime, MiniMax-M3 LLM calls through MiniMax `anthropic/v1`, SSE event streaming, and SQLite persistence.

```text
Frontend
  -> FastAPI REST APIs
  -> SSE task events

FastAPI
  -> TaskRunner
  -> WorkflowSkillRegistry
  -> LangGraph workflow
  -> LLMClient
  -> ToolGateway
  -> SQLite
```

## Backend Layout

```text
/backend
  /app
    main.py              # FastAPI entrypoint
    graph.py             # LangGraph workflow
    agents.py            # Manager/Planner/Researcher/Executor/Reviewer/Writer
    llm.py               # MiniMax-M3 anthropic/v1 client
    tools.py             # controlled tool gateway
    events.py            # SSE event stream
    storage.py           # SQLite persistence
    workflow_skills.py   # task-routed engineering skill selection
    evidence.py          # EvidencePolicy, SourceVerifier, AssumptionLedger
    cubemx.py            # controlled .ioc edits and CubeMX CLI integration
    hardware.py          # board validation and serial test helpers
```

## Frontend Layout

```text
/frontend
  /app
    page.tsx             # task console
  /components
    TaskInput.tsx
    AgentGraph.tsx       # React Flow
    AgentLogPanel.tsx
    ToolCallList.tsx
    ArtifactPanel.tsx
    ReviewPanel.tsx
    EvidencePanel.tsx
    AssumptionPanel.tsx
    HardwareValidationPanel.tsx
```

## API Surface

```text
POST /tasks
GET  /tasks/{id}
GET  /tasks/{id}/events
POST /tasks/{id}/cancel
POST /tasks/{id}/approval
POST /tasks/{id}/human-input
```

FastAPI owns lifecycle, persistence, cancellation, approvals, and SSE.

LangGraph owns workflow transitions for a single task run.

Approval gates are persisted as task states. The graph is not expected to stay alive in memory while waiting for the user.

## Workflow Graph

```text
Manager
 -> skill_router
 -> Planner
 -> Researcher
 -> evidence_gate?
 -> Executor
 -> Reviewer
 -> Writer
```

Reviewer conditional edges:

```text
pass        -> Writer
fail        -> Executor if retry_count < 2
fail        -> needs_human if retry_count >= 2
needs_human -> waiting_human_input
```

Automatic retries return to Executor with targeted retry instructions. They do not rerun Planner/Researcher unless a human decision requests it.

## Workflow Skills

The system adopts the core idea from `mattpocock/skills`: skills are small,
task-routed engineering disciplines, not a giant prompt. Manager selects the
smallest useful skill set before work starts, records that choice through the
ToolGateway, and later agents receive only the skills relevant to their role.

Initial workflow skills:

```text
skill-router              Always-on skill selection discipline.
grill-before-risky-work   Force ambiguity and hardware assumptions into gates.
domain-language           Keep chip, board, protocol, and tool terms consistent.
tdd-feedback-loop         Prefer testable vertical slices and explicit checks.
evidence-first-research   Require official or first-hand evidence for hardware facts.
deep-module-design        Keep adapters, tools, and orchestration behind clean interfaces.
review-findings-first     Make Reviewer list blockers and risk before summary.
```

Skill selection is persisted as a `skill_selection` artifact and a
`workflow.skills.select` tool call. Skills do not grant tool permissions by
themselves; ToolGateway remains the authority for what each agent may execute.

## LLM Client

Agents call a provider-neutral interface:

```python
llm.complete(
    agent_name: str,
    messages: list[dict],
    response_schema: dict | None = None,
    stream: bool = True,
)
```

V1 default implementation:

```text
MiniMaxAnthropicAdapter -> /anthropic/v1/messages
```

Configuration:

```text
MINIMAX_BASE_URL=
MINIMAX_API_KEY=
MINIMAX_MODEL=MiniMax-M3
MINIMAX_SUBSCRIPTION_KEY=
```

Each call records:

```text
provider
model
request_id
latency_ms
prompt_tokens
completion_tokens
streamed
```

Token streaming is for GUI display and logs only. Workflow state is updated only from the completed parsed agent output.

## Workflow State

Workflow state contains compact, structured summaries only:

```text
task_goal
plan
research_summary
execution_summary
review
final_output
artifact_refs
evidence_refs
assumption_refs
counters
```

Raw logs and bulky output stay in SQLite or artifact files:

```text
raw_agent_logs
raw_tool_stdout
raw_tool_stderr
full_file_snapshots
full_search_results
SSE event history
```

Each agent must produce `summary_for_next_agent`.

## Tool Gateway

Tools are accessed only through a policy-enforced gateway:

```text
agent_name + tool_name + args
 -> capability policy check
 -> risk classification
 -> approval gate if needed
 -> execution
 -> durable tool_call record
 -> SSE events
```

Initial capabilities:

```python
AGENT_CAPABILITIES = {
    "planner": {"read_context"},
    "researcher": {
        "list_files",
        "read_file",
        "search_files",
        "search_web",
        "download_reference",
        "verify_source"
    },
    "executor": {
        "list_files",
        "read_file",
        "apply_patch",
        "cubemx_update_config",
        "cubemx_generate",
        "run_build",
        "run_flash",
        "run_serial_test"
    },
    "reviewer": {
        "list_files",
        "read_file",
        "read_diff",
        "read_tool_logs",
        "read_evidence",
        "run_build",
        "run_serial_test"
    },
    "writer": {
        "read_review",
        "read_evidence",
        "write_artifact"
    },
}
```

Shell is not exposed as a generic default tool. Specific commands are wrapped as named tools.

## File Modification Policy

Project source changes go through `apply_patch`.

CubeMX configuration changes go through `cubemx_update_config`, not generic patching.

New reports and manifests may be written through `write_artifact`.

All file modifications record:

- Tool call.
- Patch or structured change intent.
- Touched files.
- Diff summary when Git is available.
- Degraded review marker when Git is not available.

## Git Handling

Git is recommended but not required.

If the workspace is a Git repo:

- Record `git diff --stat`.
- Reviewer uses `git diff` as primary evidence.

If not:

- Record touched files and patch text.
- Reviewer uses patch logs and file reads.
- GUI shows that diff review is degraded.

The system does not automatically run `git init`.

## Evidence Policy

Evidence is a first-class data type:

```json
{
  "claim": "...",
  "source_type": "official_datasheet | reference_manual | official_sdk | official_forum | upstream_issue | third_party",
  "source_title": "...",
  "url": "...",
  "local_path": "...",
  "version_or_date": "...",
  "section_or_page": "...",
  "confidence": "high | medium | low",
  "notes": "..."
}
```

SourceVerifier assigns source type and authority score using a vendor allowlist:

```text
reference/vendor-allowlist.yml
```

Search priority:

```text
1. Local /reference official docs
2. Official vendor docs and SDKs
3. Official forums and knowledge base
4. Upstream issues and discussions
5. Third-party sources as clues only
```

## Assumption Ledger

Assumptions are stored separately:

```json
{
  "id": "asm-1",
  "claim": "...",
  "scope": "...",
  "reason": "...",
  "risk": "...",
  "status": "unconfirmed | confirmed | rejected",
  "created_by": "researcher",
  "used_by": [],
  "requires_user_confirmation": true
}
```

Critical implementation cannot pass review while relying on unconfirmed assumptions.

## SQLite Model

SQLite is a state store plus append-only audit log, not a strict event-sourced system.

Core tables:

```text
tasks
agent_runs
tool_calls
artifacts
reviews
events
evidence
assumptions
hardware_validations
```

State recovery reads `tasks` plus latest related rows. It does not replay all events to reconstruct state.

## SSE Events

V1 uses SSE only. No WebSocket.

Event types:

```text
task.created
task.started
task.cancel_requested
task.cancelled
task.completed
task.failed
agent.started
agent.token
agent.completed
agent.failed
tool.started
tool.completed
tool.failed
evidence.created
assumption.created
approval.required
review.completed
artifact.created
hardware.validation_updated
```

V1 does not require full event replay. On refresh, frontend loads current task state and then subscribes to new events.

## CubeMX Integration

ST's official STM32CubeMX supports command-line/script modes for loading configurations, saving `.ioc`, and generating code/projects.

V1 does not assume the official CLI exposes complete semantic commands for all peripheral/middleware edits.

The system uses:

```text
controlled .ioc modifier
 -> STM32CubeMX CLI generation
 -> generated diff classification
 -> Reviewer checks
```

`.ioc` edits are not arbitrary. V1 only allows whitelisted STM32F103C8T6 USB CDC intents.

Future releases can expand through validated templates.

## Release Expansion Strategy

```text
v1:
  STM32F103C8T6 USB CDC only

v1.1:
  STM32F1 common peripheral templates: GPIO, USART, TIM, I2C, SPI

v1.2:
  Additional STM32 families with per-series capability maps

v1.3:
  Controlled new .ioc key proposals with dry-run, review, and human approval

v2:
  Semi-automatic CubeMX configuration planner
```

Never allowed:

- LLM arbitrary `.ioc` edits treated as trusted.
- Missing chip/package/board details being guessed.
- Production hardware parameters approved without evidence and validation.
