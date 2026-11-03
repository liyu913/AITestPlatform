from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PerfCaseItem(BaseModel):
    case_id: int = Field(ge=1)
    weight: int = Field(default=1, ge=1, le=1000)


class PerfSlaIn(BaseModel):
    max_p95_ms: float | None = Field(default=None, ge=0)
    max_error_rate: float | None = Field(default=None, ge=0, le=1)


class PerfScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    env_id: int | None = None
    case_items: list[PerfCaseItem] = Field(default_factory=list)
    users: int = Field(default=10, ge=1, le=200)
    spawn_rate: float = Field(default=2.0, gt=0, le=50)
    run_time_sec: int = Field(default=60, ge=1, le=600)
    think_time_ms: int = Field(default=0, ge=0, le=60000)
    sla: PerfSlaIn = Field(default_factory=PerfSlaIn)


class PerfScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    env_id: int | None = None
    case_items: list[PerfCaseItem] | None = None
    users: int | None = Field(default=None, ge=1, le=200)
    spawn_rate: float | None = Field(default=None, gt=0, le=50)
    run_time_sec: int | None = Field(default=None, ge=1, le=600)
    think_time_ms: int | None = Field(default=None, ge=0, le=60000)
    sla: PerfSlaIn | None = None


class PerfScenarioOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    env_id: int | None
    case_items: list[PerfCaseItem]
    users: int
    spawn_rate: float
    run_time_sec: int
    think_time_ms: int
    sla: PerfSlaIn
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class PerfScenarioRunRequest(BaseModel):
    env_id: int | None = None
    users: int | None = Field(default=None, ge=1, le=200)
    spawn_rate: float | None = Field(default=None, gt=0, le=50)
    run_time_sec: int | None = Field(default=None, ge=1, le=600)
    sync: bool = False


class PerfRunOut(BaseModel):
    id: int
    project_id: int
    scenario_id: int | None
    env_id: int | None
    status: str
    config_snapshot_json: dict[str, Any] | None
    summary_json: dict[str, Any] | None
    triggered_by: int | None
    celery_task_id: str | None
    started_at: str | None
    finished_at: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PerfTrendPoint(BaseModel):
    run_id: int
    scenario_id: int | None = None
    scenario_name: str | None = None
    status: str
    created_at: str | None = None
    finished_at: str | None = None
    p95_ms: float | None = None
    p90_ms: float | None = None
    avg_ms: float | None = None
    rps: float | None = None
    fail_ratio: float | None = None
    total_requests: int | None = None


class PerfTrendOut(BaseModel):
    points: list[PerfTrendPoint]
    totals: dict[str, int]


class PerfCompareMetric(BaseModel):
    metric: str
    current: float | int | None = None
    baseline: float | int | None = None
    delta: float | None = None
    delta_pct: float | None = None
    better: str
    improved: bool | None = None


class PerfCompareOut(BaseModel):
    current_run_id: int
    baseline_run_id: int | None
    baseline_finished_at: str | None = None
    metrics: list[PerfCompareMetric]
    current_summary: dict[str, Any] | None = None
    baseline_summary: dict[str, Any] | None = None


def normalize_case_items(items: list[PerfCaseItem] | list[dict] | None) -> list[dict]:
    if not items:
        return []
    out: list[dict] = []
    for it in items:
        if isinstance(it, PerfCaseItem):
            out.append({"case_id": it.case_id, "weight": it.weight})
        elif isinstance(it, dict):
            cid = int(it.get("case_id", 0))
            w = int(it.get("weight", 1) or 1)
            if cid >= 1:
                out.append({"case_id": cid, "weight": max(1, min(1000, w))})
    return out


def sla_dict(sla: PerfSlaIn | dict | None) -> dict:
    if sla is None:
        return {}
    if isinstance(sla, PerfSlaIn):
        data = sla.model_dump(exclude_none=True)
    elif isinstance(sla, dict):
        data = {k: v for k, v in sla.items() if v is not None and k in ("max_p95_ms", "max_error_rate")}
    else:
        data = {}
    return data
