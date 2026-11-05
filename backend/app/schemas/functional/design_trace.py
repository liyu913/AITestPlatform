from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, model_validator


TraceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class CaseDesignTrace(BaseModel):
    basis: Literal["confirmed", "inferred", "conditional", "assumed"]
    source_ref: TraceText
    locator: TraceText
    excerpt: TraceText
    method: TraceText
    coverage_target: TraceText
    rationale: TraceText
    readiness: Literal["ready", "needs_data", "needs_rule"]
    preparation_note: TraceText | None = None
    # The server stamps these after matching the model's citation against the input.
    skill_version: str | None = None
    source_digest: str | None = None
    requirement_id: int | None = None
    knowledge_library_id: int | None = None
    # Model that produced the draft, recorded so a case can be traced back to
    # the configuration used at generation time.
    llm_model: str | None = None

    @model_validator(mode="after")
    def check_preparation(self):
        if self.basis == "conditional" and self.readiness != "needs_rule":
            raise ValueError("待确认细则的准备状态必须是 needs_rule")
        if self.readiness != "ready" and not self.preparation_note:
            raise ValueError("请说明需要准备的数据或待确认的规则")
        return self


def has_step_expectation(value: str | None) -> bool:
    return (value or "").strip() not in {"", "—", "-", "正常", "符合要求", "符合预期"}
