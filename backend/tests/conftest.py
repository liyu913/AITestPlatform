from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.deps import get_db
from app.main import app as fastapi_app
from app.models.base import Base

import os

INTEGRATION_REASON = (
    "需要 Redis / MySQL / Playwright 等外部服务，默认跳过；"
    "在完整环境设置 LY_INTEGRATION=1 后运行"
)
requires_integration = pytest.mark.skipif(
    os.environ.get("LY_INTEGRATION") != "1", reason=INTEGRATION_REASON
)

# Default off so existing login tests need no captcha; tests that need it monkeypatch True.
settings.require_login_captcha = False
# Structured AI drafts (design_trace + per-step expected) are on by default in
# production; most unit tests stub the LLM with plain cases, so they opt out and
# the skill tests enable it explicitly.
settings.fast_case_skill_projects = ""

_engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _stub_celery_task_delay():
    # 避免多数 API 测试在 enqueue 时连接真实 Redis；知识库测试模块内仍用 task_always_eager 执行 parse。
    with (
        patch("app.tasks.knowledge.indexing_task.run_index_entity_task.delay"),
        patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay"),
        patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.delay"),
        patch("app.tasks.app_automation.run_app_case.run_app_case_task.delay"),
        patch("app.tasks.perf.run_perf_scenario.run_perf_scenario_task.delay"),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_effective_ai_settings_cache():
    from app.models.ai.system_ai_settings import SystemEmbeddingProfile, SystemLlmProfile, SystemMidsceneProfile
    from app.services.ai.effective_ai_settings import invalidate_runtime_cache

    def _wipe_profiles() -> None:
        db = TestingSessionLocal()
        try:
            for row in db.query(SystemLlmProfile).all():
                db.delete(row)
            for row in db.query(SystemEmbeddingProfile).all():
                db.delete(row)
            for row in db.query(SystemMidsceneProfile).all():
                db.delete(row)
            db.commit()
        finally:
            db.close()

    invalidate_runtime_cache()
    _wipe_profiles()
    yield
    invalidate_runtime_cache()
    _wipe_profiles()


@pytest.fixture(scope="session", autouse=True)
def _sqlite_app_db():
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=_engine)
    fastapi_app.dependency_overrides[get_db] = _override_get_db

    from app.api.api_automation import api_case_agent as case_agent_mod
    from app.api.api_automation import api_case_edit_agent as case_edit_agent_mod
    from app.api.api_automation import api_runs as api_runs_mod
    from app.api.api_automation import api_scenario_agent as scenario_mod
    from app.api.ui_automation import ui_scenario_agent as ui_scenario_mod

    scenario_mod.set_db_factory(TestingSessionLocal)
    case_agent_mod.set_db_factory(TestingSessionLocal)
    case_edit_agent_mod.set_db_factory(TestingSessionLocal)
    api_runs_mod.set_triage_db_factory(TestingSessionLocal)
    ui_scenario_mod.set_db_factory(TestingSessionLocal)

    from app.services.ai import call_log as call_log_mod

    call_log_mod.set_session_factory(TestingSessionLocal)

    yield
    fastapi_app.dependency_overrides.pop(get_db, None)


def get_test_session():
    return TestingSessionLocal()
