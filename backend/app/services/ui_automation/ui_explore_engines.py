"""Explore engines: playwright-cli (default) and optional Midscene aiAct legacy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.ai.effective_ai_settings import LlmRuntime, MidsceneRuntime
from app.services.ui_automation.node_ui_runner import explore_with_midscene
from app.services.ui_automation.ui_cli_explorer import (
    REPEATED_MIDSCENE_NOOP,
    compile_cli_trace_script,
    run_cli_explore_loop,
)
from app.services.ui_automation.ui_explore_report import (
    _skeleton_steps_for_row,
    bind_trace_to_matrix,
    derive_matrix_from_explore,
    ensure_plan_matrix,
    hydrate_matrix_from_trace,
)
from app.services.ui_automation.ui_node_script import (
    _assert_statements,
    actions_to_node_script,
    matrix_to_case_scripts,
    matrix_to_node_script,
    script_has_assert,
)

ENGINES = ("playwright", "midscene")


def merge_explore_ctx(
    ctx: dict[str, str] | None = None,
    *,
    username: str = "",
    password: str = "",
) -> dict[str, str]:
    """Non-empty explore/codegen variables. username/password kwargs remain for callers."""
    out: dict[str, str] = {}
    for k, v in (ctx or {}).items():
        key = str(k).strip()
        val = str(v or "").strip()
        if key and val:
            out[key] = val
    user = str(username or "").strip()
    pwd = str(password or "").strip()
    if user:
        out.setdefault("username", user)
    if pwd:
        out.setdefault("password", pwd)
    return out


def explore_task_text(
    *,
    goal: str,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
) -> str:
    env = merge_explore_ctx(ctx, username=username, password=password)
    var_bits = "".join(
        f"若页面有与键「{k}」语义匹配的输入框，fill 的 value 用 {{{{{k}}}}}。" for k in env
    )
    return (
        f"目标：{goal or '完成用户要求的界面操作'}。"
        f"{var_bits}"
        "按当前页面可见控件操作：优先用 accessibility snapshot 中的 ref（Playwright）。"
        "无稳定 locator、canvas 或需视觉理解时可用 Midscene。"
        "若有图片验证码：可用 Midscene 读图，再用 Playwright 填入对应验证码输入框，"
        "然后立刻点击页面上的登录/提交按钮；禁止对验证码图片或刷新按钮做 Input；不要反复空转同一框。"
        "若提交失败或验证码图已刷新，可点击刷新类控件后重新读图填写。"
        "不要在同一页面无限重复同一操作。"
        "只有离开当前表单页面，或出现明确的成功、失败提示时才结束。"
    )


def _trace_from_actions(
    actions: list[dict[str, Any]],
    *,
    ctx: dict[str, str] | None = None,
    password: str = "",
    username: str = "",
) -> list[dict[str, Any]]:
    from app.services.ui_automation.ui_explore_report import annotate_steps

    env = merge_explore_ctx(ctx, username=username, password=password)
    # Longer values first so we don't partially mis-map
    value_to_key = sorted(
        ((str(v), k) for k, v in env.items()),
        key=lambda kv: -len(kv[0]),
    )
    trace: list[dict[str, Any]] = []
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "")
        value = raw.get("value")
        if value is not None:
            text = str(value)
            for val, key in value_to_key:
                if val and text == val:
                    value = "{{" + key + "}}"
                    break
        item: dict[str, Any] = {
            "action": "fill" if kind in ("fill", "fill_captcha") else kind,
            "kind": kind,
            "status": "ok",
            "selector": raw.get("selector") or "",
            "locate": raw.get("locate") or "",
            "placeholder": raw.get("placeholder") or "",
            "role": raw.get("role") or "",
            "name": raw.get("name") or "",
        }
        # Prefer stable locator text for display when selector empty
        if not item["selector"]:
            item["selector"] = raw.get("cli_locator") or raw.get("placeholder") or raw.get("locate") or ""
        if value is not None and kind in ("fill", "fill_captcha", "goto", "press"):
            item["value"] = value
        if kind == "fill_captcha":
            item["captcha"] = True
        if kind == "goto":
            item["value"] = raw.get("path") or value or "/"
        for key in ("driver", "reason", "prompt", "cli_locator", "captcha"):
            if raw.get(key) is not None and key not in item:
                item[key] = raw.get(key)
        trace.append(item)
    return annotate_steps(trace)


def _append_script_lines(script: str, lines: list[str]) -> str:
    extra = [ln.strip() for ln in lines if str(ln).strip()]
    if not extra:
        return script or ""
    text = (script or "").rstrip()
    if text.endswith("}"):
        body = text[:-1].rstrip()
        indented = "\n".join(f"  {ln}" for ln in extra)
        return f"{body}\n{indented}\n}}\n"
    return text + ("\n" if text else "") + "\n".join(extra) + "\n"


def run_playwright_cli_engine(
    *,
    base_url: str,
    start_path: str,
    goal: str,
    username: str = "",
    password: str = "",
    llm: LlmRuntime,
    headed: bool,
    timeout_ms: int,
    max_steps: int = 25,
    plan: dict[str, Any] | None = None,
    ctx: dict[str, str] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
    on_screenshot: Callable[[bytes, dict[str, Any]], str | None] | None = None,
    midscene_model_config: dict[str, str] | None = None,
    storage_state: dict[str, Any] | str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
    skip_login: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Explore with playwright-cli; captcha via Midscene aiQuery then CLI fill.

    Explore-first: empty plan.matrix → explore by goal/checklist, then derive multi-case scripts.
    """
    explore_ctx = merge_explore_ctx(ctx, username=username, password=password)
    incoming = dict(plan or {})
    single_case = bool(incoming.get("_single_case"))
    has_preset_matrix = (
        not single_case
        and isinstance(incoming.get("matrix"), list)
        and bool(incoming["matrix"])
    )
    plan_obj = ensure_plan_matrix(incoming, synthesize_if_empty=has_preset_matrix)
    if not has_preset_matrix:
        plan_obj["matrix"] = []
    plan_obj["_single_case"] = single_case
    plan_obj.setdefault("goal", goal or "完成用户要求的界面操作")
    plan_obj.setdefault("start_path", start_path or "/")
    explore_goal = str(plan_obj.get("goal") or "")
    if explore_ctx and not skip_login:
        plan_obj["goal"] = explore_task_text(goal=explore_goal, ctx=explore_ctx)
    mid_cfg = midscene_model_config
    if mid_cfg is None:
        try:
            from app.deps import SessionLocal
            from app.services.ai.effective_ai_settings import get_midscene_runtime

            db = SessionLocal()
            try:
                mid_cfg = get_midscene_runtime(db).as_model_config()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            mid_cfg = None
    if on_log is not None:
        if single_case:
            mode = "单条用例"
        elif not has_preset_matrix:
            mode = "先探索后拆用例"
        else:
            mode = "矩阵驱动"
        on_log(
            f"开始 LangGraph + Playwright CLI 探索（{mode}）"
            + (
                "；复杂控件可用 Midscene（含验证码读图）…"
                if mid_cfg
                else "；未配置 Midscene，视觉步将跳过…"
            )
        )
    trace = run_cli_explore_loop(
        base_url=base_url,
        plan=plan_obj,
        ctx=explore_ctx,
        llm_cfg=llm,
        headed=headed,
        max_steps=max_steps,
        timeout_ms=timeout_ms,
        should_cancel=should_cancel,
        on_step=on_step,
        on_log=on_log,
        on_need_human=on_need_human,
        on_screenshot=on_screenshot,
        midscene_model_config=mid_cfg,
        storage_state=storage_state,
        extra_http_headers=extra_http_headers,
        local_storage=local_storage,
    )
    finish_reason = str(plan_obj.get("_explore_finish_reason") or "")
    if isinstance(plan, dict):
        if finish_reason:
            plan["_explore_finish_reason"] = finish_reason

    raw_matrix = plan_obj.get("matrix") if isinstance(plan_obj.get("matrix"), list) else []
    if single_case:
        script = compile_cli_trace_script(
            trace,
            start_path=start_path or str(plan_obj.get("start_path") or "/"),
            ctx=explore_ctx,
            username=username,
            password=password,
        )
        case_name = str(plan_obj.get("case_name") or goal or "UI 用例").strip() or "UI 用例"
        start = start_path or str(plan_obj.get("start_path") or "/")
        expected = str(plan_obj.get("expected") or (plan or {}).get("expected") or "")
        steps = _skeleton_steps_for_row(start=start, expected=expected)
        assert_steps = [s for s in steps if str(s.get("action") or "").startswith("assert")]
        if assert_steps and not script_has_assert(script):
            extra: list[str] = []
            for step in assert_steps:
                extra.extend(_assert_statements(step, start_path=start))
            script = _append_script_lines(script, extra)
        ready = bool((script or "").strip()) and bool(assert_steps)
        matrix = [
            {
                "id": "TC-001",
                "name": case_name,
                "start_url": start,
                "data": str(plan_obj.get("test_data") or (plan or {}).get("test_data") or ""),
                "expected": expected,
                "steps": steps,
                "ready": ready,
                "has_assert": bool(assert_steps),
            }
        ]
        case_scripts = [
            {
                "id": "TC-001",
                "name": case_name,
                "ready": ready,
                "code": script,
                "start_url": start,
                "needs_auth": False,
                "is_login": False,
            }
        ]
    elif not raw_matrix:
        if on_log is not None:
            on_log("探索完成，正在根据轨迹拆分多条用例脚本…")
        checklist = plan_obj.get("checklist") if isinstance(plan_obj.get("checklist"), list) else []
        matrix = derive_matrix_from_explore(
            prompt=str((plan or {}).get("user_prompt") or explore_goal or goal or ""),
            goal=explore_goal or goal or "",
            trace=trace,
            llm=llm,
            start_path=start_path or str(plan_obj.get("start_path") or "/"),
            checklist=[str(x) for x in checklist],
            case_name=str(plan_obj.get("case_name") or "") or None,
        )
        case_scripts = matrix_to_case_scripts(
            matrix,
            ctx=explore_ctx,
            username=username,
            password=password,
            start_path=start_path or "/",
        )
    else:
        matrix = hydrate_matrix_from_trace(raw_matrix, trace)
        # Fallback to step-wise bind when hydrate left nothing ready but trace exists
        if trace and not any(isinstance(r, dict) and r.get("ready") for r in matrix):
            matrix = bind_trace_to_matrix(raw_matrix, trace)
        case_scripts = matrix_to_case_scripts(
            matrix,
            ctx=explore_ctx,
            username=username,
            password=password,
            start_path=start_path or "/",
        )
    plan_obj["matrix"] = matrix
    plan_obj["case_scripts"] = case_scripts
    if isinstance(plan, dict):
        plan["matrix"] = matrix
        plan["case_scripts"] = case_scripts
        plan["_single_case"] = single_case
        plan["_explore_finish_reason"] = finish_reason or plan.get("_explore_finish_reason") or ""

    ready_codes = [c.get("code") or "" for c in case_scripts if c.get("ready") and (c.get("code") or "").strip()]
    script = ready_codes[0] if ready_codes else ""
    if not (script or "").strip():
        script = matrix_to_node_script(
            matrix,
            ctx=explore_ctx,
            username=username,
            password=password,
            start_path=start_path or "/",
            only_ready=True,
        )
    if not (script or "").strip():
        # Fallback: trace-only codegen (no asserts) — dry-run gate will fail without expect()
        script = compile_cli_trace_script(
            trace,
            start_path=start_path or "/",
            ctx=explore_ctx,
            username=username,
            password=password,
        )
    if not (script or "").strip():
        script = (
            "async function run(page, agent) {\n"
            f"  await page.goto({json_task(start_path or '/')});\n"
            "}\n"
        )
    raw_actions: list[dict[str, Any]] = []
    for item in trace:
        if not isinstance(item, dict) or item.get("status") == "error":
            continue
        kind = str(item.get("kind") or item.get("action") or "").strip()
        if kind in ("", "done"):
            continue
        row = {
            **item,
            "kind": kind if kind != "fill_captcha" else "fill_captcha",
        }
        raw_actions.append(row)
    annotated = _trace_from_actions(raw_actions, ctx=explore_ctx)
    if on_log is not None:
        n_ready = sum(1 for c in case_scripts if c.get("ready") and c.get("code"))
        mid_dup = "；已拦截重复 midscene 空转" if finish_reason == REPEATED_MIDSCENE_NOOP else ""
        split_hint = ""
        if single_case:
            split_hint = "（单条用例）"
        elif not has_preset_matrix and n_ready:
            split_hint = "（先探索后拆分）"
        on_log(
            f"Playwright CLI 探索完成，{len(annotated)} 步；"
            f"已生成 {n_ready}/{len(case_scripts) or n_ready} 条用例脚本"
            f"{split_hint}"
            f"{mid_dup}"
        )
    return annotated, script


