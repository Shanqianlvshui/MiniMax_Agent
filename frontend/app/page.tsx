"use client";

import { FormEvent, ReactNode, useMemo, useState } from "react";
import {
  Background,
  Edge,
  MarkerType,
  Node,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";

type TaskStatus =
  | "running"
  | "waiting_human_input"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed";

type TaskRecord = {
  id: string;
  goal: string;
  status: TaskStatus;
  cancel_requested: boolean;
  current_agent: string | null;
  created_at: string;
  updated_at: string;
};

type TaskEvent = {
  seq: number;
  type: string;
  payload: {
    agent?: string;
    token?: string;
    summary?: string;
    status?: string;
    error?: string;
    reason?: string;
    decision?: string;
    notes?: string;
  };
  created_at: string;
};

type ToolCall = {
  id: number;
  agent_name: string;
  tool_name: string;
  args: Record<string, unknown>;
  status: string;
  result_summary: string;
  stdout: string;
  stderr: string;
  created_at: string;
};

type Artifact = {
  id: number;
  kind: string;
  title: string;
  path: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

type Review = {
  id: number;
  status: string;
  summary: string;
  checks: Array<Record<string, unknown>>;
  retry_instructions: string | null;
  created_at: string;
};

type Evidence = {
  id: number;
  claim: string;
  source_type: string;
  source_title: string;
  url: string | null;
  version_or_date: string | null;
  section_or_page: string | null;
  confidence: string;
  notes: string;
  created_at: string;
};

type Assumption = {
  id: number;
  claim: string;
  scope: string;
  reason: string;
  risk: string;
  status: string;
  requires_user_confirmation: boolean;
  created_at: string;
};

type HardwareValidation = {
  id: number;
  name: string;
  status: string;
  evidence: string;
  created_at: string;
};

type TaskDetail = {
  task: TaskRecord;
  events: TaskEvent[];
  tool_calls: ToolCall[];
  artifacts: Artifact[];
  reviews: Review[];
  evidence: Evidence[];
  assumptions: Assumption[];
  hardware_validations: HardwareValidation[];
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const agents = ["manager", "planner", "researcher", "executor", "reviewer", "writer"];

export default function TaskConsole() {
  const [goal, setGoal] = useState(
    "Develop STM32F103C8T6 USB CDC driver with CubeMX",
  );
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [logsByAgent, setLogsByAgent] = useState<Record<string, string>>({});
  const [selectedAgent, setSelectedAgent] = useState("planner");
  const [message, setMessage] = useState<string | null>(null);
  const [approvalNotes, setApprovalNotes] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const task = detail?.task ?? null;

  async function startTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setMessage(null);

    try {
      const response = await fetch(`${API_BASE_URL}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });

      if (!response.ok) {
        throw new Error(`Task creation failed with ${response.status}`);
      }

      const created = (await response.json()) as TaskRecord;
      setEvents([]);
      setLogsByAgent({});
      setDetail({
        task: created,
        events: [],
        tool_calls: [],
        artifacts: [],
        reviews: [],
        evidence: [],
        assumptions: [],
        hardware_validations: [],
      });
      subscribeToTaskEvents(created.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Task creation failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function refreshDetails(taskId = task?.id) {
    if (!taskId) {
      return;
    }

    setMessage(null);
    const response = await fetch(`${API_BASE_URL}/tasks/${taskId}/details`);
    if (!response.ok) {
      setMessage(`Refresh failed with ${response.status}`);
      return;
    }

    const nextDetail = (await response.json()) as TaskDetail;
    setDetail(nextDetail);
    setEvents(nextDetail.events);
    rebuildLogs(nextDetail.events);
  }

  function subscribeToTaskEvents(taskId: string) {
    const source = new EventSource(`${API_BASE_URL}/tasks/${taskId}/events`);

    [
      "task.started",
      "agent.started",
      "agent.token",
      "agent.completed",
      "agent.failed",
      "approval.required",
      "task.completed",
      "task.failed",
      "task.cancelled",
    ].forEach((type) => {
      source.addEventListener(type, (event) => {
        const taskEvent = JSON.parse((event as MessageEvent).data) as TaskEvent;
        appendTaskEvent(taskEvent);
        if (type === "agent.token" && taskEvent.payload.agent && taskEvent.payload.token) {
          setLogsByAgent((current) => ({
            ...current,
            [taskEvent.payload.agent as string]:
              (current[taskEvent.payload.agent as string] ?? "") +
              taskEvent.payload.token,
          }));
        }
        if (
          type === "task.completed" ||
          type === "task.failed" ||
          type === "task.cancelled" ||
          type === "approval.required"
        ) {
          source.close();
          refreshDetails(taskId);
        }
      });
    });

    source.onerror = () => {
      source.close();
      refreshDetails(taskId);
    };
  }

  function appendTaskEvent(event: TaskEvent) {
    setEvents((current) => {
      if (current.some((existing) => existing.seq === event.seq)) {
        return current;
      }
      return [...current, event].sort((left, right) => left.seq - right.seq);
    });
  }

  function rebuildLogs(taskEvents: TaskEvent[]) {
    const rebuilt: Record<string, string> = {};
    for (const event of taskEvents) {
      if (event.type === "agent.token" && event.payload.agent && event.payload.token) {
        rebuilt[event.payload.agent] =
          (rebuilt[event.payload.agent] ?? "") + event.payload.token;
      }
    }
    setLogsByAgent(rebuilt);
  }

  async function requestStop() {
    if (!task) {
      return;
    }

    setMessage(null);
    const response = await fetch(`${API_BASE_URL}/tasks/${task.id}/cancel`, {
      method: "POST",
    });
    if (!response.ok) {
      setMessage(`Stop request failed with ${response.status}`);
      return;
    }

    await refreshDetails(task.id);
  }

  async function sendApproval(decision: "approve" | "reject") {
    if (!task) {
      return;
    }

    setMessage(null);
    const response = await fetch(`${API_BASE_URL}/tasks/${task.id}/approval`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, notes: approvalNotes }),
    });
    if (!response.ok) {
      setMessage(`Approval failed with ${response.status}`);
      return;
    }
    await refreshDetails(task.id);
  }

  const graph = useMemo(() => buildGraph(detail, events), [detail, events]);
  const latestReview = detail?.reviews.at(-1);

  return (
    <main className="shell">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>MiniMax Agent Workflow</h1>
            <p>Evidence-first multi-agent task console</p>
          </div>
          <span className={`status-pill ${task?.status ?? "idle"}`}>
            {task?.status ?? "idle"}
          </span>
        </header>

        <form className="task-input" onSubmit={startTask}>
          <label htmlFor="goal">Task</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={4}
          />
          <div className="actions">
            <button disabled={isSubmitting || goal.trim().length === 0} type="submit">
              {isSubmitting ? "Starting..." : "Start task"}
            </button>
            <button disabled={!task} type="button" onClick={() => refreshDetails()}>
              Refresh
            </button>
            <button
              disabled={!task || task.status !== "running"}
              type="button"
              onClick={requestStop}
            >
              Stop
            </button>
          </div>
        </form>

        {message ? <p className="message">{message}</p> : null}

        <section className="layout-grid">
          <section className="panel graph-panel" aria-label="Agent graph">
            <div className="panel-header">
              <h2>Agent graph</h2>
              <span className="event-count">{events.length} events</span>
            </div>
            <ReactFlowProvider>
              <ReactFlow
                nodes={graph.nodes}
                edges={graph.edges}
                fitView
                nodesDraggable={false}
                nodesConnectable={false}
                panOnDrag={false}
                zoomOnScroll={false}
              >
                <Background gap={16} />
              </ReactFlow>
            </ReactFlowProvider>
          </section>

          <section className="panel task-state" aria-label="Task status">
            <div className="panel-header">
              <h2>Task status</h2>
            </div>
            {task ? (
              <dl className="task-details">
                <div>
                  <dt>ID</dt>
                  <dd>{task.id}</dd>
                </div>
                <div>
                  <dt>Current agent</dt>
                  <dd>{task.current_agent ?? "None"}</dd>
                </div>
                <div>
                  <dt>Goal</dt>
                  <dd>{task.goal}</dd>
                </div>
                <div>
                  <dt>Updated</dt>
                  <dd>{new Date(task.updated_at).toLocaleString()}</dd>
                </div>
              </dl>
            ) : (
              <p className="empty">No task has been started yet.</p>
            )}
          </section>
        </section>

        {task?.status === "waiting_human_input" ? (
          <section className="panel approval-panel" aria-label="Human approval">
            <div className="panel-header">
              <h2>Human gate</h2>
              <span className="badge warn">Reviewer blocked</span>
            </div>
            <p>
              Reviewer did not mark this as complete because hardware-specific facts or
              board validation are missing.
            </p>
            <textarea
              value={approvalNotes}
              onChange={(event) => setApprovalNotes(event.target.value)}
              rows={3}
              placeholder="Approval or rejection notes"
            />
            <div className="actions">
              <button type="button" onClick={() => sendApproval("approve")}>
                Approve assumptions
              </button>
              <button type="button" onClick={() => sendApproval("reject")}>
                Send back
              </button>
            </div>
          </section>
        ) : null}

        <section className="panel agent-log" aria-label="Agent logs">
          <div className="panel-header">
            <h2>Agent logs</h2>
            <select
              value={selectedAgent}
              onChange={(event) => setSelectedAgent(event.target.value)}
              aria-label="Agent"
            >
              {agents.map((agent) => (
                <option key={agent} value={agent}>
                  {agent}
                </option>
              ))}
            </select>
          </div>
          <pre>{logsByAgent[selectedAgent] || "No log output for this agent yet."}</pre>
        </section>

        <section className="detail-grid">
          <RecordPanel
            title="Tool calls"
            empty="No tool calls recorded."
            records={detail?.tool_calls ?? []}
            render={(call) => (
              <>
                <strong>{call.tool_name}</strong>
                <span>{call.agent_name}</span>
                <p>{call.result_summary}</p>
                <code>{JSON.stringify(call.args)}</code>
              </>
            )}
          />

          <RecordPanel
            title="Reviewer"
            empty="No reviewer result yet."
            records={latestReview ? [latestReview] : []}
            render={(review) => (
              <>
                <strong>{review.status}</strong>
                <p>{review.summary}</p>
                {review.retry_instructions ? <p>{review.retry_instructions}</p> : null}
                <code>{JSON.stringify(review.checks)}</code>
              </>
            )}
          />

          <RecordPanel
            title="Artifacts"
            empty="No artifacts recorded."
            records={detail?.artifacts ?? []}
            render={(artifact) => (
              <>
                <strong>{artifact.title}</strong>
                <span>{artifact.kind}</span>
                <code>{artifact.path}</code>
              </>
            )}
          />

          <RecordPanel
            title="Evidence"
            empty="No evidence recorded."
            records={detail?.evidence ?? []}
            render={(evidence) => (
              <>
                <strong>{evidence.claim}</strong>
                <span>{evidence.source_title}</span>
                <p>{evidence.notes}</p>
              </>
            )}
          />

          <RecordPanel
            title="Assumptions"
            empty="No assumptions recorded."
            records={detail?.assumptions ?? []}
            render={(assumption) => (
              <>
                <strong>{assumption.status}</strong>
                <p>{assumption.claim}</p>
                <span>{assumption.risk}</span>
              </>
            )}
          />

          <RecordPanel
            title="Hardware"
            empty="No hardware validation recorded."
            records={detail?.hardware_validations ?? []}
            render={(validation) => (
              <>
                <strong>{validation.status}</strong>
                <span>{validation.name}</span>
                <p>{validation.evidence}</p>
              </>
            )}
          />
        </section>

        <section className="panel event-list" aria-label="Task events">
          <div className="panel-header">
            <h2>Events</h2>
          </div>
          {events.length > 0 ? (
            <ol>
              {events.map((event) => (
                <li key={event.seq}>
                  <span>{event.seq}</span>
                  <strong>{event.type}</strong>
                  <code>{JSON.stringify(event.payload)}</code>
                </li>
              ))}
            </ol>
          ) : (
            <p className="empty">No events have been received yet.</p>
          )}
        </section>
      </section>
    </main>
  );
}

function RecordPanel<T>({
  title,
  empty,
  records,
  render,
}: {
  title: string;
  empty: string;
  records: T[];
  render: (record: T) => ReactNode;
}) {
  return (
    <section className="panel record-panel">
      <div className="panel-header">
        <h2>{title}</h2>
        <span className="event-count">{records.length}</span>
      </div>
      {records.length > 0 ? (
        <ol>
          {records.map((record, index) => (
            <li key={index}>{render(record)}</li>
          ))}
        </ol>
      ) : (
        <p className="empty">{empty}</p>
      )}
    </section>
  );
}

function buildGraph(
  detail: TaskDetail | null,
  liveEvents: TaskEvent[],
): { nodes: Node[]; edges: Edge[] } {
  const events = detail?.events.length ? detail.events : liveEvents;
  const completed = new Set(
    events
      .filter((event) => event.type === "agent.completed" && event.payload.agent)
      .map((event) => event.payload.agent as string),
  );
  const started = new Set(
    events
      .filter((event) => event.type === "agent.started" && event.payload.agent)
      .map((event) => event.payload.agent as string),
  );
  const current = detail?.task.current_agent;

  const nodes: Node[] = agents.map((agent, index) => {
    const state = completed.has(agent)
      ? "done"
      : current === agent
        ? "active"
        : started.has(agent)
          ? "started"
          : "pending";
    return {
      id: agent,
      position: { x: index * 190, y: index % 2 === 0 ? 0 : 90 },
      data: { label: `${agent}\n${state}` },
      className: `agent-node ${state}`,
      draggable: false,
    };
  });

  const edges: Edge[] = agents.slice(0, -1).map((agent, index) => ({
    id: `${agent}-${agents[index + 1]}`,
    source: agent,
    target: agents[index + 1],
    markerEnd: { type: MarkerType.ArrowClosed },
    animated: current === agents[index + 1],
  }));

  return { nodes, edges };
}
