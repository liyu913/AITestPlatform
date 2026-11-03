"""Unit tests for UI agent post-codegen script review."""

import json

from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.ui_automation.ui_script_review import review_and_fix_case_scripts

_LLM = LlmRuntime(
    api_base="https://example.com/v1",
    api_key="k",
    model="m",
    allowed_models_csv="",
)

_BAD_LOGIN = """async function run(page, agent) {
  await page.goto("/login");
  await page.getByRole('textbox', { name: "账号" }).fill(env.username);
  await page.getByRole('textbox', { name: "密码" }).fill(env.password);
  {
    const captcha = String(await agent.aiQuery("只读取验证码图片中的字符"));
    await page.getByRole('textbox', { name: '验证码' }).fill(captcha);
  }
  await page.getByRole("button", { name: "新建项目" }).click();
  await expect(page.getByText("离开登录页").first()).toBeVisible();
}
"""

_FIXED_LOGIN = """async function run(page, agent) {
  await page.goto("/login");
  await page.getByRole('textbox', { name: "账号" }).fill(env.username);
  await page.getByRole('textbox', { name: "密码" }).fill(env.password);
  {
    const captcha = String(await agent.aiQuery("只读取验证码图片中的字符"));
    await page.getByRole('textbox', { name: '验证码' }).fill(captcha);
  }
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByText("离开登录页").first()).toBeVisible();
}
"""

_INVENTED = """async function run(page, agent) {
  await page.goto("/login");
  await page.getByTestId("totally-fake").click();
}
"""

_MATRIX_WITH_LOGIN = [
    {
        "id": "TC-01",
        "name": "登录成功",
        "expected": "离开登录页",
        "steps": [
            {
                "action": "fill",
                "cli_locator": 'page.getByRole("textbox", { name: "账号" })',
            },
            {
                "action": "fill",
                "cli_locator": 'page.getByRole("textbox", { name: "密码" })',
            },
            {
                "action": "fill",
                "cli_locator": 'page.getByRole("textbox", { name: "验证码" })',
            },
            {
                "action": "click",
                "cli_locator": 'page.getByRole("button", { name: "新建项目" })',
            },
            {
                "action": "click",
                "cli_locator": 'page.getByRole("button", { name: "登录" })',
            },
        ],
    }
]

_MATRIX_ONLY_NEW_PROJECT = [
    {
        "id": "TC-01",
        "name": "登录成功",
        "expected": "离开登录页",
        "steps": [
            {
                "action": "click",
                "cli_locator": 'page.getByRole("button", { name: "新建项目" })',
            },
        ],
    }
]


def test_review_fixes_login_wrong_click_when_login_in_allowlist(monkeypatch):
    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        user = messages[1]["content"]
        assert "explored_locators" in user
        assert "登录" in user
        return json.dumps(
            {
                "summary": "登录用例误点新建项目，已改为登录按钮",
                "issues": [
                    {
                        "case_id": "TC-01",
                        "severity": "error",
                        "message": "登录成功用例不应点击新建项目",
                    }
                ],
                "cases": [{"id": "TC-01", "code": _FIXED_LOGIN}],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "app.services.ui_automation.ui_script_review.llm.chat_complete",
        fake_chat,
    )
    scripts = [
        {
            "id": "TC-01",
            "name": "登录成功",
            "ready": True,
            "code": _BAD_LOGIN,
        }
    ]
    updated, meta = review_and_fix_case_scripts(
        scripts,
        goal="登录后项目管理",
        checklist=["登录"],
        matrix=_MATRIX_WITH_LOGIN,
        llm_cfg=_LLM,
    )
    assert meta["fixed_count"] == 1
    assert meta["skipped"] is False
    assert meta.get("rejected_invented", 0) == 0
    assert "新建项目" not in updated[0]["code"]
    assert "登录" in updated[0]["code"]
    assert any(i.get("case_id") == "TC-01" for i in meta["issues"])
    assert captured.get("json_mode") is True
    assert captured.get("scene") == "UI 脚本自审"
    assert "explored_locators" in captured["messages"][0]["content"]


def test_review_rejects_invented_locator(monkeypatch):
    def fake_chat(messages, **kwargs):
        return json.dumps(
            {
                "summary": "编造了未探索的按钮",
                "issues": [],
                "cases": [{"id": "TC-01", "code": _FIXED_LOGIN}],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "app.services.ui_automation.ui_script_review.llm.chat_complete",
        fake_chat,
    )
    scripts = [{"id": "TC-01", "name": "登录成功", "ready": True, "code": _BAD_LOGIN}]
    updated, meta = review_and_fix_case_scripts(
        scripts,
        goal="登录",
        matrix=_MATRIX_ONLY_NEW_PROJECT,
        llm_cfg=_LLM,
    )
    assert meta["fixed_count"] == 0
    assert meta["rejected_invented"] == 1
    assert "新建项目" in updated[0]["code"]
    # Keep original wrong click — do not accept invented 登录 button
    assert 'name: "登录"' not in updated[0]["code"].replace("'", '"')
    assert any("白名单" in str(i.get("message") or "") for i in meta["issues"])


def test_review_rejects_fake_testid(monkeypatch):
    def fake_chat(*_a, **_k):
        return json.dumps(
            {
                "summary": "fake",
                "issues": [],
                "cases": [{"id": "TC-01", "code": _INVENTED}],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "app.services.ui_automation.ui_script_review.llm.chat_complete",
        fake_chat,
    )
    scripts = [{"id": "TC-01", "name": "登录", "ready": True, "code": _BAD_LOGIN}]
    updated, meta = review_and_fix_case_scripts(
        scripts,
        goal="登录",
        matrix=_MATRIX_ONLY_NEW_PROJECT,
        llm_cfg=_LLM,
    )
    assert meta["fixed_count"] == 0
    assert "totally-fake" not in updated[0]["code"]
    assert "新建项目" in updated[0]["code"]


def test_review_skips_when_llm_fails(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "app.services.ui_automation.ui_script_review.llm.chat_complete",
        boom,
    )
    scripts = [{"id": "TC-01", "name": "登录", "ready": True, "code": _BAD_LOGIN}]
    updated, meta = review_and_fix_case_scripts(scripts, goal="登录", llm_cfg=_LLM)
    assert meta["skipped"] is True
    assert updated[0]["code"] == _BAD_LOGIN
    assert "新建项目" in updated[0]["code"]


def test_review_noop_when_no_scripts():
    updated, meta = review_and_fix_case_scripts([], goal="x", llm_cfg=_LLM)
    assert updated == []
    assert meta["skipped"] is True
