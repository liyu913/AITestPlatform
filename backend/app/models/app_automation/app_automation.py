from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AppEnvironment(Base, TimestampMixin):
    __tablename__ = "app_environments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), default="android")
    appium_server_url: Mapped[str] = mapped_column(String(1024), default="http://127.0.0.1:4723")
    device_name: Mapped[str] = mapped_column(String(255), default="Android Emulator")
    udid: Mapped[str] = mapped_column(String(255), default="")
    platform_version: Mapped[str] = mapped_column(String(64), default="")
    automation_name: Mapped[str] = mapped_column(String(64), default="UiAutomator2")
    app_package: Mapped[str] = mapped_column(String(255), default="")
    app_activity: Mapped[str] = mapped_column(String(255), default="")
    bundle_id: Mapped[str] = mapped_column(String(255), default="")
    app_path: Mapped[str] = mapped_column(String(1024), default="")
    no_reset: Mapped[bool] = mapped_column(Boolean, default=True)
    full_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    caps_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    accounts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    queue_name: Mapped[str] = mapped_column(String(64), default="app")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    default_timeout_ms: Mapped[int] = mapped_column(Integer, default=15000)
    step_retries: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Prefer devices in pool matching these tags (empty = any online device for platform)
    device_tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    use_device_pool: Mapped[bool] = mapped_column(Boolean, default=True)


class AppDevice(Base, TimestampMixin):
    """Physical/emulator device in the project device pool."""

    __tablename__ = "app_devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), default="android")
    udid: Mapped[str] = mapped_column(String(255), default="")
    device_name: Mapped[str] = mapped_column(String(255), default="")
    platform_version: Mapped[str] = mapped_column(String(64), default="")
    appium_server_url: Mapped[str] = mapped_column(String(1024), default="")
    automation_name: Mapped[str] = mapped_column(String(64), default="")
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # online | busy | offline | disabled
    status: Mapped[str] = mapped_column(String(32), default="offline", index=True)
    worker_id: Mapped[str] = mapped_column(String(128), default="")
    last_heartbeat_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AppElement(Base, TimestampMixin):
    """Page Object / element repository entry for mobile."""

    __tablename__ = "app_elements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    page_name: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(128))
    strategy: Mapped[str] = mapped_column(String(64), default="id")
    selector: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text, default="")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AppCase(Base, TimestampMixin):
    __tablename__ = "app_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    manual_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(64), default="draft")
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AppRun(Base, TimestampMixin):
    __tablename__ = "app_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="case")
    target_id: Mapped[int] = mapped_column(Integer)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    device_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="android")
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queue_name: Mapped[str] = mapped_column(String(64), default="app")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AppRunStep(Base, TimestampMixin):
    __tablename__ = "app_run_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("app_runs.id", ondelete="CASCADE"), index=True)
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
