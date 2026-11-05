import json
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.ai import ai_generate
from app.config import settings
from app.main import app
from app.models.functional.testcase import TestCase
from app.services.ai.fast_case_skill import bundled_skill
from tests.conftest import TestingSessionLocal
from tests.test_ai_generate import _login, _project, _user


@pytest.fixture
def project(monkeypatch):
    username = "skill_" + uuid4().hex[:8]
    _user(username, "testpass")
    token = _login(username, "testpass")
    pid = _project(token)
    monkeypatch.setattr(settings, "llm_api_key", "test-only")
    monkeypatch.setattr(settings, "fast_case_skill_projects", str(pid))
    monkeypatch.setattr(ai_generate, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(ai_generate, "enqueue_reindex", Mock())
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    path = f"/api/v1/projects/{pid}"
    requirement = client.post(path + "/requirements/", json={"title": "文档提交",
        "body_md": "正文" * 9550 + "单个文档可上传至 5 MB。"})
    assert requirement.status_code == 201
    rid = requirement.json()["id"]
    case = {"title": "上传上限文件", "overall_expected": "显示已上传的文档",
            "steps": [{"order": 0, "action": "选择 5 MB 文件", "expected": "文件列表显示选中的文件"},
                      {"order": 1, "action": "提交文件", "expected": "列表中有一条已上传记录"}],
            "design_trace": {"basis": "confirmed", "source_ref": f"REQ-{rid}", "locator": "文档提交规则",
                 "excerpt": "单个文档可上传至 5 MB。", "method": "边界值分析", "coverage_target": "文件大小上限",
                 "rationale": "5 MB 恰好等于允许上限", "readiness": "ready"}}
    return client, path, rid, case


def test_skill_drafts_refresh_partial_confirm_and_retry(project, monkeypatch):
    client, path, rid, case = project
    second = deepcopy(case)
    second["title"] = "小于上传上限"
    generate = Mock(return_value=json.dumps({"cases": [case, second]}, ensure_ascii=False))
    monkeypatch.setattr(ai_generate.llm, "chat_complete", generate)
    result = client.post(path + "/ai/generate-testcases", json={"requirement_id": rid})
    assert result.status_code == 200, result.text
    assert "等价类划分" in generate.call_args.args[0][0]["content"]
    drafts = result.json()["cases"]
    assert drafts[0]["draft_id"] != drafts[1]["draft_id"]
    assert drafts[0]["design_trace"]["skill_version"] == bundled_skill()[0]
    assert len(drafts[0]["design_trace"]["source_digest"]) == 64
    # Which model produced the draft is recorded next to the rule version.
    assert drafts[0]["design_trace"]["llm_model"] == settings.llm_model
    target = drafts[1]
    target["steps"][1]["expected"] = "刷新列表后仍有一条对应记录"
    sid = client.post(path + "/ai/sessions").json()["id"]
    session_url = path + f"/ai/sessions/{sid}"
    assert client.put(session_url, json={"drafts": drafts[::-1], "requirement_id": rid}).is_success
    restored = client.get(session_url).json()["drafts"]
    assert restored == drafts[::-1]
    payload = {"cases": [restored[0]], "requirement_id": rid}
    url = path + "/ai/confirm-testcases"
    result = client.post(url, json=payload)
    assert result.status_code == 201, result.text
    saved = result.json()[0]
    retry = client.post(url, json=payload)
    assert retry.status_code == 201 and retry.json()[0]["id"] == saved["id"]
    detail = client.get(path + f"/test-cases/{saved['id']}").json()
    assert detail["ai_draft_id"] == target["draft_id"]
    assert detail["design_trace"] == target["design_trace"]
    assert [s["expected"] for s in detail["steps"]] == [s["expected"] for s in target["steps"]]
    # Confirmed AI drafts start a traceable version history.
    versions = client.get(path + f"/test-cases/{saved['id']}/versions").json()
    assert [v["version_no"] for v in versions] == [1]
    assert versions[0]["change_source"] == "ai_confirmed"
    assert versions[0]["snapshot"]["title"] == target["title"]
    with TestingSessionLocal() as db:
        assert db.query(TestCase).filter(TestCase.project_id == saved["project_id"]).count() == 1
    changed = deepcopy(payload)
    changed["cases"][0]["steps"][1]["expected"] = "另一条断言"
    assert client.post(url, json=changed).status_code == 409
    broken = deepcopy(payload)
    broken["cases"][0]["steps"][0]["expected"] = "—"
    assert client.post(url, json=broken).status_code == 422


def test_stream_review_repairs_lost_trace_and_keeps_identity(project, monkeypatch):
    client, path, rid, case = project
    monkeypatch.setattr(ai_generate.llm, "chat_complete_stream", lambda *a, **kw: iter([json.dumps({"cases": [case]})]))
    seen = {}
    def review(messages, **kwargs):
        if not seen:
            text = messages[1]["content"].split("待检查用例：\n", 1)[1]
            draft = json.JSONDecoder().raw_decode(text)[0][0]
            seen.update(draft)
            missing = dict(draft, design_trace=None)
            return json.dumps({"summary": "检查", "cases": [missing]})
        return json.dumps({"summary": "修订完成", "cases": [dict(seen, title="修订后的标题")]})
    complete = Mock(side_effect=review)
    monkeypatch.setattr(ai_generate.llm, "chat_complete", complete)
    result = client.post(path + "/ai/generate-testcases/stream", json={"requirement_id": rid, "enable_case_review": True})
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "done", events[-1]
    draft = events[-1]["cases"][0]
    assert complete.call_count == 2 and draft["draft_id"] == seen["draft_id"]
    assert draft["design_trace"] == seen["design_trace"] and draft["title"] == "修订后的标题"


def test_unverifiable_citation_fails_before_confirmation(project, monkeypatch):
    client, path, rid, case = project
    case["design_trace"]["excerpt"] = "材料并未规定的上限"
    generate = Mock(return_value=json.dumps({"cases": [case]}))
    monkeypatch.setattr(ai_generate.llm, "chat_complete", generate)
    result = client.post(path + "/ai/generate-testcases", json={"requirement_id": rid})
    assert result.status_code == 422 and "摘录" in result.json()["message"]
    assert generate.call_count == 2


def test_agent_case_phase_applies_the_same_quality_gate(project, monkeypatch):
    """Step-by-step generation must obey the same rules as the fast path."""
    from app.errors import AppError
    from app.schemas.ai.ai_agent import RequirementAnalysisOut
    from app.schemas.ai.ai_gen import PromptMessage
    from app.services.ai import ai_gen_pipeline
    from app.services.ai.fast_case_skill import FastCaseSkill, Source

    _, _, rid, case = project
    skill = FastCaseSkill(
        sources={f"REQ-{rid}": Source.from_text("文档提交", "正文" * 10 + "单个文档可上传至 5 MB。")},
        requirement_id=rid,
        library_id=None,
    )
    artifacts = ai_gen_pipeline.PipelineArtifacts()
    analysis = RequirementAnalysisOut(is_clear=True, summary="上传文档")

    monkeypatch.setattr(
        ai_gen_pipeline.llm,
        "chat_complete_stream",
        lambda *a, **kw: iter([json.dumps({"cases": [case]}, ensure_ascii=False)]),
    )
    events = list(
        ai_gen_pipeline._iter_cases_and_review(
            context=f"REQ-{rid} 文档提交",
            analysis=analysis,
            test_points=[],
            artifacts=artifacts,
            use_llm_model="qwen-test",
            llm_cfg=Mock(),
            skill=skill,
        )
    )
    done = events[-1]
    assert done["type"] == "done"
    trace = done["cases"][0]["design_trace"]
    assert trace["skill_version"] == bundled_skill()[0]
    assert trace["llm_model"] == "qwen-test"
    assert trace["requirement_id"] == rid

    # An unverifiable draft is rejected by the agent path too, not silently saved.
    bad = deepcopy(case)
    bad["design_trace"]["excerpt"] = "材料并未规定的内容"
    monkeypatch.setattr(
        ai_gen_pipeline.llm,
        "chat_complete_stream",
        lambda *a, **kw: iter([json.dumps({"cases": [bad]}, ensure_ascii=False)]),
    )
    monkeypatch.setattr(
        ai_gen_pipeline.llm,
        "chat_complete",
        Mock(return_value=json.dumps({"cases": [bad]}, ensure_ascii=False)),
    )
    with pytest.raises(AppError) as err:
        list(
            ai_gen_pipeline._iter_cases_and_review(
                context=f"REQ-{rid} 文档提交",
                analysis=analysis,
                test_points=[],
                artifacts=artifacts,
                use_llm_model="qwen-test",
                llm_cfg=Mock(),
                skill=skill,
            )
        )
    assert err.value.status_code == 422
