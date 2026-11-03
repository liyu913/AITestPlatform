import datetime

from pydantic import BaseModel


class AiCallLogListItem(BaseModel):
    id: int
    created_at: datetime.datetime | None = None
    kind: str
    scene: str | None = None
    model: str | None = None
    status: str
    http_status: int | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    user_id: int | None = None
    username: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    request_id: str | None = None


class AiCallLogDetail(AiCallLogListItem):
    error_message: str | None = None
    request_text: str | None = None
    response_text: str | None = None
