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

const agentLabels: Record<string, string> = {
  manager: "管理器",
  planner: "规划员",
  researcher: "研究员",
  executor: "执行员",
  reviewer: "审查员",
  writer: "撰写员",
};

const statusLabels: Record<string, string> = {
  idle: "空闲",
  running: "运行中",
  waiting_human_input: "等待人工处理",
  cancel_requested: "正在停止",
  cancelled: "已停止",
  completed: "已完成",
  failed: "失败",
  pending: "待执行",
  started: "已启动",
  active: "执行中",
  done: "完成",
  needs_human: "需要人工处理",
  not_run: "未运行",
  passed: "通过",
};

export default function TaskConsole() {
  const [goal, setGoal] = useState(
    "基于官方 STM32Cube 工具链，为 STM32F103C8T6 最小系统板开发 USB CDC 驱动",
  );
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [logsByAgent, setLogsByAgent] = useState<Record<string, string>>({});
  const [selectedAgent, setSelectedAgent] = useState("planner");
  const [selectedDetailPanel, setSelectedDetailPanel] = useState("tools");
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
        throw new Error(`创建任务失败，HTTP ${response.status}`);
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
      setMessage(error instanceof Error ? error.message : "创建任务失败");
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
      setMessage(`刷新失败，HTTP ${response.status}`);
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
      setMessage(`停止任务失败，HTTP ${response.status}`);
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
      setMessage(`提交人工处理结果失败，HTTP ${response.status}`);
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
            <h1>MiniMax Agent 工作流</h1>
            <p>证据优先的多 Agent 任务控制台</p>
          </div>
          <span className={`status-pill ${task?.status ?? "idle"}`}>
            {formatStatus(task?.status ?? "idle")}
          </span>
        </header>

        <form className="task-input" onSubmit={startTask}>
          <label htmlFor="goal">任务</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={4}
          />
          <div className="actions">
            <button disabled={isSubmitting || goal.trim().length === 0} type="submit">
              {isSubmitting ? "启动中..." : "启动任务"}
            </button>
            <button disabled={!task} type="button" onClick={() => refreshDetails()}>
              刷新
            </button>
            <button
              disabled={!task || task.status !== "running"}
              type="button"
              onClick={requestStop}
            >
              停止
            </button>
          </div>
        </form>

        {message ? <p className="message">{message}</p> : null}

        <section className="layout-grid">
          <section className="panel graph-panel" aria-label="Agent 状态图">
            <div className="panel-header">
              <h2>Agent 状态图</h2>
              <span className="event-count">{events.length} 条事件</span>
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

          <section className="panel task-state" aria-label="任务状态">
            <div className="panel-header">
              <h2>任务状态</h2>
            </div>
            {task ? (
              <dl className="task-details">
                <div>
                  <dt>ID</dt>
                  <dd>{task.id}</dd>
                </div>
                <div>
                  <dt>当前 Agent</dt>
                  <dd>{task.current_agent ? formatAgent(task.current_agent) : "无"}</dd>
                </div>
                <div>
                  <dt>目标</dt>
                  <dd>{task.goal}</dd>
                </div>
                <div>
                  <dt>更新时间</dt>
                  <dd>{new Date(task.updated_at).toLocaleString()}</dd>
                </div>
              </dl>
            ) : (
              <p className="empty">还没有启动任务。</p>
            )}
          </section>
        </section>

        {task?.status === "waiting_human_input" ? (
          <section className="panel approval-panel" aria-label="人工处理">
            <div className="panel-header">
              <h2>人工关口</h2>
              <span className="badge warn">审查员已阻塞</span>
            </div>
            <p>
              审查员没有把任务标记为完成，因为板级事实或实际硬件验证仍缺失。
            </p>
            <textarea
              value={approvalNotes}
              onChange={(event) => setApprovalNotes(event.target.value)}
              rows={3}
              placeholder="批准或打回说明"
            />
            <div className="actions">
              <button type="button" onClick={() => sendApproval("approve")}>
                批准这些假设
              </button>
              <button type="button" onClick={() => sendApproval("reject")}>
                打回
              </button>
            </div>
          </section>
        ) : null}

        <section className="panel agent-log" aria-label="Agent 日志">
          <div className="panel-header">
            <h2>Agent 日志</h2>
            <select
              value={selectedAgent}
              onChange={(event) => setSelectedAgent(event.target.value)}
              aria-label="Agent"
            >
              {agents.map((agent) => (
                <option key={agent} value={agent}>
                  {formatAgent(agent)}
                </option>
              ))}
            </select>
          </div>
          <pre>{logsByAgent[selectedAgent] || "这个 Agent 还没有日志输出。"}</pre>
        </section>

        <section className="panel detail-dock">
          <div className="panel-header">
            <h2>审计</h2>
            <select
              value={selectedDetailPanel}
              onChange={(event) => setSelectedDetailPanel(event.target.value)}
              aria-label="审计面板"
            >
              <option value="tools">工具调用</option>
              <option value="review">审查结果</option>
              <option value="artifacts">产物</option>
              <option value="evidence">证据</option>
              <option value="assumptions">假设</option>
              <option value="hardware">硬件验证</option>
            </select>
          </div>
          <div className="detail-dock-body">
            {selectedDetailPanel === "tools" ? (
              <RecordPanel
                title="工具调用"
                empty="还没有记录工具调用。"
                records={detail?.tool_calls ?? []}
                render={(call) => (
                  <>
                    <strong>{call.tool_name}</strong>
                    <span>{formatAgent(call.agent_name)}</span>
                    <p>{call.result_summary}</p>
                    <code>{JSON.stringify(call.args)}</code>
                  </>
                )}
              />
            ) : null}

            {selectedDetailPanel === "review" ? (
              <RecordPanel
                title="审查结果"
                empty="还没有审查结果。"
                records={latestReview ? [latestReview] : []}
                render={(review) => (
                  <>
                    <strong>{formatStatus(review.status)}</strong>
                    <p>{review.summary}</p>
                    {review.retry_instructions ? <p>{review.retry_instructions}</p> : null}
                    <code>{JSON.stringify(review.checks)}</code>
                  </>
                )}
              />
            ) : null}

            {selectedDetailPanel === "artifacts" ? (
              <RecordPanel
                title="产物"
                empty="还没有记录产物。"
                records={detail?.artifacts ?? []}
                render={(artifact) => (
                  <>
                    <strong>{artifact.title}</strong>
                    <span>{artifact.kind}</span>
                    <code>{artifact.path}</code>
                  </>
                )}
              />
            ) : null}

            {selectedDetailPanel === "evidence" ? (
              <RecordPanel
                title="证据"
                empty="还没有记录证据。"
                records={detail?.evidence ?? []}
                render={(evidence) => (
                  <>
                    <strong>{evidence.claim}</strong>
                    <span>{evidence.source_title}</span>
                    <p>{evidence.notes}</p>
                  </>
                )}
              />
            ) : null}

            {selectedDetailPanel === "assumptions" ? (
              <RecordPanel
                title="假设"
                empty="还没有记录假设。"
                records={detail?.assumptions ?? []}
                render={(assumption) => (
                  <>
                    <strong>{formatStatus(assumption.status)}</strong>
                    <p>{assumption.claim}</p>
                    <span>{assumption.risk}</span>
                  </>
                )}
              />
            ) : null}

            {selectedDetailPanel === "hardware" ? (
              <RecordPanel
                title="硬件验证"
                empty="还没有记录硬件验证。"
                records={detail?.hardware_validations ?? []}
                render={(validation) => (
                  <>
                    <strong>{formatStatus(validation.status)}</strong>
                    <span>{validation.name}</span>
                    <p>{validation.evidence}</p>
                  </>
                )}
              />
            ) : null}
          </div>
        </section>

        <section className="panel event-list" aria-label="任务事件">
          <div className="panel-header">
            <h2>事件</h2>
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
            <p className="empty">还没有收到事件。</p>
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
        <span className="event-count">{records.length} 条</span>
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
      data: { label: `${formatAgent(agent)}\n${formatStatus(state)}` },
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

function formatAgent(agent: string): string {
  return agentLabels[agent] ?? agent;
}

function formatStatus(status: string): string {
  return statusLabels[status] ?? status;
}
