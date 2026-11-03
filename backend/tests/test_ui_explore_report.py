from app.services.ui_automation.ui_explore_report import (
    annotate_steps,
    bind_trace_to_matrix,
    build_explore_report,
    derive_matrix_from_explore,
    ensure_plan_matrix,
    hydrate_matrix_from_trace,
    infer_driver,
    matrix_bind_issues,
    normalize_matrix,
    skeletonize_plan_matrix,
)
from app.services.ui_automation.ui_node_script import (
    matrix_to_case_scripts,
    matrix_to_node_script,
    script_has_assert,
)


def test_normalize_matrix_from_llm_rows():
    rows = normalize_matrix(
        [
            {
                "id": "AL-001",
                "name": "列表加载",
                "preconditions": "已登录",
                "steps": ["打开列表", "检查表头"],
                "expected": "表格可见",
            },
            {"name": "搜索", "操作": "输入名称点查询", "预期": "结果含关键字"},
        ],
        goal="公寓列表",
        start_path="/apartment/list",
    )
    assert rows[0]["id"] == "AL-001"
    assert isinstance(rows[0]["steps"][0], dict)
    assert rows[0]["steps"][0]["action"] == "goto"
    assert any(s.get("action", "").startswith("assert") for s in rows[0]["steps"])
    assert rows[1]["id"] == "UC-002"
    assert any("查询" in str(s.get("target") or "") for s in rows[1]["steps"])


def test_normalize_matrix_structured_steps():
    rows = normalize_matrix(
        [
            {
                "id": "L-001",
                "name": "登录成功",
                "steps": [
                    {"action": "goto", "target": "/login", "value": "/login"},
                    {"action": "fill", "target": "账号", "value": "{{username}}"},
                    {"action": "fill", "target": "密码", "value": "{{password}}"},
                    {
                        "action": "fill",
                        "target": "验证码图片",
                        "value": "{{captcha}}",
                        "driver": "midscene",
                    },
                    {"action": "click", "target": "登录"},
                    {"action": "assert_url", "value": "/"},
                ],
            }
        ],
        start_path="/login",
    )
    assert rows[0]["steps"][0]["action"] == "goto"
    assert rows[0]["steps"][-1]["action"] == "assert_url"
    assert any(s.get("driver") == "midscene" for s in rows[0]["steps"])


def test_ensure_plan_matrix_synthesizes_when_missing():
    plan = ensure_plan_matrix(
        {
            "goal": "登录后打开首页",
            "start_path": "/login",
            "checklist": ["填账号", "点登录", "进入首页"],
            "case_name": "登录冒烟",
        }
    )
    assert len(plan["matrix"]) == 1
    assert plan["matrix"][0]["name"] == "登录冒烟"
    assert isinstance(plan["matrix"][0]["steps"][0], dict)
    assert any(s.get("action") == "goto" for s in plan["matrix"][0]["steps"])
    assert any(s.get("action", "").startswith("assert") for s in plan["matrix"][0]["steps"])


def test_ensure_plan_matrix_allow_empty_for_explore_first():
    plan = ensure_plan_matrix(
        {"goal": "登录与项目管理", "start_path": "/login", "checklist": ["登录", "项目"], "matrix": []},
        synthesize_if_empty=False,
    )
    assert plan["matrix"] == []


def test_infer_driver_playwright_vs_midscene():
    assert infer_driver({"action": "goto", "value": "/"})[0] == "playwright"
    assert infer_driver({"action": "click", "role": "button", "name": "登录"})[0] == "playwright"
    assert infer_driver({"action": "fill", "placeholder": "用户名", "value": "a"})[0] == "playwright"
    driver, reason, prompt = infer_driver(
        {"kind": "fill_captcha", "locate": "验证码输入框", "captcha": True}
    )
    assert driver == "midscene"
    assert reason and "captcha" in reason
    assert prompt
    driver2, reason2, _ = infer_driver(
        {"action": "click", "locate": "图表放大按钮", "selector": ""}
    )
    assert driver2 == "midscene"
    assert reason2


