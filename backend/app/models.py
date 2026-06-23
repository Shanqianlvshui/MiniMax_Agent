from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    RUNNING = "running"
    WAITING_HUMAN_INPUT = "waiting_human_input"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class CreateTaskRequest(BaseModel):
    goal: str = Field(min_length=1)


class TaskRecord(BaseModel):
    id: str
    goal: str
    status: TaskStatus
    cancel_requested: bool
    current_agent: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TaskEvent(BaseModel):
    id: int
    task_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    notes: str = ""


class ToolCallRecord(BaseModel):
    id: int
    task_id: str
    agent_name: str
    tool_name: str
    args: dict[str, Any]
    status: str
    result_summary: str
    stdout: str
    stderr: str
    created_at: datetime


class ArtifactRecord(BaseModel):
    id: int
    task_id: str
    kind: str
    title: str
    path: str
    metadata: dict[str, Any]
    created_at: datetime


class ReviewRecord(BaseModel):
    id: int
    task_id: str
    status: str
    summary: str
    checks: list[dict[str, Any]]
    retry_instructions: Optional[str]
    created_at: datetime


class EvidenceRecord(BaseModel):
    id: int
    task_id: str
    claim: str
    source_type: str
    source_title: str
    url: Optional[str]
    version_or_date: Optional[str]
    section_or_page: Optional[str]
    confidence: str
    notes: str
    created_at: datetime


class AssumptionRecord(BaseModel):
    id: int
    task_id: str
    claim: str
    scope: str
    reason: str
    risk: str
    status: str
    requires_user_confirmation: bool
    created_at: datetime


class HardwareValidationRecord(BaseModel):
    id: int
    task_id: str
    name: str
    status: str
    evidence: str
    created_at: datetime


class TaskDetail(BaseModel):
    task: TaskRecord
    events: list[TaskEvent]
    tool_calls: list[ToolCallRecord]
    artifacts: list[ArtifactRecord]
    reviews: list[ReviewRecord]
    evidence: list[EvidenceRecord]
    assumptions: list[AssumptionRecord]
    hardware_validations: list[HardwareValidationRecord]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
