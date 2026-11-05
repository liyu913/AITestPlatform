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

action_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_call_action", default=None
)
skill_version_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "ai_call_skill_version_id", default=None
)
binding_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "ai_call_binding_id", default=None
)
config_source_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_call_config_source", default=None
)
resolved_config_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "ai_call_resolved_config", default=None
)
