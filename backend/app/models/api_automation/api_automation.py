from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


# OpenAPI 原文可能超过 MySQL TEXT(64KB)
RawDocText = Text().with_variant(MEDIUMTEXT(), "mysql")


class ApiEnvironment(Base, TimestampMixin):
    __tablename__ = "api_environments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(1024))
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiCase(Base, TimestampMixin):
    __tablename__ = "api_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    api_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_definitions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(16), default="GET")
    path: Mapped[str] = mapped_column(String(1024))
    query_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    body_type: Mapped[str] = mapped_column(String(32), default="none")
    body_text: Mapped[str] = mapped_column(Text, default="")
    extractors_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    assertions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=15000)
    manual_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(64), default="draft")
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    pre_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_script: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApiDefinition(Base, TimestampMixin):
    """Project-scoped API catalog entry (interface management)."""

    __tablename__ = "api_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    module: Mapped[str] = mapped_column(String(255), default="")
    module_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_modules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(16), default="GET")
    path: Mapped[str] = mapped_column(String(1024))
    query_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    body_type: Mapped[str] = mapped_column(String(32), default="none")
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spec_id: Mapped[int | None] = mapped_column(ForeignKey("api_specs.id", ondelete="SET NULL"), nullable=True, index=True)
    spec_endpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_spec_endpoints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ApiSuite(Base, TimestampMixin):
    __tablename__ = "api_suites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    stop_on_fail: Mapped[bool] = mapped_column(Boolean, default=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ApiScenarioAgentSession(Base, TimestampMixin):
    """LangGraph scenario agent session (plan-first, human confirm)."""

    __tablename__ = "api_scenario_agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    prompt: Mapped[str] = mapped_column(Text, default="")
    candidate_case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    candidate_definition_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    suite_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_suites.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("api_runs.id", ondelete="SET NULL"), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")


class ApiCaseAgentSession(Base, TimestampMixin):
    """LangGraph single-API case agent (multi-def matrices, plan-first confirm)."""

    __tablename__ = "api_case_agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    prompt: Mapped[str] = mapped_column(Text, default="")
    candidate_definition_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("api_runs.id", ondelete="SET NULL"), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")


class ApiCaseEditAgentSession(Base, TimestampMixin):
    """LangGraph batch edit agent for existing ApiCase rows (plan-first confirm)."""

    __tablename__ = "api_case_edit_agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    prompt: Mapped[str] = mapped_column(Text, default="")
    case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    base_snapshots_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    applied_case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")


class ApiRun(Base, TimestampMixin):
    __tablename__ = "api_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="case")
    target_id: Mapped[int] = mapped_column(Integer)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApiRunStep(Base, TimestampMixin):
    __tablename__ = "api_run_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("api_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    step_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_name: Mapped[str] = mapped_column(String(512), default="")
    request_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_body_text: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assertion_results_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    extract_results_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")


class ApiSpec(Base, TimestampMixin):
    __tablename__ = "api_specs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(32), default="openapi")
    raw_text: Mapped[str | None] = mapped_column(RawDocText, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ApiSpecEndpoint(Base, TimestampMixin):
    __tablename__ = "api_spec_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("api_specs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(1024))
    operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    request_sample_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ApiSchedule(Base, TimestampMixin):
    __tablename__ = "api_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    suite_id: Mapped[int] = mapped_column(ForeignKey("api_suites.id", ondelete="CASCADE"), index=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True
    )
    cron_expr: Mapped[str] = mapped_column(String(128), default="0 * * * *")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
