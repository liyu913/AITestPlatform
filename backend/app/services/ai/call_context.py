"""Request-scoped identity for AI call logs. Set by middleware and auth dependencies."""

from __future__ import annotations

import contextvars

user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("ai_call_user_id", default=None)
project_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "ai_call_project_id", default=None
)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_call_request_id", default=None
)
