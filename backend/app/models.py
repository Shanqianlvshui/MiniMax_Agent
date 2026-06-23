from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    RUNNING = "running"
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