def test_build_explore_report_summary():
    plan = ensure_plan_matrix(
        {
            "goal": "登录",
            "start_path": "/",
            "case_name": "登录",
            "checklist": ["登录"],
            "matrix": [{"id": "L-001", "name": "登录成功", "expected": "进入首页"}],
        }
    )
    steps = annotate_steps(
        [
            {"action": "goto", "value": "/login", "status": "ok"},
            {"action": "fill", "placeholder": "账号", "value": "demo", "status": "ok"},
            {"action": "fill", "locate": "验证码输入框", "captcha": True, "kind": "fill_captcha"},
            {"action": "click", "role": "button", "name": "登录", "status": "ok"},
        ],
        matrix=plan["matrix"],
    )
    report = build_explore_report(plan=plan, steps=steps, script="async function run(){}", engine="midscene")
    assert report["case_name"] == "登录"
    assert report["mapping"][0]["id"] == "L-001"
    assert report["summary"]["total_steps"] == 4
    assert report["summary"]["midscene_steps"] >= 1
    assert report["summary"]["playwright_steps"] >= 2
    assert report["captcha"] is not None
    assert any(s.get("driver") == "midscene" for s in report["steps"])


def test_bind_trace_and_matrix_to_script_has_expect():
    matrix = normalize_matrix(
        [
            {
                "id": "L-001",
                "name": "登录成功",
                "steps": [
                    {"action": "goto", "target": "/login"},
                    {"action": "fill", "target": "账号", "value": "{{username}}"},
                    {"action": "fill", "target": "密码", "value": "{{password}}"},
                    {"action": "fill", "target": "验证码", "value": "{{captcha}}"},
                    {"action": "click", "target": "登录"},
                    {"action": "assert_url", "value": "/home"},
                ],
            }
        ],
        start_path="/login",
    )
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "cli_locator": 'page.getByRole("textbox", { name: "账号" })',
            "value": "admin",
        },
        {
            "kind": "fill",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "密码",
            "value": "secret",
        },
        {
            "kind": "fill_captcha",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "captcha": True,
            "placeholder": "请输入图中字符",
        },
        {
            "kind": "click",
            "action": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "driver": "midscene",
            "prompt": "读取登录页验证码图片中的字符",
            "explore_noop": True,
            "status": "ok",
        },
        {
            "kind": "click",
            "driver": "midscene",
            "prompt": "读取登录页验证码图片中的字符",
            "explore_noop": True,
            "status": "ok",
        },
    ]
    bound = bind_trace_to_matrix(matrix, trace)
    assert bound[0]["ready"] is True
    assert matrix_bind_issues(bound) == []
    code = matrix_to_node_script(bound, username="admin", password="secret", start_path="/login")
    assert code.startswith("async function run(page, agent)")
    assert "expect(page)" in code
    assert "toHaveURL" in code
    assert script_has_assert(code)


def test_collapse_noisy_captcha_retry_trace_for_codegen():
    """Explore retries must not become repeated login/refresh clicks in the script."""
    from app.services.ui_automation.ui_explore_report import _collapse_trace_for_bind, hydrate_matrix_from_trace

    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "name": "账号",
            "cli_locator": 'page.getByRole("textbox", { name: "账号" })',
            "value": "admin123",  # wrong value recorded; field name wins in codegen
        },
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "name": "密码",
            "value": "admin123",
        },
        {
            "kind": "fill_captcha",
            "status": "ok",
            "captcha": True,
            "placeholder": "请输入图中字符",
            "value": "{{captcha}}",
        },
        {"kind": "click", "status": "ok", "role": "button", "name": "登录"},
        {"kind": "click", "status": "ok", "name": "换一张验证码", "reason": "captcha_retry_refresh"},
        {
            "kind": "fill_captcha",
            "status": "ok",
            "captcha": True,
            "placeholder": "请输入图中字符",
            "value": "{{captcha}}",
        },
        {"kind": "click", "status": "ok", "role": "button", "name": "登录"},
        {"kind": "click", "status": "ok", "role": "button", "name": "登录"},
        {"kind": "click", "status": "ok", "role": "button", "name": "登录"},
        {"kind": "click", "status": "ok", "role": "button", "name": "登录"},
    ]
    collapsed = _collapse_trace_for_bind(trace)
    kinds = [t.get("kind") or t.get("action") for t in collapsed]
    assert kinds.count("goto") == 1
    assert sum(1 for t in collapsed if (t.get("kind") == "fill" and t.get("name") == "账号")) == 1
    assert sum(1 for t in collapsed if t.get("kind") == "fill_captcha" or t.get("captcha")) == 1
    assert sum(1 for t in collapsed if t.get("name") == "登录") == 1
    assert not any(t.get("reason") == "captcha_retry_refresh" for t in collapsed)
    assert not any("换一张" in str(t.get("name") or "") for t in collapsed)

    matrix = [
        {"id": "TC-01", "name": "使用正确账号密码登录成功", "expected": "离开登录页", "start_url": "/login"}
    ]
    hydrated = hydrate_matrix_from_trace(matrix, trace)
    code = matrix_to_case_scripts(hydrated, username="admin", password="admin123", start_path="/login")[0]["code"]
    assert code.count('name: "登录"') == 1 or code.count("name: \"登录\"") == 1
    assert "换一张" not in code
    assert code.count(".click()") <= 2  # login only (+ maybe none)
    assert 'name: "账号"' in code or "name: \"账号\"" in code
    assert "env.username" in code
    assert "env.password" in code
    # Must not fill 账号 with env.password
    assert 'name: "账号" }).fill(env.password)' not in code
    assert 'name: "账号" }).fill(env.username)' in code or "env.username" in code


