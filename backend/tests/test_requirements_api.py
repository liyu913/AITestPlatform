import json
import os
import struct
import zlib
from io import BytesIO
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.models.functional.requirement import Requirement
from app.services.knowledge.document_extract import MAX_RAW_BYTES
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


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "PR"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@patch("app.api.functional.requirements.enqueue_reindex")
def test_create_and_update_requirement_calls_reindex(mock_rq):
    _user("req_admin", "a")
    tok = _login("req_admin", "a")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "R1", "body_md": "body"},
    )
    assert r.status_code == 201
    rid = r.json()["id"]
    mock_rq.assert_called_with(pid, "requirement", rid)

    mock_rq.reset_mock()
    r2 = client.patch(
        f"/api/v1/projects/{pid}/requirements/{rid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "R2"},
    )
    assert r2.status_code == 200
    mock_rq.assert_called_with(pid, "requirement", rid)


def test_viewer_read_only_on_requirements():
    _user("req_owner", "o")
    _user("req_view", "v")
    otok = _login("req_owner", "o")
    pid = _project(otok)
    client = TestClient(app)
    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {otok}"},
        json={"username": "req_view", "role": "viewer"},
    )
    vtok = _login("req_view", "v")

    r = client.get(f"/api/v1/projects/{pid}/requirements/", headers={"Authorization": f"Bearer {vtok}"})
    assert r.status_code == 200

    r2 = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {vtok}"},
        json={"title": "X", "body_md": "y"},
    )
    assert r2.status_code == 403


def test_only_admin_deletes_requirement():
    _user("req_adm", "a")
    _user("req_ed", "e")
    atok = _login("req_adm", "a")
    pid = _project(atok)
    client = TestClient(app)
    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {atok}"},
        json={"username": "req_ed", "role": "editor"},
    )
    etok = _login("req_ed", "e")
    cr = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {etok}"},
        json={"title": "Del", "body_md": "b"},
    )
    rid = cr.json()["id"]

    r_del = client.delete(
        f"/api/v1/projects/{pid}/requirements/{rid}",
        headers={"Authorization": f"Bearer {etok}"},
    )
    assert r_del.status_code == 403

    r_ok = client.delete(
        f"/api/v1/projects/{pid}/requirements/{rid}",
        headers={"Authorization": f"Bearer {atok}"},
    )
    assert r_ok.status_code == 204


