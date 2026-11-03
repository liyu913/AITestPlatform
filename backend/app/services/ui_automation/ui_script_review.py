"""Post-codegen AI review / fix for UI scenario agent case scripts."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.services.ai import llm
from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.ui_automation.ui_ai_explorer import _extract_json
from app.services.ui_automation.ui_locator_stability import (
    collect_explored_locators,
    locators_allowed,
    script_locator_exprs,
)
from app.services.ui_automation.ui_node_script import is_node_ui_script

log = logging.getLogger(__name__)


class ScriptReviewIssue(BaseModel):
    case_id: str = ""
    severity: str = "warn"  # info | warn | error
    message: str = ""


class ScriptReviewCaseOut(BaseModel):
    id: str
    code: str = ""


class ScriptReviewOut(BaseModel):
    summary: str = ""
    issues: list[ScriptReviewIssue] = Field(default_factory=list)
    cases: list[ScriptReviewCaseOut] = Field(default_factory=list)


_REVIEW_SYSTEM = (
    "你是 UI 自动化 Playwright 脚本审核助手。根据用例名与预期，检查并修正 Node/Python 脚本。"
    "只输出一个 JSON 对象，不要 Markdown，不要一次输出多个 JSON。"
    "JSON 形状：{"
    '"summary":"一句话摘要",'
    '"issues":[{"case_id":"TC-01","severity":"error|warn|info","message":"问题说明"}],'
    '"cases":[{"id":"TC-01","code":"修订后的完整脚本"}]'
    "}。"
    "审核与修正规则："
    "1) 步骤必须与用例名/预期语义一致：登录成功类用例只能完成登录相关操作与断言，"
    "禁止点击「新建项目」「创建需求」等业务按钮。"
    "2) 登录提交应点击登录/提交/确定等按钮，不要点验证码图片或刷新验证码。"
    "3) 验证码：用 agent.aiQuery 读图中字符，再用 page.getByRole/getByPlaceholder/.locator().fill 填入；"
    "禁止写死验证码字符，禁止用 aiInput 操作验证码。"
    "4) 禁止断言错误提示/必填提示；须保留成功态 expect（如离开登录页、可见成功文案）。"
    "5) page.goto 必须是以 / 开头的相对路径；账号密码优先 env.username / env.password。"
    "6) Node 脚本必须保留 async function run(page, agent)；Python 保留 def run(page)。不要写 import。"
    "7) 仅返回需要修改的 cases；无问题时 cases 可为空数组，issues 也可为空。"
    "8) 修订后的 code 必须是完整可执行脚本，不要省略中间步骤。"
    "9) 定位器必须来自该用例的 explored_locators（探索得到的真实页面元素）。"
    "可删掉错误步骤，或改用同案 explored_locators 中更合适/更靠前（更稳定）的表达式；"
    "禁止编造未出现在 explored_locators 中的按钮名、testid、CSS。"
    "稳定性优先顺序：getByTestId > getByRole+name > getByLabel > getByPlaceholder > getByText > locator。"
    "断言中的 getByText / URL 可保留；交互 fill/click 不得引入白名单外 locator。"
)


def _expected_for_case(
    case_id: str,
    matrix: list[dict[str, Any]] | None,
) -> str:
    for row in matrix or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip() == case_id:
            return str(row.get("expected") or "").strip()
    return ""


def _matrix_row_for(
    case_id: str,
    matrix: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for row in matrix or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip() == case_id:
            return row
    return None


def _report_steps(explore_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(explore_report, dict):
        return []
    steps = explore_report.get("steps")
    return steps if isinstance(steps, list) else []


def _explored_for_case(
    case_id: str,
    *,
    matrix: list[dict[str, Any]] | None,
    explore_report: dict[str, Any] | None,
    original_code: str = "",
) -> list[str]:
    row = _matrix_row_for(case_id, matrix)
    locs = collect_explored_locators(
        matrix_row=row,
        report_steps=_report_steps(explore_report),
        case_id=case_id,
    )
    if locs:
        return locs
    # Fallback: locators already present in the explore-compiled script
    return sorted(script_locator_exprs(original_code, interaction_only=True))


def _payload_cases(
    case_scripts: list[dict[str, Any]],
    matrix: list[dict[str, Any]] | None,
    explore_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in case_scripts:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        cid = str(row.get("id") or "").strip() or "TC"
        out.append(
            {
                "id": cid,
                "name": str(row.get("name") or "").strip() or cid,
                "expected": _expected_for_case(cid, matrix),
                "ready": bool(row.get("ready")),
                "code": code,
                "explored_locators": _explored_for_case(
                    cid,
                    matrix=matrix,
                    explore_report=explore_report,
                    original_code=code,
                ),
            }
        )
    return out


def _apply_fixed_codes(
    case_scripts: list[dict[str, Any]],
    fixed: dict[str, str],
) -> list[dict[str, Any]]:
    if not fixed:
        return case_scripts
    out: list[dict[str, Any]] = []
    for row in case_scripts:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        cid = str(item.get("id") or "").strip()
        if cid and cid in fixed:
            code = fixed[cid]
            if not code.endswith("\n"):
                code += "\n"
            # Keep runnable shape
            if is_node_ui_script(str(item.get("code") or "")) and "async function run" not in code:
                pass  # reject bad fix below by skipping
            else:
                item["code"] = code
                if code.strip():
                    item["ready"] = True
        out.append(item)
    return out


def review_and_fix_case_scripts(
    case_scripts: list[dict[str, Any]] | None,
    *,
    goal: str = "",
    checklist: list[str] | None = None,
    matrix: list[dict[str, Any]] | None = None,
    explore_report: dict[str, Any] | None = None,
    llm_cfg: LlmRuntime,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LLM-review generated scripts; return (possibly fixed scripts, review meta).

    On LLM/parse failure, returns original scripts and meta with ``skipped``/``error``.
    Rejects revisions that invent locators outside explored_locators allowlist.
    """
    scripts = [dict(c) for c in (case_scripts or []) if isinstance(c, dict)]
    payload = _payload_cases(scripts, matrix, explore_report)
    empty_meta: dict[str, Any] = {
        "summary": "",
        "issues": [],
        "fixed_count": 0,
        "skipped": False,
        "rejected_invented": 0,
    }
    if not payload:
        empty_meta["skipped"] = True
        empty_meta["summary"] = "无待审核脚本"
        return scripts, empty_meta

    allow_by_id = {
        str(c["id"]): list(c.get("explored_locators") or []) for c in payload
    }

    user_payload = {
        "goal": goal or "",
        "checklist": [str(x) for x in (checklist or [])],
        "cases": payload,
    }
    messages = [
        {"role": "system", "content": _REVIEW_SYSTEM},
        {
            "role": "user",
            "content": (
                json.dumps(user_payload, ensure_ascii=False)
                + "\n请审核并输出含 summary、issues、cases 的 JSON 对象。"
            ),
        },
    ]
    try:
        raw = llm.chat_complete(
            messages,
            model=model,
            db=None,
            cfg=llm_cfg,
            json_mode=True,
            scene="UI 脚本自审",
        )
        data = _extract_json(raw)
        parsed = ScriptReviewOut.model_validate(data if isinstance(data, dict) else {})
    except Exception as ex:  # noqa: BLE001
        log.warning("ui script review failed: %s", ex)
        empty_meta["skipped"] = True
        empty_meta["error"] = str(ex)
        empty_meta["summary"] = f"脚本自审跳过：{ex}"
        return scripts, empty_meta

    fixed_map: dict[str, str] = {}
    reject_issues: list[dict[str, str]] = []
    rejected = 0
    for item in parsed.cases:
        cid = str(item.id or "").strip()
        code = str(item.code or "").strip()
        if not cid or not code:
            continue
        if "async function run" not in code and "def run" not in code:
            continue
        allow = allow_by_id.get(cid) or []
        if allow and not locators_allowed(code, allow):
            rejected += 1
            reject_issues.append(
                {
                    "case_id": cid,
                    "severity": "warn",
                    "message": "修订引入了探索白名单外的 locator，已丢弃该修订并保留探索编译稿",
                }
            )
            continue
        fixed_map[cid] = code if code.endswith("\n") else code + "\n"

    updated = _apply_fixed_codes(scripts, fixed_map)
    issues = [i.model_dump() for i in parsed.issues] + reject_issues
    summary = (parsed.summary or "").strip()
    if rejected and not fixed_map:
        summary = (summary + "；" if summary else "") + f"已拒绝 {rejected} 条编造 locator 的修订"
    elif not summary:
        if fixed_map:
            summary = f"已修正 {len(fixed_map)} 条脚本"
        elif issues:
            summary = f"发现 {len(issues)} 个问题"
        else:
            summary = "脚本自审通过，无需修改"
    meta = {
        "summary": summary,
        "issues": issues,
        "fixed_count": len(fixed_map),
        "skipped": False,
        "rejected_invented": rejected,
    }
    return updated, meta
