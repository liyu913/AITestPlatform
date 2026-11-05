"""B4 stage 1: batch configuration snapshot, typed assets and execution attempts."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str = "p") -> None:
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str = "p") -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str, name: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _batch(client: TestClient, pid: int, h: dict, **over) -> dict:
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    payload = {"name": "第 1 批", "status": "active"}
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


@patch("app.api.functional.testcases.enqueue_reindex")
def test_batch_snapshots_its_environment(_mock):
    _user("b4_env")
    tok = _login("b4_env")
    pid = _project(tok, "B4ENV")
    client = TestClient(app)
    h = _auth(tok)

    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "预发", "base_url": "https://pre.example.com"},
    )
    assert env.status_code == 201, env.text
    env_id = env.json()["id"]

    cycle = _batch(
        client,
        pid,
        h,
        target_build="build-2026.11.05",
        env_kind="ui",
        env_id=env_id,
        credential_ref="qa-robot",
        data_note="每次执行前重置订单数据",
    )
    assert cycle["target_build"] == "build-2026.11.05"
    assert cycle["env_kind"] == "ui"
    assert cycle["env_id"] == env_id
    assert cycle["credential_ref"] == "qa-robot"
    assert cycle["data_note"].startswith("每次执行前")
    snapshot = cycle["env_snapshot_json"]
    assert snapshot["name"] == "预发"
    assert snapshot["base_url"] == "https://pre.example.com"

    # Changing the environment afterwards must not rewrite the frozen snapshot.
    client.patch(f"/api/v1/projects/{pid}/ui-envs/{env_id}", headers=h, json={"base_url": "https://new.example.com"})
    again = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}", headers=h).json()
    assert again["env_snapshot_json"]["base_url"] == "https://pre.example.com"

    # Switching the batch environment re-freezes it explicitly.
    switched = client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}", headers=h, json={"env_id": env_id}
    )
    assert switched.status_code == 200
    assert switched.json()["env_snapshot_json"]["base_url"] == "https://new.example.com"

    # Another project's environment is rejected.
    other = _project(tok, "B4ENV2")
    other_env = client.post(
        f"/api/v1/projects/{other}/ui-envs/",
        headers=h,
        json={"name": "别的项目", "base_url": "https://other.example.com"},
    ).json()
    bad = client.post(
        f"/api/v1/projects/{pid}/test-plans/{cycle['plan_id']}/cycles/",
        headers=h,
        json={"name": "错环境", "env_kind": "ui", "env_id": other_env["id"]},
    )
    assert bad.status_code == 400


@patch("app.api.functional.testcases.enqueue_reindex")
def test_typed_assets_and_attempt_history(_mock):
    _user("b4_mix")
    tok = _login("b4_mix")
    pid = _project(tok, "B4MIX")
    client = TestClient(app)
    h = _auth(tok)

    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "下单", "steps": [{"order": 1, "action": "提交", "expected": "创建成功"}]},
    ).json()
    api_case = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "查询订单", "path": "/orders/1"},
    )
    assert api_case.status_code == 201, api_case.text
    ui_case = client.post(
        f"/api/v1/projects/{pid}/ui-cases/", headers=h, json={"name": "下单界面"}
    )
    assert ui_case.status_code == 201, ui_case.text

    cycle = _batch(client, pid, h, name="混合批次")
    add = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={
            "case_ids": [case["id"]],
            "assets": [
                {"asset_type": "api_case", "asset_id": api_case.json()["id"]},
                {"asset_type": "ui_case", "asset_id": ui_case.json()["id"]},
            ],
        },
    )
    assert add.status_code == 201, add.text
    items = add.json()
    assert len(items) == 3
    by_type = {i["asset_type"]: i for i in items}
    assert by_type["functional"]["case_title"] == "下单"
    assert by_type["functional"]["asset_id"] == case["id"]
    assert by_type["api_case"]["asset_title"] == "查询订单"
    assert by_type["ui_case"]["asset_title"] == "下单界面"
    # Automation assets carry no manual case id.
    assert by_type["api_case"]["case_id"] is None

    # Re-adding the same assets is a no-op, including across types.
    again = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={
            "case_ids": [case["id"]],
            "assets": [{"asset_type": "api_case", "asset_id": api_case.json()["id"]}],
        },
    )
    assert again.status_code == 201
    assert again.json() == []

    # Unknown asset is rejected instead of silently creating a dangling item.
    missing = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "api_case", "asset_id": 999999}]},
    )
    assert missing.status_code == 400

    item_id = by_type["functional"]["id"]
    base = f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item_id}/attempts"

    running = client.post(
        base,
        headers=h,
        json={"execution_type": "agent_once", "status": "running", "original_run_type": "functional",
              "original_run_id": 77},
    )
    assert running.status_code == 201, running.text
    assert running.json()["attempt_no"] == 1
    assert running.json()["finished_at"] is None
    # A running attempt does not decide the item yet.
    item_now = client.get(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/", headers=h
    )
    assert item_now.status_code == 200, item_now.text
    item_now = item_now.json()
    row = next(i for i in item_now["items"] if i["id"] == item_id)
    assert row["result"] == "untested"
    assert row["last_run_type"] == "functional" and row["last_run_id"] == 77

    failed = client.post(
        base,
        headers=h,
        json={"execution_type": "agent_once", "status": "failed", "summary": "未出现成功提示",
              "original_run_type": "functional", "original_run_id": 78,
              "evidence": {"screenshot_keys": ["1/a.png"]}},
    )
    assert failed.status_code == 201
    assert failed.json()["attempt_no"] == 2

    passed = client.post(
        base,
        headers=h,
        json={"execution_type": "agent_once", "status": "passed", "summary": "重试通过",
              "original_run_type": "functional", "original_run_id": 79},
    )
    assert passed.status_code == 201
    assert passed.json()["attempt_no"] == 3

    history = client.get(base, headers=h).json()
    assert [a["attempt_no"] for a in history] == [1, 2, 3]
    assert [a["status"] for a in history] == ["running", "failed", "passed"]
    assert history[1]["evidence_json"]["screenshot_keys"] == ["1/a.png"]

    # Final state follows the latest finished attempt; the first failure is kept.
    rows = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/", headers=h).json()
    row = next(i for i in rows["items"] if i["id"] == item_id)
    assert row["result"] == "passed"
    assert row["attempt_no"] == 3
    assert row["last_run_id"] == 79
    assert row["actual_result"] == "重试通过"
    case_after = client.get(f"/api/v1/projects/{pid}/test-cases/{case['id']}", headers=h).json()
    assert case_after["last_result"] == "passed"
    assert case_after["lifecycle_status"] == "draft"

    # Cycle level attempt list aggregates the same rows.
    all_attempts = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}/attempts", headers=h).json()
    assert len(all_attempts) == 3

    # An attempt on an automation item never touches a manual case projection.
    api_item_id = by_type["api_case"]["id"]
    api_attempt = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{api_item_id}/attempts",
        headers=h,
        json={"execution_type": "api", "status": "failed", "summary": "500",
              "original_run_type": "api", "original_run_id": 5},
    )
    assert api_attempt.status_code == 201
    rows = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/", headers=h).json()
    api_row = next(i for i in rows["items"] if i["id"] == api_item_id)
    assert api_row["result"] == "failed"
    assert api_row["case_id"] is None

    # Items from another cycle are not addressable through this cycle.
    other_cycle = _batch(client, pid, h, name="另一批")
    wrong = client.post(
        f"/api/v1/projects/{pid}/cycles/{other_cycle['id']}/items/{item_id}/attempts",
        headers=h,
        json={"execution_type": "manual", "status": "passed"},
    )
    assert wrong.status_code == 404
