import os
import sqlite3
from pathlib import Path
from typing import Optional

from .models import TaskRecord, TaskStatus, utc_now


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
                WHERE id = ?
                """,
                (TaskStatus.CANCEL_REQUESTED.value, now.isoformat(), task_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_task(task_id)

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
