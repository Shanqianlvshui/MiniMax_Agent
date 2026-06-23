import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .models import (
    ArtifactRecord,
    AssumptionRecord,
    EvidenceRecord,
    HardwareValidationRecord,
    ReviewRecord,
    TaskDetail,
    TaskEvent,
    TaskRecord,
    TaskStatus,
    ToolCallRecord,
    utc_now,
)


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    retry_instructions TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    url TEXT,
                    version_or_date TEXT,
                    section_or_page TEXT,
                    confidence TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assumptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_user_confirmation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hardware_validations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
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
                WHERE id = ? AND status IN (?, ?)
                """,
                (
                    TaskStatus.CANCEL_REQUESTED.value,
                    now.isoformat(),
                    task_id,
                    TaskStatus.RUNNING.value,
                    TaskStatus.WAITING_HUMAN_INPUT.value,
                ),
            )
            if cursor.rowcount == 0:
                return self.get_task(task_id)
        return self.get_task(task_id)

    def set_waiting_for_human(self, task_id: str, current_agent: Optional[str] = None) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_agent = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    TaskStatus.WAITING_HUMAN_INPUT.value,
                    current_agent,
                    now.isoformat(),
                    task_id,
                ),
            )

    def resume_task(self, task_id: str, current_agent: Optional[str] = None) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_agent = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    current_agent,
                    now.isoformat(),
                    task_id,
                ),
            )

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

    def cancel_task(self, task_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, current_agent = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.CANCELLED.value, now.isoformat(), task_id),
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

    def record_tool_call(
        self,
        task_id: str,
        agent_name: str,
        tool_name: str,
        args: dict[str, Any],
        status: str,
        result_summary: str,
        stdout: str = "",
        stderr: str = "",
    ) -> ToolCallRecord:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tool_calls (
                    task_id, agent_name, tool_name, args_json, status,
                    result_summary, stdout, stderr, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    agent_name,
                    tool_name,
                    json.dumps(args),
                    status,
                    result_summary,
                    stdout,
                    stderr,
                    now.isoformat(),
                ),
            )
            row_id = int(cursor.lastrowid)
        return ToolCallRecord(
            id=row_id,
            task_id=task_id,
            agent_name=agent_name,
            tool_name=tool_name,
            args=args,
            status=status,
            result_summary=result_summary,
            stdout=stdout,
            stderr=stderr,
            created_at=now,
        )

    def record_artifact(
        self,
        task_id: str,
        kind: str,
        title: str,
        path: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ArtifactRecord:
        now = utc_now()
        metadata = metadata or {}
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO artifacts (task_id, kind, title, path, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    kind,
                    title,
                    path,
                    json.dumps(metadata),
                    now.isoformat(),
                ),
            )
            row_id = int(cursor.lastrowid)
        return ArtifactRecord(
            id=row_id,
            task_id=task_id,
            kind=kind,
            title=title,
            path=path,
            metadata=metadata,
            created_at=now,
        )

    def record_review(
        self,
        task_id: str,
        status: str,
        summary: str,
        checks: list[dict[str, Any]],
        retry_instructions: Optional[str] = None,
    ) -> ReviewRecord:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reviews (
                    task_id, status, summary, checks_json, retry_instructions, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    status,
                    summary,
                    json.dumps(checks),
                    retry_instructions,
                    now.isoformat(),
                ),
            )
            row_id = int(cursor.lastrowid)
        return ReviewRecord(
            id=row_id,
            task_id=task_id,
            status=status,
            summary=summary,
            checks=checks,
            retry_instructions=retry_instructions,
            created_at=now,
        )

    def record_evidence(
        self,
        task_id: str,
        claim: str,
        source_type: str,
        source_title: str,
        url: Optional[str],
        version_or_date: Optional[str],
        section_or_page: Optional[str],
        confidence: str,
        notes: str,
    ) -> EvidenceRecord:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO evidence (
                    task_id, claim, source_type, source_title, url,
                    version_or_date, section_or_page, confidence, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    claim,
                    source_type,
                    source_title,
                    url,
                    version_or_date,
                    section_or_page,
                    confidence,
                    notes,
                    now.isoformat(),
                ),
            )
            row_id = int(cursor.lastrowid)
        return EvidenceRecord(
            id=row_id,
            task_id=task_id,
            claim=claim,
            source_type=source_type,
            source_title=source_title,
            url=url,
            version_or_date=version_or_date,
            section_or_page=section_or_page,
            confidence=confidence,
            notes=notes,
            created_at=now,
        )

    def record_assumption(
        self,
        task_id: str,
        claim: str,
        scope: str,
        reason: str,
        risk: str,
        status: str,
        requires_user_confirmation: bool,
    ) -> AssumptionRecord:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO assumptions (
                    task_id, claim, scope, reason, risk, status,
                    requires_user_confirmation, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    claim,
                    scope,
                    reason,
                    risk,
                    status,
                    int(requires_user_confirmation),
                    now.isoformat(),
                ),
            )
            row_id = int(cursor.lastrowid)
        return AssumptionRecord(
            id=row_id,
            task_id=task_id,
            claim=claim,
            scope=scope,
            reason=reason,
            risk=risk,
            status=status,
            requires_user_confirmation=requires_user_confirmation,
            created_at=now,
        )

    def confirm_pending_assumptions(self, task_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE assumptions
                SET status = ?, created_at = created_at
                WHERE task_id = ? AND status = ?
                """,
                ("confirmed_by_human", task_id, "needs_human_confirmation"),
            )
            connection.execute(
                """
                UPDATE tasks
                SET updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), task_id),
            )

    def record_hardware_validation(
        self,
        task_id: str,
        name: str,
        status: str,
        evidence: str,
    ) -> HardwareValidationRecord:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO hardware_validations (task_id, name, status, evidence, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, name, status, evidence, now.isoformat()),
            )
            row_id = int(cursor.lastrowid)
        return HardwareValidationRecord(
            id=row_id,
            task_id=task_id,
            name=name,
            status=status,
            evidence=evidence,
            created_at=now,
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

    def task_detail(self, task_id: str) -> Optional[TaskDetail]:
        task = self.get_task(task_id)
        if task is None:
            return None
        return TaskDetail(
            task=task,
            events=self.list_events(task_id),
            tool_calls=self.list_tool_calls(task_id),
            artifacts=self.list_artifacts(task_id),
            reviews=self.list_reviews(task_id),
            evidence=self.list_evidence(task_id),
            assumptions=self.list_assumptions(task_id),
            hardware_validations=self.list_hardware_validations(task_id),
        )

    def list_tool_calls(self, task_id: str) -> list[ToolCallRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_tool_call(row) for row in rows]

    def list_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def list_reviews(self, task_id: str) -> list[ReviewRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reviews WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_review(row) for row in rows]

    def list_evidence(self, task_id: str) -> list[EvidenceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_evidence(row) for row in rows]

    def list_assumptions(self, task_id: str) -> list[AssumptionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assumptions WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_assumption(row) for row in rows]

    def list_hardware_validations(self, task_id: str) -> list[HardwareValidationRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM hardware_validations WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_hardware_validation(row) for row in rows]

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

    @staticmethod
    def _row_to_tool_call(row: sqlite3.Row) -> ToolCallRecord:
        return ToolCallRecord(
            id=row["id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            tool_name=row["tool_name"],
            args=json.loads(row["args_json"]),
            status=row["status"],
            result_summary=row["result_summary"],
            stdout=row["stdout"],
            stderr=row["stderr"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=row["id"],
            task_id=row["task_id"],
            kind=row["kind"],
            title=row["title"],
            path=row["path"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_review(row: sqlite3.Row) -> ReviewRecord:
        return ReviewRecord(
            id=row["id"],
            task_id=row["task_id"],
            status=row["status"],
            summary=row["summary"],
            checks=json.loads(row["checks_json"]),
            retry_instructions=row["retry_instructions"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            id=row["id"],
            task_id=row["task_id"],
            claim=row["claim"],
            source_type=row["source_type"],
            source_title=row["source_title"],
            url=row["url"],
            version_or_date=row["version_or_date"],
            section_or_page=row["section_or_page"],
            confidence=row["confidence"],
            notes=row["notes"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_assumption(row: sqlite3.Row) -> AssumptionRecord:
        return AssumptionRecord(
            id=row["id"],
            task_id=row["task_id"],
            claim=row["claim"],
            scope=row["scope"],
            reason=row["reason"],
            risk=row["risk"],
            status=row["status"],
            requires_user_confirmation=bool(row["requires_user_confirmation"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_hardware_validation(row: sqlite3.Row) -> HardwareValidationRecord:
        return HardwareValidationRecord(
            id=row["id"],
            task_id=row["task_id"],
            name=row["name"],
            status=row["status"],
            evidence=row["evidence"],
            created_at=row["created_at"],
        )
