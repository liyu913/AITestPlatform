from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WorkbenchKind = Literal["manual_run", "review", "regression", "run"]


class WorkbenchTask(BaseModel):
    """One actionable row on the workbench.

    ``link`` is the project-relative route the UI appends to
    ``/projects/{project_id}/`` so that a task always lands on the record it
    came from.
    """

    key: str
    kind: WorkbenchKind
    title: str
    subtitle: str | None = None
    status: str
    priority: str | None = None
    target_type: str
    target_id: int
    link: str
    updated_at: datetime | None = None


class WorkbenchBucket(BaseModel):
    key: str
    label: str
    count: int
    items: list[WorkbenchTask] = Field(default_factory=list)
    empty_hint: str = ""


class WorkbenchRecentRun(BaseModel):
    key: str
    kind: Literal["functional", "api", "ui"]
    kind_label: str
    id: int
    status: str
    link: str
    created_at: datetime


class WorkbenchOut(BaseModel):
    project_id: int
    generated_at: datetime
    buckets: list[WorkbenchBucket]
    recent_runs: list[WorkbenchRecentRun]
