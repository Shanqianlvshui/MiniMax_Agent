# PRD: MiniMax-M3 Multi-Agent Workflow System

## Goal

Build a GUI-based multi-agent development workflow system for evidence-driven embedded development tasks.

The system should orchestrate multiple MiniMax-M3 agents through a fixed workflow, make every tool call and important claim reviewable, and support hardware-oriented tasks where stale or unsupported information is unacceptable.

## Target User

The primary user is an embedded developer working on chips, registers, board-level parameters, SDKs, and production hardware.

The user needs the system to:

- Avoid hallucinated or outdated technical facts.
- Prefer official vendor documentation and official SDK/code.
- Stop and ask when chip, package, board, revision, or toolchain details are missing.
- Preserve evidence, assumptions, tool calls, generated artifacts, and review results for later audit.
- Validate success against real hardware behavior when the task requires it.

## V1 Scope

V1 supports one explicit task type:

> Given a user development goal in the current workspace, complete a code/documentation/configuration task using controlled tools, evidence-backed research, MiniMax-M3 agents, and a fixed reviewable workflow.

The fixed workflow is:

```text
User Task
 -> Manager
 -> Planner
 -> Researcher
 -> Executor
 -> Reviewer
 -> Writer
 -> Final Output
```

The first end-to-end demo task is:

> Develop a USB CDC ACM virtual serial driver for an STM32F103C8T6 minimum system board using the official STM32 VS Code workflow.

## Non-Goals

V1 does not support:

- A drag-and-drop workflow editor.
- Arbitrary agent-defined workflows.
- Arbitrary MCP access.
- Arbitrary shell access.
- Unreviewed `.ioc` key/value edits.
- Fully automatic production PCB or BOM generation.
- Unconfirmed hardware assumptions being treated as facts.
- Direct production sign-off without real board validation.
- Token-stream replay as authoritative state recovery.

## Technology Stack

```text
Frontend: Next.js / React
Backend: FastAPI
Agent Runtime: LangGraph
LLM: MiniMax-M3 via MiniMax anthropic/v1
Realtime: SSE
Database: SQLite
Workflow UI: React Flow
Multimodal Tools: mmx-cli / MCP, added only through explicit capability gates
```

## GUI Requirements

V1 GUI includes:

- Task input.
- Start task.
- Request stop.
- Agent node status graph.
- Per-agent streaming token/log panel.
- Tool call list.
- Evidence panel.
- Assumptions/blockers panel.
- Reviewer result panel.
- Artifact list.
- Human approval and rejection actions.
- Hardware validation status for hardware tasks.

## Agent Roles

```text
Manager    controls task routing and lifecycle
Planner    produces structured plan and acceptance criteria
Researcher collects workspace context and authoritative external evidence
Executor   applies approved changes through controlled tools
Reviewer   checks diff, evidence, assumptions, validation, and policy
Writer     produces final report and artifact manifest
```

Only Executor can change project files or run state-changing commands.

Reviewer does not fix code directly. Reviewer returns pass/fail/needs_human and sends retry instructions back to Executor.

## Evidence Policy

For any fact that affects implementation, parameters, registers, board wiring, SDK/API behavior, or debug conclusions, the system must record evidence.

Accepted high-authority sources include:

- Official vendor datasheets.
- Official reference manuals.
- Official errata.
- Official application notes.
- Official SDK documentation.
- Official SDK repositories, examples, release notes.
- Official vendor forums or knowledge base.

Third-party blogs and forums may be used as clues, but they cannot be the sole basis for critical hardware or register facts.

If a critical fact has no reliable evidence, the task must enter a human gate instead of continuing as if the fact is true.

## Assumption Policy

Assumptions must be recorded separately from evidence.

An unconfirmed assumption cannot be used as a passing basis for:

- Register configuration.
- Clock configuration.
- Pin mapping.
- Electrical parameters.
- Board production parameters.
- Hardware validation conclusions.

The GUI must show assumptions and allow the user to confirm, reject, or provide missing details.

## Reviewer Protocol

Reviewer returns structured output:

```json
{
  "status": "pass | fail | needs_human",
  "summary": "...",
  "checks": [
    {
      "name": "matches_user_task",
      "status": "pass | fail | unknown",
      "evidence": "..."
    },
    {
      "name": "tests_or_validation",
      "status": "pass | fail | unknown",
      "evidence": "..."
    },
    {
      "name": "no_unapproved_tool_use",
      "status": "pass | fail | unknown",
      "evidence": "..."
    },
    {
      "name": "critical_claims_have_evidence",
      "status": "pass | fail | unknown",
      "evidence": "..."
    },
    {
      "name": "no_unconfirmed_critical_assumptions",
      "status": "pass | fail | unknown",
      "evidence": "..."
    }
  ],
  "retry_instructions": "required when status=fail"
}
```

Automatic retry returns to Executor only. Planner and Researcher are not rerun unless a human explicitly requests replanning or supplies new facts.

Maximum automatic retries: 2.

## Stop Semantics

The stop button means "request stop", not immediate guaranteed termination.

Behavior:

- Backend sets `cancel_requested=true`.
- Agents and tools check cancellation at safe boundaries.
- LLM streaming connections are closed.
- Short shell commands are terminated if possible, otherwise timeout.
- File writes are not partially rolled back.

V1 does not support arbitrary pause/resume. Approval gates are persisted and resumable, but user stop is cancellation.

## V1 Acceptance Criteria

The system is acceptable when:

- A task can be created from the GUI.
- The fixed workflow runs through Planner, Researcher, Executor, Reviewer, and Writer.
- MiniMax-M3 calls use the MiniMax `anthropic/v1` path.
- Token streaming appears in the GUI through SSE.
- Tool calls are recorded.
- Evidence and assumptions are visible in the GUI.
- Executor changes files only through approved tools.
- Reviewer can pass, fail, or request human input.
- SQLite stores tasks, agent runs, tool calls, artifacts, reviews, events, evidence, and assumptions.
- The STM32F103C8T6 USB CDC demo can produce firmware changes and distinguish build success from real-board validation success.