def test_skeleton_then_hydrate_generates_case_scripts():
    plan = skeletonize_plan_matrix(
        ensure_plan_matrix(
            {
                "goal": "登录与建项目",
                "start_path": "/login",
                "matrix": [
                    {"id": "TC-01", "name": "登录成功", "expected": "进入首页"},
                    {"id": "TC-02", "name": "创建项目", "expected": "项目列表可见"},
                ],
            }
        )
    )
    assert all(r.get("ready") is False for r in plan["matrix"])
    assert all(
        all(isinstance(s, dict) and s.get("action") in ("goto", "assert_text", "assert_url") for s in r["steps"])
        for r in plan["matrix"]
    )
    # One explore session tags steps with case_id → both rows hydrate
    trace = [
        {
            "kind": "goto",
            "path": "/login",
            "status": "ok",
            "driver": "playwright",
            "case_id": "TC-01",
        },
        {
            "kind": "fill",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
            "case_id": "TC-01",
        },
        {
            "kind": "click",
            "action": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
            "case_id": "TC-01",
        },
        {
            "kind": "click",
            "action": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "新建项目",
            "case_id": "TC-02",
        },
    ]
    hydrated = hydrate_matrix_from_trace(plan["matrix"], trace)
    assert hydrated[0]["ready"] is True
    assert hydrated[1]["ready"] is True
    cases = matrix_to_case_scripts(hydrated, username="admin", password="secret", start_path="/login")
    assert cases[0]["ready"] and "async function run" in (cases[0]["code"] or "")
    assert script_has_assert(cases[0]["code"] or "")
    assert cases[1]["ready"] and "async function run" in (cases[1]["code"] or "")
    assert script_has_assert(cases[1]["code"] or "")
    # Business case reuses storageState — no embedded login
    assert "env.username" not in (cases[1]["code"] or "")
    assert 'name: "登录"' not in (cases[1]["code"] or "")
    assert "新建项目" in (cases[1]["code"] or "")
    assert cases[1].get("needs_auth") is True
    assert "// auth:" in (cases[1]["code"] or "")


def test_hydrate_without_case_id_splits_at_login():
    matrix = [
        {"id": "TC-01", "name": "登录成功", "expected": "进入首页", "start_url": "/login"},
        {"id": "TC-02", "name": "进入项目管理", "expected": "项目列表", "start_url": "/"},
    ]
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
    ]
    hydrated = hydrate_matrix_from_trace(matrix, trace)
    assert hydrated[0]["ready"] is True
    assert hydrated[1]["ready"] is True
    cases = matrix_to_case_scripts(hydrated, username="admin", start_path="/login")
    assert cases[0]["ready"] and cases[1]["ready"]
    assert "项目管理" in (cases[1]["code"] or "")
    assert "env.username" not in (cases[1]["code"] or "")
    assert cases[1].get("needs_auth") is True


