import os
import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import TaskEvent, TaskRecord, TaskStatus, utc_now


def default_db_path() -> Path:
    configured = os.environ.get("MINIMAX_AGENT_DB_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "minimax_agent.db"


class TaskStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    current_agent TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_task_seq
                ON events(task_id, seq)
                """
            )

    def create_task(self, task_id: str, goal: str) -> TaskRecord:
        now = utc_now()
        task = TaskRecord(
            id=task_id,
            goal=goal,
            status=TaskStatus.RUNNING,
            cancel_requested=False,
            current_agent=None,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, goal, status, cancel_requested, current_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.goal,
                    task.status.value,
                    int(task.cancel_requested),
                    task.current_agent,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )
        return task

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def request_cancel(self, task_id: str) -> Optional[TaskRecord]:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?, cancel_requested = 1, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.CANCEL_REQUESTED.value,
                    now.isoformat(),
                    task_id,
                    TaskStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount == 0:
                return self.get_task(task_id)
        return self.get_task(task_id)

    def set_current_agent(self, task_id: str, current_agent: Optional[str]) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET current_agent = ?, updated_at = ?
                WHERE id = ?
                """,
                (current_agent, now.isoformat(), task_id),
            )

    def complete_task(self, task_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_agent = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.COMPLETED.value, now.isoformat(), task_id),
            )

    def fail_task(self, task_id: str, error: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_agent = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.FAILED.value, now.isoformat(), task_id),
            )

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict,
    ) -> TaskEvent:
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            seq = int(row["next_seq"])
            cursor = connection.execute(
                """
                INSERT INTO events (task_id, seq, type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    seq,
                    event_type,
                    json.dumps(payload),
                    now.isoformat(),
                ),
            )
            event_id = int(cursor.lastrowid)
        return TaskEvent(
            id=event_id,
            task_id=task_id,
            seq=seq,
            type=event_type,
            payload=payload,
            created_at=now,
        )

    def list_events(self, task_id: str) -> list[TaskEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY seq ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            goal=row["goal"],
            status=TaskStatus(row["status"]),
            cancel_requested=bool(row["cancel_requested"]),
            current_agent=row["current_agent"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            id=row["id"],
            task_id=row["task_id"],
            seq=row["seq"],
            type=row["type"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )
