from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CountBucket(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


class DashboardAssets(BaseModel):
    requirements: CountBucket
    test_cases: CountBucket
    defects: CountBucket
    test_plans: CountBucket
    knowledge_documents: CountBucket
    modules: CountBucket
    members: CountBucket
    api_cases: CountBucket
    api_suites: CountBucket
    ui_cases: CountBucket
    ui_suites: CountBucket


class CoverageSummary(BaseModel):
    requirement_total: int
    blind_count: int
    avg_ratio: float


class RunTrendPoint(BaseModel):
    day: str
    total: int
    passed: int
    failed: int
    error: int


class RunTrend(BaseModel):
    points: list[RunTrendPoint]
    totals: dict[str, int]


class DashboardRuns(BaseModel):
    functional: RunTrend
    api: RunTrend
    ui: RunTrend


class RecentRun(BaseModel):
    kind: Literal["functional", "api", "ui"]
    id: int
    status: str
    created_at: datetime


class ProjectDashboardOut(BaseModel):
    assets: DashboardAssets
    coverage: CoverageSummary
    runs: DashboardRuns
    recent_runs: list[RecentRun]
