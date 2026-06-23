# ADR-0001: Use mmx-cli as a Tool Provider, Not the Core Orchestrator

## Status

Accepted

## Context

MiniMax provides official tooling for agent workflows:

- `mmx-cli` exposes MiniMax multimodal capabilities from the command line.
- Mini-Agent demonstrates a complete MiniMax-M3 agent loop with filesystem tools, shell tools, session notes, context summarization, Claude Skills, MCP support, and detailed logs.

This project also needs a strict workflow runtime:

```text
Manager -> Planner -> Researcher -> Executor -> Reviewer -> Writer
```

The workflow must enforce per-agent permissions, evidence gates, assumption tracking, reviewer retry semantics, GUI-visible state, durable audit logs, and hardware validation gates.

## Decision

The core orchestrator remains project-owned:

```text
FastAPI + LangGraph + SQLite + SSE
```

MiniMax-M3 model calls are made directly through the MiniMax Anthropic-compatible API for workflow agents.

`mmx-cli` is integrated as a controlled tool provider behind the project `ToolGateway`.

Mini-Agent is not used as the primary runtime for v1. It may be used later as a reference implementation or as a controlled worker for delegated subtasks.

## Rationale

Mini-Agent owns its own agent loop. Using it as the primary runtime would make it harder to guarantee:

- Which workflow node performed an action.
- Whether a critical claim has evidence.
- Whether an assumption was confirmed.
- Whether Reviewer can reliably block or retry Executor.
- Whether tool permissions were enforced by the project policy.
- Whether every tool call is persisted in the project audit model.

`mmx-cli` is a better fit for the tool layer because it provides official MiniMax capabilities while allowing this system to keep control of:

- Capability checks.
- Approval gates.
- Tool-call persistence.
- SSE events.
- Artifact registration.
- GUI inspection.

## Consequences

V1 should add an `MmxCliToolProvider` with narrowly scoped commands first:

```text
mmx auth status
mmx quota
```

Future slices may add:

```text
mmx search query
mmx vision describe
mmx image generate
mmx video generate
mmx speech synthesize
mmx music generate
```

Generation and external-write commands must go through risk classification and approval before execution.