def test_derive_matrix_from_explore_fallback_splits_checklist():
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "新建项目",
        },
    ]
    rows = derive_matrix_from_explore(
        prompt="登录后做项目管理",
        goal="登录与项目管理",
        trace=trace,
        llm=None,
        start_path="/login",
        checklist=["登录", "项目列表", "新建项目"],
        case_name="项目管理",
    )
    assert len(rows) >= 2
    assert all(r.get("ready") for r in rows)
    cases = matrix_to_case_scripts(rows, username="admin", password="x", start_path="/login")
    assert sum(1 for c in cases if c.get("ready") and (c.get("code") or "").strip()) >= 2
    non_login = next(c for c in cases if "登录" not in (c.get("name") or ""))
    assert "env.username" not in (non_login["code"] or "")
    assert non_login.get("needs_auth") is True
    assert "// auth:" in (non_login["code"] or "")


def test_derive_matrix_from_explore_llm_step_indexes():
    class FakeLlm:
        def chat_complete(self, *args, **kwargs):
            return (
                '{"matrix":['
                '{"id":"TC-01","name":"登录成功","expected":"首页","step_indexes":[0,1,2]},'
                '{"id":"TC-02","name":"进入项目","expected":"列表","step_indexes":[3]}'
                "]}"
            )

    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
    ]
    rows = derive_matrix_from_explore(
        prompt="登录后进项目",
        goal="登录与项目",
        trace=trace,
        llm=FakeLlm(),
        start_path="/login",
        checklist=["登录", "项目"],
    )
    assert len(rows) == 2
    assert rows[0]["id"] == "TC-01" and rows[1]["id"] == "TC-02"
    assert rows[0]["ready"] and rows[1]["ready"]
    cases = matrix_to_case_scripts(rows, username="admin", start_path="/login")
    assert cases[1]["ready"]
    assert "项目管理" in (cases[1]["code"] or "")
    assert "env.username" not in (cases[1]["code"] or "")
    assert cases[1].get("needs_auth") is True


def test_derive_matrix_empty_checklist_still_splits():
    """Empty checklist must not collapse a login+business explore into UC-001."""
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "密码",
            "value": "x",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "新建项目",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "新建需求",
        },
    ]
    rows = derive_matrix_from_explore(
        prompt="登录后项目管理与需求",
        goal="登录后项目管理与需求管理端到端探索",
        trace=trace,
        llm=None,
        start_path="/login",
        checklist=[],
        case_name="端到端探索",
    )
    assert len(rows) >= 2
    assert all(r.get("id") != "UC-001" or len(rows) > 1 for r in rows)
    assert rows[0]["id"] == "TC-01"
    assert not any(r.get("id") == "UC-001" for r in rows)


def test_derive_matrix_no_login_button_uses_captcha_boundary():
    """Auth fills + captcha without a named 登录 click still split login vs business."""
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "密码",
            "value": "secret",
        },
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "验证码",
            "value": "AB12",
            "captcha": True,
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "新建需求",
        },
    ]
    rows = derive_matrix_from_explore(
        prompt="登录后做需求",
        goal="登录与需求",
        trace=trace,
        llm=None,
        start_path="/login",
        checklist=[],
    )
    assert len(rows) >= 2
    assert rows[0]["id"] == "TC-01"
    assert rows[0]["name"] == "登录成功"
    assert not any(r.get("id") == "UC-001" for r in rows)
    cases = matrix_to_case_scripts(rows, username="admin", password="x", start_path="/login")
    ready = [c for c in cases if c.get("ready") and (c.get("code") or "").strip()]
    assert len(ready) >= 2


def test_derive_matrix_llm_mega_single_triggers_fallback():
    """LLM returning one case that covers the whole trace should re-split via fallback."""

    class MegaLlm:
        def chat_complete(self, *args, **kwargs):
            return (
                '{"matrix":['
                '{"id":"UC-001","name":"端到端探索","expected":"完成",'
                '"step_indexes":[0,1,2,3,4]}'
                "]}"
            )

    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "status": "ok",
            "driver": "playwright",
            "placeholder": "账号",
            "value": "admin",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "role": "button",
            "name": "登录",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "项目管理",
        },
        {
            "kind": "click",
            "status": "ok",
            "driver": "playwright",
            "name": "新建项目",
        },
    ]
    rows = derive_matrix_from_explore(
        prompt="登录后项目管理",
        goal="登录与项目",
        trace=trace,
        llm=MegaLlm(),
        start_path="/login",
        checklist=["登录", "项目列表", "新建项目"],
    )
    assert len(rows) >= 2
    assert not any(r.get("id") == "UC-001" for r in rows)
