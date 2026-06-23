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

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export default function TaskConsole() {
  const [goal, setGoal] = useState("");
  const [task, setTask] = useState<TaskRecord | null>(null);
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
      setGoal("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Task creation failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function refreshTask() {
    if (!task) {
      return;
    }

    setMessage(null);
    const response = await fetch(`${API_BASE_URL}/tasks/${task.id}`);
    if (!response.ok) {
      setMessage(`Refresh failed with ${response.status}`);
      return;
    }

    setTask((await response.json()) as TaskRecord);
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
              <button disabled={!task} type="button" onClick={refreshTask}>
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
      </section>
    </main>
  );
}
