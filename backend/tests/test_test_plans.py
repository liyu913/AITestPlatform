from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str, name: str = "PlanProj") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@patch("app.api.functional.testcases.enqueue_reindex")
def test_plan_cycle_execute_defect_coverage(mock_rq, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_dir", str(tmp_path))

    _user("plan_adm", "a")
    tok = _login("plan_adm", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)

    # requirement + cases
    rr = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录", "status": "active"},
    )
    assert rr.status_code == 201
    rid = rr.json()["id"]

    c1 = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "正常登录", "requirement_id": rid, "steps": [{"action": "a", "expected": "e"}]},
    )
    assert c1.status_code == 201
    case1 = c1.json()["id"]

    c2 = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "错误密码", "requirement_id": rid, "steps": [{"action": "a", "expected": "e"}]},
    )
    assert c2.status_code == 201
    case2 = c2.json()["id"]

    # plan + cycle
    pr = client.post(
        f"/api/v1/projects/{pid}/test-plans/",
        headers=h,
        json={"name": "v1.0 回归", "version": "1.0.0", "iteration": "Sprint-12", "status": "active"},
    )
    assert pr.status_code == 201
    plan_id = pr.json()["id"]
    assert pr.json()["version"] == "1.0.0"

    cr = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan_id}/cycles/",
        headers=h,
        json={"name": "第1轮", "status": "active"},
    )
    assert cr.status_code == 201
    cycle_id = cr.json()["id"]
    assert cr.json()["item_count"] == 0

    add = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items",
        headers=h,
        json={"case_ids": [case1, case2]},
    )
    assert add.status_code == 201
    assert len(add.json()) == 2
    item1 = add.json()[0]["id"]
    item2 = add.json()[1]["id"]

    # duplicate add skipped
    add2 = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items",
        headers=h,
        json={"case_ids": [case1]},
    )
    assert add2.status_code == 201
    assert add2.json() == []

    # assign
    me = client.get("/api/v1/users/me", headers=h)
    uid = me.json()["id"]
    asn = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/assign",
        headers=h,
        json={"item_ids": [item1, item2], "assignee_id": uid},
    )
    assert asn.status_code == 200
    assert all(x["assignee_id"] == uid for x in asn.json())

    # execute: pass + fail
    p1 = client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item1}",
        headers=h,
        json={"result": "passed", "actual_result": "登录成功"},
    )
    assert p1.status_code == 200
    assert p1.json()["result"] == "passed"
    assert p1.json()["executed_by_id"] == uid

    p2 = client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item2}",
        headers=h,
        json={"result": "failed", "actual_result": "未提示错误"},
    )
    assert p2.status_code == 200
    assert p2.json()["result"] == "failed"

    # execution result is projected onto the case; lifecycle is untouched
    g1 = client.get(f"/api/v1/projects/{pid}/test-cases/{case1}", headers=h)
    assert g1.json()["last_result"] == "passed"
    assert g1.json()["lifecycle_status"] == "draft"
    assert g1.json()["status"] == "draft"
    g2 = client.get(f"/api/v1/projects/{pid}/test-cases/{case2}", headers=h)
    assert g2.json()["last_result"] == "failed"
    assert g2.json()["lifecycle_status"] == "draft"

    # attachment
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item2}/attachments",
        headers=h,
        files={"file": ("fail.png", BytesIO(png), "image/png")},
    )
    assert up.status_code == 201
    att_id = up.json()["id"]

    dl = client.get(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item2}/attachments/{att_id}/file",
        headers=h,
    )
    assert dl.status_code == 200
    assert dl.content.startswith(b"\x89PNG")

    # one-click defect (copies execution screenshots)
    df = client.post(
        f"/api/v1/projects/{pid}/defects/from-cycle-item/{item2}",
        headers=h,
    )
    assert df.status_code == 201
    assert df.json()["title"].startswith("[失败]")
    assert df.json()["case_id"] == case2
    assert df.json()["cycle_item_id"] == item2
    assert df.json()["requirement_id"] == rid
    assert len(df.json()["attachments"]) == 1
    defect_id = df.json()["id"]
    d_att_id = df.json()["attachments"][0]["id"]

    d_dl = client.get(
        f"/api/v1/projects/{pid}/defects/{defect_id}/attachments/{d_att_id}/file",
        headers=h,
    )
    assert d_dl.status_code == 200
    assert d_dl.content.startswith(b"\x89PNG")

    # manual defect attachment upload
    txt = client.post(
        f"/api/v1/projects/{pid}/defects/{defect_id}/attachments",
        headers=h,
        files={"file": ("note.txt", BytesIO(b"repro steps"), "text/plain")},
    )
    assert txt.status_code == 201
    assert txt.json()["filename"] == "note.txt"

    gdef = client.get(f"/api/v1/projects/{pid}/defects/{defect_id}", headers=h)
    assert gdef.status_code == 200
    assert len(gdef.json()["attachments"]) == 2

    # cannot create from passed
    bad = client.post(
        f"/api/v1/projects/{pid}/defects/from-cycle-item/{item1}",
        headers=h,
    )
    assert bad.status_code == 400

    # coverage
    cov = client.get(f"/api/v1/projects/{pid}/coverage/requirements", headers=h)
    assert cov.status_code == 200
    rows = cov.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["requirement_id"] == rid
    assert row["case_total"] == 2
    assert row["passed"] == 1
    assert row["failed"] == 1
    assert row["untested"] == 0
    assert row["blind"] is True  # has failed
    assert row["coverage_ratio"] == 1.0

    cov_cycle = client.get(
        f"/api/v1/projects/{pid}/coverage/requirements",
        headers=h,
        params={"cycle_id": cycle_id},
    )
    assert cov_cycle.status_code == 200
    assert cov_cycle.json()[0]["passed"] == 1

    # cycle stats
    gc = client.get(f"/api/v1/projects/{pid}/cycles/{cycle_id}", headers=h)
    assert gc.status_code == 200
    assert gc.json()["item_count"] == 2
    assert gc.json()["passed_count"] == 1
    assert gc.json()["failed_count"] == 1

    # defect list
    dlst = client.get(f"/api/v1/projects/{pid}/defects/", headers=h)
    assert dlst.status_code == 200
    assert dlst.json()["total"] == 1