@patch("app.api.functional.requirements.enqueue_reindex")
def test_list_requirements_pagination_and_search(_mock_rq):
    _user("req_pg", "p")
    tok = _login("req_pg", "p")
    pid = _project(tok)
    client = TestClient(app)
    for i, t in enumerate(["Alpha", "BetaGamma", "Gamma"]):
        client.post(
            f"/api/v1/projects/{pid}/requirements/",
            headers={"Authorization": f"Bearer {tok}"},
            json={"title": t, "body_md": f"body-{i}"},
        )
    r1 = client.get(
        f"/api/v1/projects/{pid}/requirements/?page=1&page_size=2",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 3
    assert len(d1["items"]) == 2
    assert d1["page"] == 1
    assert d1["page_size"] == 2

    r2 = client.get(
        f"/api/v1/projects/{pid}/requirements/?search=Gamma",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    titles = [x["title"] for x in d2["items"]]
    assert "BetaGamma" in titles
    assert "Gamma" in titles
    assert "Alpha" not in titles


def test_import_requirement_body_txt():
    _user("req_imp", "i")
    tok = _login("req_imp", "i")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/import-body",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("note.txt", b"hello import\nline2", "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["body_md"] == "hello import\nline2"


def test_import_requirement_body_rejects_docx():
    _user("req_imd", "d")
    tok = _login("req_imd", "d")
    pid = _project(tok)
    buf = BytesIO()
    doc = Document()
    doc.add_paragraph("para one")
    doc.save(buf)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/import-body",
        headers={"Authorization": f"Bearer {tok}"},
        files={
            "file": (
                "spec.docx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r.status_code == 400


def _png(width: int = 80, height: int = 40) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@patch("app.api.functional.requirements.enqueue_reindex")
def test_create_requirement_from_files_keeps_attachments_not_body(mock_rq):
    _user("req_src", "s")
    tok = _login("req_src", "s")
    pid = _project(tok)
    buf = BytesIO()
    doc = Document()
    doc.add_paragraph("para one")
    doc.save(buf)
    png = _png()
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/from-files",
        headers={"Authorization": f"Bearer {tok}"},
        files=[
            (
                "files",
                (
                    "spec.docx",
                    buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
            ("files", ("shot.png", png, "image/png")),
        ],
        data={"title": "Spec Doc"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Spec Doc"
    assert body["body_md"] == ""
    assert body["has_attachments"] is True
    assert len(body["attachments"]) == 2
    names = {a["filename"] for a in body["attachments"]}
    assert names == {"spec.docx", "shot.png"}
    rid = body["id"]
    mock_rq.assert_called_with(pid, "requirement", rid)

    att_id = next(a["id"] for a in body["attachments"] if a["filename"] == "spec.docx")
    src = client.get(
        f"/api/v1/projects/{pid}/requirements/{rid}/attachments/{att_id}/file",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert src.status_code == 200
    assert len(src.content) > 0
    assert "para one" in Document(BytesIO(src.content)).paragraphs[0].text

    # add another attachment
    buf2 = BytesIO()
    doc2 = Document()
    doc2.add_paragraph("para two")
    doc2.save(buf2)
    r2 = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/attachments",
        headers={"Authorization": f"Bearer {tok}"},
        files={
            "file": (
                "spec2.docx",
                buf2.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r2.status_code == 201
    assert r2.json()["filename"] == "spec2.docx"

    listed = client.get(
        f"/api/v1/projects/{pid}/requirements/{rid}/attachments",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 3

    # delete all attachments → placeholder body
    for att in listed.json():
        r3 = client.delete(
            f"/api/v1/projects/{pid}/requirements/{rid}/attachments/{att['id']}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r3.status_code == 200
    final = r3.json()
    assert final["has_attachments"] is False
    assert (final["body_md"] or "").strip()


def test_import_requirement_body_rejects_doc_extension():
    _user("req_imo", "o")
    tok = _login("req_imo", "o")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/import-body",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("old.doc", b"%PDF-fake", "application/msword")},
    )
    assert r.status_code == 400


def test_import_requirement_body_rejects_oversized():
    _user("req_imz", "z")
    tok = _login("req_imz", "z")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/import-body",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("big.txt", b"x" * (MAX_RAW_BYTES + 1), "text/plain")},
    )
    assert r.status_code == 400


def _docx_with_picture() -> bytes:
    buf = BytesIO()
    doc = Document()
    doc.add_paragraph("登录需要账号和密码")
    doc.add_picture(BytesIO(_png()))
    doc.save(buf)
    return buf.getvalue()


@patch("app.api.functional.requirements.enqueue_reindex")
@patch("app.services.functional.requirement_analysis.chat_complete")
def test_analyze_requirement_sends_images_and_persists(mock_llm, _mock_rq):
    def _fake(messages, **_kwargs):
        mock_llm.messages = messages
        return json.dumps(
            {
                "features": [
                    {
                        "module": "登录",
                        "items": [
                            {
                                "name": "账号密码登录",
                                "detail": "用户用账号密码登录系统",
                            }
                        ],
                    }
                ],
                "ui": {
                    "summary": "登录页含账号、密码输入框与提交按钮",
                    "pages": [
                        {
                            "name": "登录页",
                            "description": "用户输入凭证并提交登录",
                            "areas": ["账号输入框", "密码输入框", "登录按钮"],
                        }
                    ],
                },
                "unclear": [{"item": "失败锁定", "reason": "图中未写清次数"}],
            },
            ensure_ascii=False,
        )

    mock_llm.side_effect = _fake
    _user("req_an", "a")
    tok = _login("req_an", "a")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/from-file",
        headers={"Authorization": f"Bearer {tok}"},
        files={
            "file": (
                "spec.docx",
                _docx_with_picture(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "登录需求"},
    )
    assert created.status_code == 201
    rid = created.json()["id"]

    analyzed = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["analysis_json"]["features"][0]["module"] == "登录"
    assert body["analysis_json"]["features"][0]["items"][0]["name"] == "账号密码登录"
    assert body["analysis_json"]["ui"]["summary"].startswith("登录页")
    assert body["analysis_json"]["ui"]["pages"][0]["name"] == "登录页"
    assert body["analysis_json"]["ui"]["pages"][0]["areas"][0] == "账号输入框"
    assert body["analysis_json"]["unclear"][0]["item"] == "失败锁定"
    assert body["analysis_json"]["image_count"] == 1
    assert body["analyzed_at"]
    assert "logic_issues" not in body["analysis_json"]
    assert "points" not in body["analysis_json"]
    assert "boundaries" not in body["analysis_json"]
    user_content = mock_llm.messages[1]["content"]
    assert isinstance(user_content, list)
    assert any(part.get("type") == "image_url" for part in user_content)

    got = client.get(
        f"/api/v1/projects/{pid}/requirements/{rid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert got.status_code == 200
    assert got.json()["analysis_json"]["features"][0]["items"][0]["name"] == "账号密码登录"
    assert got.json()["analysis_json"]["ui"]["pages"][0]["areas"][2] == "登录按钮"


@patch("app.api.functional.requirements.enqueue_reindex")
@patch("app.services.functional.requirement_analysis.chat_complete")
def test_analyze_requirement_with_standalone_png(mock_llm, _mock_rq):
    def _fake(messages, **_kwargs):
        mock_llm.messages = messages
        return json.dumps(
            {
                "features": [
                    {
                        "module": "界面",
                        "items": [{"name": "界面展示", "detail": "按截图展示控件"}],
                    }
                ],
                "ui": {
                    "summary": "截图中的主界面",
                    "pages": [
                        {
                            "name": "主界面",
                            "description": "按截图验证元素",
                            "areas": ["主内容区"],
                        }
                    ],
                },
                "unclear": [],
            },
            ensure_ascii=False,
        )

    mock_llm.side_effect = _fake
    _user("req_png", "p")
    tok = _login("req_png", "p")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/from-files",
        headers={"Authorization": f"Bearer {tok}"},
        files=[("files", ("ui.png", _png(), "image/png"))],
        data={"title": "截图需求"},
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    assert created.json()["body_md"] == ""
    assert created.json()["has_attachments"] is True

    analyzed = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert analyzed.status_code == 200
    assert analyzed.json()["analysis_json"]["image_count"] == 1
    user_content = mock_llm.messages[1]["content"]
    assert isinstance(user_content, list)
    assert any(part.get("type") == "image_url" for part in user_content)


@patch("app.api.functional.requirements.enqueue_reindex")
@patch("app.services.functional.requirement_analysis.chat_complete")
def test_analyze_requirement_empty_does_not_call_model(mock_llm, _mock_rq):
    _user("req_empty", "e")
    tok = _login("req_empty", "e")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "空需求", "body_md": "临时正文"},
    )
    rid = created.json()["id"]
    db = TestingSessionLocal()
    try:
        row = db.get(Requirement, rid)
        assert row is not None
        row.body_md = ""
        db.commit()
    finally:
        db.close()

    analyzed = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert analyzed.status_code == 400
    mock_llm.assert_not_called()


def test_viewer_cannot_import_requirement_body():
    _user("req_imo2", "a")
    _user("req_imv", "v")
    atok = _login("req_imo2", "a")
    pid = _project(atok)
    client = TestClient(app)
    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {atok}"},
        json={"username": "req_imv", "role": "viewer"},
    )
    vtok = _login("req_imv", "v")
    r = client.post(
        f"/api/v1/projects/{pid}/requirements/import-body",
        headers={"Authorization": f"Bearer {vtok}"},
        files={"file": ("n.txt", b"no", "text/plain")},
    )
    assert r.status_code == 403
