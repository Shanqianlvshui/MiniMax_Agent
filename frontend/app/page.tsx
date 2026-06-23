"use client";

import { FormEvent, useState } from "react";

type TaskStatus =
  | "running"
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
  };
  created_at: string;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export default function TaskConsole() {
  const [goal, setGoal] = useState("");
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [plannerOutput, setPlannerOutput] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function startTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setMessage(null);

    try {
      const response = await fetch(`${API_BASE_URL}/tasks`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ goal }),
      });

      if (!response.ok) {
        throw new Error(`Task creation failed with ${response.status}`);
      }

      const created = (await response.json()) as TaskRecord;
      setTask(created);
      setEvents([]);
      setPlannerOutput("");
      setGoal("");
      subscribeToTaskEvents(created.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Task creation failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function refreshTask(taskId = task?.id) {
    if (!taskId) {
      return;
    }

    setMessage(null);
    const response = await fetch(`${API_BASE_URL}/tasks/${taskId}`);
    if (!response.ok) {
      setMessage(`Refresh failed with ${response.status}`);
      return;
    }

    setTask((await response.json()) as TaskRecord);
  }

  function subscribeToTaskEvents(taskId: string) {
    const source = new EventSource(`${API_BASE_URL}/tasks/${taskId}/events`);

    source.onmessage = (event) => {
      appendTaskEvent(JSON.parse(event.data) as TaskEvent);
    };

    [
      "task.started",
      "agent.started",
      "agent.token",
      "agent.completed",
      "agent.failed",
      "task.completed",
      "task.failed",
    ].forEach((type) => {
      source.addEventListener(type, (event) => {
        const taskEvent = JSON.parse((event as MessageEvent).data) as TaskEvent;
        appendTaskEvent(taskEvent);
        if (type === "agent.token" && taskEvent.payload.token) {
          setPlannerOutput((current) => current + taskEvent.payload.token);
        }
        if (type === "task.completed" || type === "task.failed") {
          source.close();
          refreshTask(taskId);
        }
      });
    });

    source.onerror = () => {
      source.close();
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

    setTask((await response.json()) as TaskRecord);
  }

  return (
    <main className="shell">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>MiniMax Agent Workflow</h1>
            <p>Evidence-driven task console</p>
          </div>
          <span className="status-pill">V1 Slice 1</span>
        </header>

        <form className="task-input" onSubmit={startTask}>
          <label htmlFor="goal">Task</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="Describe the development task..."
            rows={5}
          />
          <button disabled={isSubmitting || goal.trim().length === 0} type="submit">
            {isSubmitting ? "Starting..." : "Start task"}
          </button>
        </form>

        {message ? <p className="message">{message}</p> : null}

        <section className="task-state" aria-label="Task status">
          <div className="panel-header">
            <h2>Task status</h2>
            <div className="actions">
              <button disabled={!task} type="button" onClick={() => refreshTask()}>
                Refresh
              </button>
              <button
                disabled={!task || task.cancel_requested}
                type="button"
                onClick={requestStop}
              >
                Request stop
              </button>
            </div>
          </div>

          {task ? (
            <dl className="task-details">
              <div>
                <dt>ID</dt>
                <dd>{task.id}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{task.status}</dd>
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
                <dt>Created</dt>
                <dd>{new Date(task.created_at).toLocaleString()}</dd>
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

        <section className="agent-log" aria-label="Planner log">
          <div className="panel-header">
            <h2>Planner log</h2>
            <span className="event-count">{events.length} events</span>
          </div>
          <pre>{plannerOutput || "No planner output yet."}</pre>
        </section>

        <section className="event-list" aria-label="Task events">
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
