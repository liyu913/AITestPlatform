"""R3/R6: project evidence search and scheme relation counts."""

from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.functional.test_plan import TestCycleAttempt
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


@patch("app.api.functional.testcases.enqueue_reindex")
def test_evidence_search_filters_and_scheme_plan_count(_mock):
    _user("r6_ev")
    tok = _login("r6_ev")
    pid = _project(tok, "R6-EVIDENCE")
    client = TestClient(app)
    h = _auth(tok)

    scheme = client.post(
        f"/api/v1/projects/{pid}/schemes/",
        headers=h,
        json={"name": "证据方案", "scope": "主链路"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "证据用例",
            "overall_expected": "成功",
            "steps": [{"order": 1, "action": "操作", "expected": "成功"}],
        },
    ).json()
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/",
        headers=h,
        json={"name": "证据计划", "scheme_version_id": scheme["versions"][0]["id"]},
    ).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "证据批次", "target_build": "b1", "env_kind": "ui"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "functional", "asset_id": case["id"]}]},
    ).json()
    item_id = items[0]["id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TestCycleAttempt(
                project_id=pid,
                cycle_id=cycle["id"],
                cycle_item_id=item_id,
                asset_type="functional",
                asset_id=case["id"],
                execution_type="manual",
                attempt_no=1,
                status="passed",
                summary="执行通过",
                evidence_json={"screenshots": ["a.png", "b.png"]},
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
        )
        db.commit()
    finally:
        db.close()

    rows = client.get(f"/api/v1/projects/{pid}/evidence/", headers=h).json()
    assert rows["total"] == 1
    row = rows["items"][0]
    assert row["plan_name"] == "证据计划"
    assert row["case_id"] == case["id"]
    assert row["case_title"] == "证据用例"
    assert row["status"] == "passed"
    assert row["evidence"]["screenshots"] == ["a.png", "b.png"]

    by_plan = client.get(
        f"/api/v1/projects/{pid}/evidence/?plan_id={plan['id']}", headers=h
    ).json()
    assert by_plan["total"] == 1
    by_cycle = client.get(
        f"/api/v1/projects/{pid}/evidence/?cycle_id={cycle['id']}", headers=h
    ).json()
    assert by_cycle["total"] == 1
    by_case = client.get(
        f"/api/v1/projects/{pid}/evidence/?case_id={case['id']}", headers=h
    ).json()
    assert by_case["total"] == 1
    none = client.get(f"/api/v1/projects/{pid}/evidence/?case_id=99999", headers=h).json()
    assert none["total"] == 0

    other = _project(tok, "R6-EVIDENCE-OTHER")
    isolated = client.get(f"/api/v1/projects/{other}/evidence/", headers=h).json()
    assert isolated["total"] == 0

    scheme_after = client.get(f"/api/v1/projects/{pid}/schemes/{scheme['id']}", headers=h).json()
    assert scheme_after["plan_count"] == 1
