from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExecutionKind = Literal["manual", "api", "ui"]


class ExecutionRow(BaseModel):
    """One row of the unified execution list.

    ``link`` is project-relative so the UI can always land on the record that
    produced the row (manual item detail, API run detail, UI run detail).
    """

    key: str
    kind: ExecutionKind
    id: int
    title: str
    status: str
    batch: str | None = None
    cycle_id: int | None = None
    cycle_item_id: int | None = None
    attempt_no: int = 0
    target_type: str | None = None
    target_id: int | None = None
    link: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class ExecutionPage(BaseModel):
    items: list[ExecutionRow] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    counts: dict[str, int] = Field(default_factory=dict)


class ExecutionSummary(BaseModel):
    manual: int = 0
    api: int = 0
    ui: int = 0
    total: int = 0
