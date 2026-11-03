from fastapi.testclient import TestClient

from app.main import app
from app.models.api_automation.api_automation import ApiCase, ApiRun, ApiSuite
from app.models.functional.defect import Defect
from app.models.functional.requirement import Requirement
from app.models.functional.test_plan import TestPlan as PlanRow
from app.models.functional.testcase import TestCase as CaseRow
from app.models.functional.testcase_run import TestCaseRun as CaseRunRow
from app.models.identity.user import User
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.project.project import ProjectModule
from app.models.ui_automation.ui_automation import UiCase, UiRun, UiSuite
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, password: str) -> None:
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(password),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()


def _login(username: str, password: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _project(token: str, name: str = "Dash") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _empty_trend() -> dict:
    return {"points": [], "totals": {"total": 0, "passed": 0, "failed": 0, "error": 0}}


def test_dashboard_empty_project():
    _user("dash_empty", "p")
    tok = _login("dash_empty", "p")
    pid = _project(tok)
    client = TestClient(app)
    r = client.get(
        f"/api/v1/projects/{pid}/dashboard",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assets = body["assets"]
    for key in (
        "requirements",
        "test_cases",
        "defects",
        "test_plans",
        "knowledge_documents",
        "modules",
        "api_cases",
        "api_suites",
        "ui_cases",
        "ui_suites",
    ):
        assert assets[key]["total"] == 0
    assert assets["members"]["total"] == 1
    assert assets["members"]["by_status"]["admin"] == 1
    assert body["coverage"] == {"requirement_total": 0, "blind_count": 0, "avg_ratio": 0.0}
    assert body["runs"]["functional"] == _empty_trend()
    assert body["runs"]["api"] == _empty_trend()
    assert body["runs"]["ui"] == _empty_trend()
    assert body["recent_runs"] == []


def test_dashboard_forbidden_for_non_member():
    _user("dash_owner", "o")
    _user("dash_stranger", "s")
    owner_tok = _login("dash_owner", "o")
    pid = _project(owner_tok, "Private")
    other_tok = _login("dash_stranger", "s")
    r = TestClient(app).get(
        f"/api/v1/projects/{pid}/dashboard",
        headers={"Authorization": f"Bearer {other_tok}"},
    )
    assert r.status_code == 403


def test_dashboard_counts_trends_and_recent():
    _user("dash_data", "d")
    tok = _login("dash_data", "d")
    pid = _project(tok, "Stats")

    db = TestingSessionLocal()
    try:
        req = Requirement(project_id=pid, title="R1", body_md="", status="active")
        db.add(req)
        db.flush()
        case = CaseRow(project_id=pid, requirement_id=req.id, title="C1", overall_expected="")
        db.add(case)
        db.add(Requirement(project_id=pid, title="R2", body_md="", status="draft"))
        db.add(Defect(project_id=pid, title="Bug", description="", status="open"))
        db.add(Defect(project_id=pid, title="Done", description="", status="closed"))
        db.add(PlanRow(project_id=pid, name="Plan"))
        db.add(ProjectModule(project_id=pid, name="Mod"))
        db.add(ApiCase(project_id=pid, name="API-1", path="/ping"))
        db.add(ApiSuite(project_id=pid, name="Suite-A"))
        db.add(UiCase(project_id=pid, name="UI-1"))
        db.add(UiSuite(project_id=pid, name="UI-Suite"))
        lib = db.query(KnowledgeLibrary).filter(KnowledgeLibrary.project_id == pid).one()
        db.add(
            KnowledgeDocument(
                project_id=pid,
                library_id=lib.id,
                filename="doc.md",
                storage_path="doc.md",
                parse_status="ready",
            )
        )
        db.flush()
        db.add(CaseRunRow(project_id=pid, case_id=case.id, status="passed"))
        db.add(CaseRunRow(project_id=pid, case_id=case.id, status="failed"))
        db.add(ApiRun(project_id=pid, trigger_type="case", target_id=1, status="passed"))
        db.add(UiRun(project_id=pid, trigger_type="case", target_id=1, status="error"))
        db.commit()
        case_id = case.id
    finally:
        db.close()

    r = TestClient(app).get(
        f"/api/v1/projects/{pid}/dashboard?days=14",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assets = body["assets"]
    assert assets["requirements"]["total"] == 2
    assert assets["requirements"]["by_status"]["active"] == 1
    assert assets["requirements"]["by_status"]["draft"] == 1
    assert assets["test_cases"]["total"] == 1
    assert assets["defects"]["total"] == 2
    assert assets["defects"]["by_status"]["open"] == 1
    assert assets["defects"]["by_status"]["closed"] == 1
    assert assets["test_plans"]["total"] == 1
    assert assets["modules"]["total"] == 1
    assert assets["api_cases"]["total"] == 1
    assert assets["api_suites"]["total"] == 1
    assert assets["ui_cases"]["total"] == 1
    assert assets["ui_suites"]["total"] == 1
    assert assets["knowledge_documents"]["total"] == 1
    assert assets["knowledge_documents"]["by_status"]["ready"] == 1

    coverage = body["coverage"]
    assert coverage["requirement_total"] == 2
    assert coverage["blind_count"] == 2
    assert coverage["avg_ratio"] == 0.0

    assert body["runs"]["functional"]["totals"]["total"] == 2
    assert body["runs"]["functional"]["totals"]["passed"] == 1
    assert body["runs"]["functional"]["totals"]["failed"] == 1
    assert body["runs"]["api"]["totals"]["passed"] == 1
    assert body["runs"]["ui"]["totals"]["error"] == 1
    assert body["runs"]["functional"]["points"]

    kinds = {item["kind"] for item in body["recent_runs"]}
    assert kinds == {"functional", "api", "ui"}
    assert any(item["kind"] == "functional" and item["status"] == "failed" for item in body["recent_runs"])
    assert all("created_at" in item and item["id"] for item in body["recent_runs"])
    assert case_id > 0


def test_dashboard_days_bounds():
    _user("dash_days", "x")
    tok = _login("dash_days", "x")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/api/v1/projects/{pid}/dashboard?days=0", headers=h).status_code == 422
    assert client.get(f"/api/v1/projects/{pid}/dashboard?days=91", headers=h).status_code == 422
