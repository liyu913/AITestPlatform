from datetime import datetime

from pydantic import BaseModel


class EvidenceOut(BaseModel):
    attempt_id: int
    plan_id: int
    plan_name: str | None = None
    cycle_id: int
    cycle_name: str | None = None
    item_id: int
    asset_type: str = "functional"
    case_id: int | None = None
    case_title: str | None = None
    attempt_no: int
    execution_type: str
    status: str
    summary: str = ""
    original_run_type: str | None = None
    original_run_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    evidence: dict | None = None
