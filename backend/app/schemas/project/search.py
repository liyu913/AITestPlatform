from typing import Literal

from pydantic import BaseModel, Field

SearchKind = Literal["requirement", "test_case", "test_plan", "test_cycle", "defect", "api_case", "ui_case"]


class SearchHit(BaseModel):
    kind: SearchKind
    id: int
    title: str
    subtitle: str | None = None
    # Project-relative route (the UI prefixes /projects/{id}/).
    link: str


class SearchGroup(BaseModel):
    kind: SearchKind
    label: str
    items: list[SearchHit] = Field(default_factory=list)


class SearchOut(BaseModel):
    query: str
    groups: list[SearchGroup] = Field(default_factory=list)
    total: int = 0
