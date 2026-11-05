from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UiEnvironment(Base, TimestampMixin):
    __tablename__ = "ui_environments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(1024))
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    browsers_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    viewport_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    accounts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    auth_states_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_inject: Mapped[str] = mapped_column(String(32), default="none")
    token_storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    inject_headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    inject_local_storage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    queue_name: Mapped[str] = mapped_column(String(64), default="ui")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    default_timeout_ms: Mapped[int] = mapped_column(Integer, default=15000)
    step_retries: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class UiElement(Base, TimestampMixin):
    """Page Object / element repository entry."""

    __tablename__ = "ui_elements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    page_name: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(128))
    selector: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text, default="")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class UiCase(Base, TimestampMixin):
    __tablename__ = "ui_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="", server_default="")
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    auth_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manual_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(64), default="draft")
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class UiSuite(Base, TimestampMixin):
    __tablename__ = "ui_suites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    case_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    stop_on_fail: Mapped[bool] = mapped_column(Boolean, default=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True
    )
    browsers_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class UiSchedule(Base, TimestampMixin):
    __tablename__ = "ui_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    suite_id: Mapped[int] = mapped_column(ForeignKey("ui_suites.id", ondelete="CASCADE"), index=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True
    )
    cron_expr: Mapped[str] = mapped_column(String(128), default="0 * * * *")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class UiRun(Base, TimestampMixin):
    __tablename__ = "ui_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="case")
    target_id: Mapped[int] = mapped_column(Integer)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    headless: Mapped[bool] = mapped_column(Boolean, default=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queue_name: Mapped[str] = mapped_column(String(64), default="ui")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Batch item this run belongs to (B4); results are mirrored into an attempt.
    cycle_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UiScenarioAgentSession(Base, TimestampMixin):
    """Conversational UI automation agent (plan → explore → confirm → UiCase)."""

    __tablename__ = "ui_scenario_agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    testcase_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    result_steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    playwright_code: Mapped[str] = mapped_column(Text, default="")
    credentials_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dry_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dry_run_error: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    saved_ui_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_cases.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ui_runs.id", ondelete="SET NULL"), nullable=True)


class UiAiExploreJob(Base, TimestampMixin):
    """AI-assisted UI exploration job: functional case → UiSteps + Playwright code."""

    __tablename__ = "ui_ai_explore_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    testcase_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    plan_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    trace_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    result_steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    playwright_code: Mapped[str] = mapped_column(Text, default="")
    dry_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dry_run_error: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    saved_ui_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_cases.id", ondelete="SET NULL"), nullable=True
    )
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class UiRunStep(Base, TimestampMixin):
    __tablename__ = "ui_run_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ui_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_name: Mapped[str] = mapped_column(String(512), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    selector: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screenshot_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
