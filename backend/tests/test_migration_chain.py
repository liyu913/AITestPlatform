"""B7: the batch's migrations must upgrade and roll back a legacy database.

Covers `91de45b6c284 → head` (B2–B6): backfilled case lifecycle/results, seeded
baseline versions, batch item asset columns, run links, defect triage columns and
report table. Runs on SQLite so it needs no external services.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]
PRE_BATCH_REVISION = "91de45b6c284"

LEGACY_DDL = [
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username VARCHAR(128) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        is_superuser BOOLEAN NOT NULL DEFAULT 0,
        approval_status VARCHAR(32) NOT NULL DEFAULT 'approved',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE projects (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE requirements (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        title VARCHAR(512) NOT NULL,
        body_md TEXT NOT NULL DEFAULT '',
        status VARCHAR(64) NOT NULL DEFAULT 'draft',
        priority VARCHAR(64),
        creator_id INTEGER,
        extracted_text TEXT,
        analysis_json JSON,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_cases (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        requirement_id INTEGER,
        title VARCHAR(512) NOT NULL,
        module VARCHAR(128),
        precondition TEXT,
        test_data TEXT,
        variables_json JSON,
        case_type VARCHAR(32),
        overall_expected TEXT NOT NULL DEFAULT '',
        priority VARCHAR(64),
        status VARCHAR(64) NOT NULL DEFAULT 'draft',
        ai_draft_id VARCHAR(36),
        design_trace JSON,
        tags_json JSON,
        creator_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_case_steps (
        id INTEGER PRIMARY KEY,
        case_id INTEGER NOT NULL REFERENCES test_cases(id),
        "order" INTEGER NOT NULL DEFAULT 0,
        action TEXT NOT NULL,
        expected TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_plans (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        name VARCHAR(512) NOT NULL,
        version VARCHAR(128),
        iteration VARCHAR(128),
        description TEXT,
        status VARCHAR(64) NOT NULL DEFAULT 'draft',
        requirement_id INTEGER,
        body_md TEXT,
        creator_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_cycles (
        id INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL REFERENCES test_plans(id),
        name VARCHAR(512) NOT NULL,
        status VARCHAR(64) NOT NULL DEFAULT 'draft',
        creator_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_cycle_items (
        id INTEGER PRIMARY KEY,
        cycle_id INTEGER NOT NULL REFERENCES test_cycles(id),
        case_id INTEGER NOT NULL REFERENCES test_cases(id),
        assignee_id INTEGER,
        result VARCHAR(64) NOT NULL DEFAULT 'untested',
        actual_result TEXT NOT NULL DEFAULT '',
        executed_by_id INTEGER,
        executed_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_cycle_case UNIQUE (cycle_id, case_id)
    )""",
    """CREATE TABLE api_runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        trigger_type VARCHAR(32) NOT NULL DEFAULT 'case',
        target_id INTEGER NOT NULL,
        env_id INTEGER,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        summary_json JSON,
        triggered_by INTEGER,
        celery_task_id VARCHAR(128),
        started_at VARCHAR(64),
        finished_at VARCHAR(64),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE ui_runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        trigger_type VARCHAR(32) NOT NULL DEFAULT 'case',
        target_id INTEGER NOT NULL,
        env_id INTEGER,
        browser VARCHAR(32) NOT NULL DEFAULT 'chromium',
        headless BOOLEAN NOT NULL DEFAULT 1,
        account_name VARCHAR(128),
        queue_name VARCHAR(64) NOT NULL DEFAULT 'ui',
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        summary_json JSON,
        triggered_by INTEGER,
        celery_task_id VARCHAR(128),
        started_at VARCHAR(64),
        finished_at VARCHAR(64),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE test_case_runs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        case_id INTEGER NOT NULL REFERENCES test_cases(id),
        triggered_by INTEGER,
        mode VARCHAR(32) NOT NULL DEFAULT 'agent_once',
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        env_id INTEGER,
        session_id INTEGER,
        summary TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        actual_result TEXT NOT NULL DEFAULT '',
        summary_json JSON,
        started_at DATETIME,
        finished_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE defects (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        title VARCHAR(512) NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        severity VARCHAR(64) NOT NULL DEFAULT 'P2',
        status VARCHAR(64) NOT NULL DEFAULT 'open',
        case_id INTEGER,
        cycle_item_id INTEGER,
        requirement_id INTEGER,
        reporter_id INTEGER,
        assignee_id INTEGER,
        external_system VARCHAR(64),
        external_id VARCHAR(256),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
]


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path.as_posix()}")
    return cfg


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    for ddl in LEGACY_DDL:
        conn.execute(ddl)
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'legacy', 'x')")
    conn.execute("INSERT INTO projects (id, name, owner_id) VALUES (1, 'Legacy Project', 1)")
    conn.execute(
        "INSERT INTO requirements (id, project_id, title, body_md, status) "
        "VALUES (1, 1, '登录需求', '正文', 'active')"
    )
    conn.execute(
        "INSERT INTO test_cases (id, project_id, requirement_id, title, overall_expected, status) "
        "VALUES (1, 1, 1, '登录用例', '进入首页', 'passed')"
    )
    conn.execute(
        "INSERT INTO test_cases (id, project_id, requirement_id, title, overall_expected, status) "
        "VALUES (2, 1, 1, '草稿用例', '', 'draft')"
    )
    conn.execute(
        'INSERT INTO test_case_steps (id, case_id, "order", action, expected) '
        "VALUES (1, 1, 0, '输入账号', '表单可见')"
    )
    conn.execute("INSERT INTO test_plans (id, project_id, name, version) VALUES (1, 1, '回归计划', '1.0.0')")
    conn.execute("INSERT INTO test_cycles (id, plan_id, name, status) VALUES (1, 1, '第一批', 'active')")
    conn.execute(
        "INSERT INTO test_cycle_items (id, cycle_id, case_id, result, actual_result) "
        "VALUES (1, 1, 1, 'failed', '未跳转')"
    )
    conn.execute(
        "INSERT INTO defects (id, project_id, title, status, case_id, cycle_item_id) "
        "VALUES (1, 1, '登录失败', 'failed', 1, 1)"
    )
    conn.execute("INSERT INTO defects (id, project_id, title, status, case_id, cycle_item_id) VALUES (2, 1, '重复', 'open', 1, 1)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("ALEMBIC_SYNC_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")
    cfg = _alembic_config(db_path)
    command.stamp(cfg, PRE_BATCH_REVISION)
    return db_path, cfg


def test_batch_migrations_upgrade_backfill_and_downgrade(legacy_db):
    db_path, cfg = legacy_db
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # B2: lifecycle split + baseline versions.
    rows = {
        int(r["id"]): dict(r)
        for r in conn.execute("SELECT id, status, lifecycle_status, last_result FROM test_cases")
    }
    assert rows[1]["lifecycle_status"] == "confirmed"
    assert rows[1]["last_result"] == "passed"
    assert rows[1]["status"] == "confirmed"  # legacy mirror
    assert rows[2]["lifecycle_status"] == "draft"
    assert rows[2]["last_result"] is None
    versions = conn.execute("SELECT case_id, version_no, change_source FROM test_case_versions").fetchall()
    assert {(int(v["case_id"]), int(v["version_no"])) for v in versions} == {(1, 1), (2, 1)}
    assert all(v["change_source"] == "migration" for v in versions)
    req_cols = {r["name"] for r in conn.execute("PRAGMA table_info(requirements)")}
    assert {"acceptance_rules", "version_no"} <= req_cols

    # B4: batch item becomes a typed asset, legacy item keeps its case link.
    item = conn.execute(
        "SELECT case_id, asset_type, asset_id, attempt_no FROM test_cycle_items WHERE id = 1"
    ).fetchone()
    assert item["asset_type"] == "functional"
    assert item["asset_id"] == 1
    assert item["attempt_no"] == 0
    run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_runs)")}
    assert "cycle_item_id" in run_cols

    # B5: defect triage backfill + one failure source per item + history baseline.
    defect = conn.execute(
        "SELECT category, failure_source_type, failure_source_id FROM defects WHERE id = 1"
    ).fetchone()
    assert defect["category"] == "product"
    assert defect["failure_source_type"] == "cycle_item"
    assert defect["failure_source_id"] == 1
    dup = conn.execute("SELECT failure_source_type FROM defects WHERE id = 2").fetchone()
    assert dup["failure_source_type"] is None
    history = conn.execute("SELECT COUNT(*) AS n FROM defect_status_history").fetchone()
    assert int(history["n"]) == 2

    # B6: report table is usable and revisions are unique per cycle.
    conn.execute(
        "INSERT INTO test_cycle_reports (project_id, cycle_id, revision, generated_at, summary_text) "
        "VALUES (1, 1, 1, '2026-11-05 10:00:00', 'r1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO test_cycle_reports (project_id, cycle_id, revision, generated_at, summary_text) "
            "VALUES (1, 1, 1, '2026-11-05 10:00:00', 'dup')"
        )
    conn.rollback()
    conn.close()

    # Roll back the whole batch: our columns/tables disappear, legacy data stays.
    command.downgrade(cfg, PRE_BATCH_REVISION)
    conn = sqlite3.connect(db_path)
    case_cols = {r[1] for r in conn.execute("PRAGMA table_info(test_cases)")}
    assert "lifecycle_status" not in case_cols
    assert "last_result" not in case_cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "test_cycle_reports" not in tables
    assert "defect_status_history" not in tables
    assert "test_case_versions" not in tables
    # Non-functional batch items cannot survive once case_id is required again.
    remaining_items = conn.execute("SELECT COUNT(*) FROM test_cycle_items").fetchone()[0]
    assert remaining_items == 1
    remaining_defects = conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert remaining_defects == 2
    conn.close()
