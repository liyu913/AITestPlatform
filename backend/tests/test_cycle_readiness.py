"""R3: execution readiness gate and review assignment."""

from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str = "p") -> int:
    db = TestingSessionLocal()
    try:
        row = User(username=username, password_hash=auth_service.hash_password(pw), is_active=True)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
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


def _case(client: TestClient, pid: int, h: dict, title: str = "支付回调幂等") -> dict:
    r = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": title,
            "overall_expected": "只处理一次",
            "steps": [{"order": 1, "action": "重复回调", "expected": "仅一次生效"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _cycle_with_item(client: TestClient, pid: int, h: dict, case_id: int) -> int:
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "1.2 回归计划"}
    )
    assert plan.status_code == 201, plan.text
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan.json()['id']}/cycles/",
        headers=h,
        json={"name": "回归批次", "target_build": "build-1", "env_kind": "api"},
    )
    assert cycle.status_code == 201, cycle.text
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle.json()['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "functional", "asset_id": case_id}]},
    )
    assert items.status_code == 201, items.text
    return cycle.json()["id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_readiness_blocks_unpublished_case_and_start_passes_after_publish(_mock):
    _user("r3_ready")
    tok = _login("r3_ready")
    pid = _project(tok, "R3-READY")
    client = TestClient(app)
    h = _auth(tok)
    case = _case(client, pid, h)
    cid = _cycle_with_item(client, pid, h, case["id"])

    readiness = client.get(f"/api/v1/projects/{pid}/cycles/{cid}/readiness", headers=h)
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    assert body["ready"] is False
    assert body["blockers"][0]["reason"] == "用例尚未发布正式版本"
    assert body["blockers"][0]["case_id"] == case["id"]

    blocked = client.post(f"/api/v1/projects/{pid}/cycles/{cid}/start", headers=h)
    assert blocked.status_code == 409, blocked.text

    # Publish the approved version, then the batch becomes ready.
    client.post(f"/api/v1/projects/{pid}/test-cases/{case['id']}/submit-review", headers=h)
    client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/review",
        headers=h,
        json={"decision": "approved", "comment": "ok"},
    )
    client.post(f"/api/v1/projects/{pid}/test-cases/{case['id']}/publish", headers=h, json={})

    body = client.get(f"/api/v1/projects/{pid}/cycles/{cid}/readiness", headers=h).json()
    assert body["ready"] is True, body

    started = client.post(f"/api/v1/projects/{pid}/cycles/{cid}/start", headers=h)
    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["cycle"]["status"] == "active"
    assert payload["cycle"]["started_at"] is not None
    assert payload["readiness"]["ready"] is True

    # Starting again is idempotent and keeps the original start time.
    again = client.post(f"/api/v1/projects/{pid}/cycles/{cid}/start", headers=h)
    assert again.status_code == 200
    assert again.json()["cycle"]["started_at"] == payload["cycle"]["started_at"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_review_assignment_shows_in_queue_and_clears_on_decision(_mock):
    uid = _user("r3_review")
    tok = _login("r3_review")
    pid = _project(tok, "R3-REVIEW")
    client = TestClient(app)
    h = _auth(tok)

    case = _case(client, pid, h, title="退款金额校验")
    due = (datetime.now() + timedelta(days=2)).isoformat()
    submitted = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/submit-review",
        headers=h,
        json={"reviewer_id": uid, "due_at": due},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["assigned_reviewer_id"] == uid

    queue = client.get(f"/api/v1/projects/{pid}/test-cases/review-queue", headers=h).json()
    row = next(item for item in queue if item["case_id"] == case["id"])
    assert row["assigned_reviewer_id"] == uid
    assert row["assigned_reviewer_name"] == "r3_review"
    assert row["due_at"] is not None

    # A non-member reviewer is rejected.
    outsider = _user("r3_outsider")
    rejected = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/submit-review",
        headers=h,
        json={"reviewer_id": outsider},
    )
    assert rejected.status_code == 400, rejected.text

    client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/review",
        headers=h,
        json={"decision": "approved", "comment": "ok"},
    )
    after = client.get(f"/api/v1/projects/{pid}/test-cases/{case['id']}", headers=h).json()
    assert after["assigned_reviewer_id"] is None
    assert after["review_due_at"] is None

@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay")
def test_run_with_cycle_item_requires_started_cycle(_delay):
    from unittest.mock import MagicMock

    stub = MagicMock()
    stub.id = "task-x"
    _delay.return_value = stub

    _user("r3_gate")
    tok = _login("r3_gate")
    pid = _project(tok, "R3-GATE")
    client = TestClient(app)
    h = _auth(tok)

    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "env", "base_url": "https://api.example.com"},
    )
    assert env.status_code == 201, env.text
    case = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "查询", "path": "/get"},
    )
    assert case.status_code == 201, case.text
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "p"})
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan.json()['id']}/cycles/",
        headers=h,
        json={"name": "draft cycle"},
    )
    assert cycle.status_code == 201, cycle.text
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle.json()['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "api_case", "asset_id": case.json()["id"]}]},
    )
    assert items.status_code == 201, items.text
    item_id = items.json()[0]["id"]

    payload = {"cycle_item_id": item_id, "env_id": env.json()["id"], "sync": False}
    blocked = client.post(
        f"/api/v1/projects/{pid}/api-cases/{case.json()['id']}/run", headers=h, json=payload
    )
    assert blocked.status_code == 409, blocked.text

    started = client.post(f"/api/v1/projects/{pid}/cycles/{cycle.json()['id']}/start", headers=h)
    assert started.status_code == 200, started.text

    allowed = client.post(
        f"/api/v1/projects/{pid}/api-cases/{case.json()['id']}/run", headers=h, json=payload
    )
    assert allowed.status_code == 201, allowed.text
