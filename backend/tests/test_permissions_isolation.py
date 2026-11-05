"""B7: role permissions and cross-project isolation for the new endpoints."""

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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project(token: str, name: str) -> int:
    r = TestClient(app).post("/api/v1/projects/", headers=_auth(token), json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


def _add_member(client: TestClient, pid: int, h: dict, username: str, role: str) -> None:
    r = client.post(
        f"/api/v1/projects/{pid}/members", headers=h, json={"username": username, "role": role}
    )
    assert r.status_code == 201, r.text


def _seed_assets(client: TestClient, pid: int, h: dict) -> dict:
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "权限用例", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    )
    assert case.status_code == 201, case.text
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "权限计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "权限批次", "status": "active"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case.json()["id"]]},
    ).json()
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{items[0]['id']}",
        headers=h,
        json={"result": "failed", "actual_result": "点击无响应"},
    )
    defect = client.post(
        f"/api/v1/projects/{pid}/defects/from-cycle-item/{items[0]['id']}", headers=h
    )
    assert defect.status_code == 201, defect.text
    report = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle['id']}", headers=h)
    assert report.status_code == 201, report.text
    return {
        "case_id": case.json()["id"],
        "plan_id": plan["id"],
        "cycle_id": cycle["id"],
        "item_id": items[0]["id"],
        "defect_id": defect.json()["id"],
        "report_id": report.json()["id"],
    }


@patch("app.api.functional.testcases.enqueue_reindex")
def test_roles_and_cross_project_isolation(_mock):
    for name in ("perm_owner", "perm_viewer", "perm_editor", "perm_admin", "perm_outsider"):
        _user(name)
    owner_tok = _login("perm_owner")
    viewer_tok = _login("perm_viewer")
    editor_tok = _login("perm_editor")
    admin_tok = _login("perm_admin")
    outsider_tok = _login("perm_outsider")
    client = TestClient(app)
    owner_h = _auth(owner_tok)

    pid = _project(owner_tok, "权限项目")
    _add_member(client, pid, owner_h, "perm_viewer", "viewer")
    _add_member(client, pid, owner_h, "perm_editor", "editor")
    _add_member(client, pid, owner_h, "perm_admin", "admin")
    assets = _seed_assets(client, pid, owner_h)

    # Non-members are refused on every new entry point.
    for path in (
        f"/api/v1/projects/{pid}/workbench",
        f"/api/v1/projects/{pid}/executions",
        f"/api/v1/projects/{pid}/reports",
        f"/api/v1/projects/{pid}/reports/{assets['report_id']}",
        f"/api/v1/projects/{pid}/test-cases/",
        f"/api/v1/projects/{pid}/defects/",
        f"/api/v1/projects/{pid}/test-plans/",
    ):
        denied = client.get(path, headers=_auth(outsider_tok))
        assert denied.status_code in (403, 404), (path, denied.status_code)

    # Viewer can read the new surfaces but cannot write.
    viewer_h = _auth(viewer_tok)
    assert client.get(f"/api/v1/projects/{pid}/workbench", headers=viewer_h).status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/executions", headers=viewer_h).status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/reports", headers=viewer_h).status_code == 200
    read_report = client.get(f"/api/v1/projects/{pid}/reports/{assets['report_id']}", headers=viewer_h)
    assert read_report.status_code == 200
    assert read_report.json()["snapshot"]["cycle"]["name"] == "权限批次"

    assert (
        client.post(
            f"/api/v1/projects/{pid}/test-cases/",
            headers=viewer_h,
            json={"title": "viewer 建的", "steps": []},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/projects/{pid}/reports/cycles/{assets['cycle_id']}", headers=viewer_h
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/projects/{pid}/defects/{assets['defect_id']}/verify",
            headers=viewer_h,
            json={"result": "passed", "fix_build": "b1"},
        ).status_code
        == 403
    )

    # Editor writes assets but cannot hard-delete them (admin only).
    editor_h = _auth(editor_tok)
    made = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=editor_h,
        json={"title": "editor 建的", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    )
    assert made.status_code == 201, made.text
    assert client.delete(f"/api/v1/projects/{pid}/test-cases/{made.json()['id']}", headers=editor_h).status_code == 403
    assert client.delete(f"/api/v1/projects/{pid}/test-plans/{assets['plan_id']}", headers=editor_h).status_code == 403
    # Editor can record a retest (B5 workflow).
    assert (
        client.post(
            f"/api/v1/projects/{pid}/defects/{assets['defect_id']}/verify",
            headers=editor_h,
            json={"result": "passed", "fix_build": "build-2"},
        ).status_code
        == 200
    )

    # Admin may delete.
    admin_h = _auth(admin_tok)
    assert client.delete(f"/api/v1/projects/{pid}/test-cases/{made.json()['id']}", headers=admin_h).status_code == 204

    # Cross-project: another project cannot read this project's report by id.
    other_pid = _project(owner_tok, "另一个项目")
    other_h = owner_h
    assert client.get(f"/api/v1/projects/{other_pid}/reports/{assets['report_id']}", headers=other_h).status_code == 404
    assert client.get(f"/api/v1/projects/{other_pid}/reports/cycles/{assets['cycle_id']}", headers=other_h).status_code == 404
    empty = client.get(f"/api/v1/projects/{other_pid}/executions", headers=other_h).json()
    assert empty["total"] == 0, empty
    other_workbench = client.get(f"/api/v1/projects/{other_pid}/workbench", headers=other_h).json()
    assert all(bucket["count"] == 0 for bucket in other_workbench["buckets"])