def run_midscene_engine(
    *,
    base_url: str,
    start_path: str,
    goal: str,
    username: str = "",
    password: str = "",
    midscene: MidsceneRuntime,
    browser: str,
    headed: bool,
    viewport: dict[str, int] | None,
    timeout_ms: int,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
    on_screenshot: Callable[[bytes, dict[str, Any]], str | None] | None = None,
    llm: LlmRuntime | None = None,
    max_steps: int = 25,
    plan: dict[str, Any] | None = None,
    ctx: dict[str, str] | None = None,
    prefer_cli: bool = True,
    storage_state: dict[str, Any] | str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
    skip_login: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Prefer playwright-cli explore; legacy whole-task aiAct only when prefer_cli=False."""
    del browser, viewport
    explore_ctx = merge_explore_ctx(ctx, username=username, password=password)
    if prefer_cli and llm is not None:
        return run_playwright_cli_engine(
            base_url=base_url,
            start_path=start_path,
            goal=goal,
            username=username,
            password=password,
            llm=llm,
            headed=headed,
            timeout_ms=timeout_ms,
            max_steps=max_steps,
            plan=plan,
            ctx=explore_ctx,
            on_log=on_log,
            should_cancel=should_cancel,
            on_need_human=on_need_human,
            on_screenshot=on_screenshot,
            midscene_model_config=midscene.as_model_config(),
            storage_state=storage_state,
            extra_http_headers=extra_http_headers,
            local_storage=local_storage,
            skip_login=skip_login,
        )
    task = explore_task_text(goal=goal, ctx=explore_ctx)
    if on_log is not None:
        on_log("使用旧版 Midscene aiAct 整段探索…")
    actions = explore_with_midscene(
        base_url=base_url,
        start_path=start_path,
        task=task,
        model_config=midscene.as_model_config(),
        browser="chromium",
        headed=headed,
        viewport=None,
        timeout_ms=timeout_ms,
        on_log=on_log,
        should_cancel=should_cancel,
        on_need_human=on_need_human,
    )
    script = actions_to_node_script(
        actions,
        ctx=explore_ctx,
        username=username,
        password=password,
        start_path=start_path or "/",
        # Append submit only when actions already recorded a click locate (no 「提交」 hardcode).
        ensure_submit=True,
    )
    if "agent.ai" not in script and ".click(" not in script and ".fill(" not in script:
        # Still avoid silent empty script, but prefer empty goto over aiAct when possible
        script = (
            "async function run(page, agent) {\n"
            f"  await page.goto({json_task(start_path or '/')});\n"
            "}\n"
        )
        actions = [{"kind": "goto", "path": start_path or "/"}]
    return _trace_from_actions(actions, ctx=explore_ctx), script


def json_task(text: str) -> str:
    import json

    return json.dumps(text, ensure_ascii=False)
