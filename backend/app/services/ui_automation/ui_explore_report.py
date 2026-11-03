"""Explore report + case matrix helpers (handoff contract before codegen)."""

from __future__ import annotations

import json
import re
from typing import Any

_HASH_CLASS = re.compile(r"\.(?:css|sc|jsx|emotion)-[a-zA-Z0-9_-]+")
_DEEP_XPATH = re.compile(r"xpath=", re.I)
_CAPTCHA = re.compile(r"验证码|captcha|verify[\s_-]?code|vcode", re.I)
_CANVAS = re.compile(r"canvas|webgl|图表|chart", re.I)
_SHADOW = re.compile(r"shadow|iframe", re.I)
_TESTID = re.compile(r"data-testid|getByTestId|\[data-testid", re.I)
_ROLE = re.compile(r"getByRole|role=", re.I)
_PLACEHOLDER = re.compile(r"getByPlaceholder|placeholder", re.I)
_USER_FIELD = re.compile(r"账号|用户名|username|user\s*name", re.I)
_PASS_FIELD = re.compile(r"密码|password|passwd", re.I)
_CAPTCHA_REFRESH = re.compile(r"换一张|刷新验证码|refresh.*captcha", re.I)
_LOGIN_BTN = re.compile(r"登录|login|sign\s*in", re.I)
_BIZ_ANCHOR = re.compile(
    r"新建|新增|创建|项目管理|需求|列表|搜索|查询|编辑|删除|保存|提交|确定|详情|查看",
    re.I,
)


def _str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _skeleton_steps_for_row(*, start: str, expected: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "action": "goto",
            "target": start or "/",
            "value": start or "/",
            "driver": "playwright",
            "ready": True,
        }
    ]
    exp = (expected or "").strip()
    if exp:
        if re.search(r"url|路径|跳转|/|首页", exp, re.I):
            steps.append(
                {
                    "action": "assert_url",
                    "target": exp,
                    "value": exp,
                    "driver": "playwright",
                    "ready": True,
                }
            )
        else:
            steps.append(
                {
                    "action": "assert_text",
                    "target": exp,
                    "value": exp,
                    "driver": "playwright",
                    "ready": True,
                }
            )
    return steps


def skeletonize_matrix_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep id/name/start_url/expected; drop prose steps until explore hydrates."""
    out = dict(row)
    start = _str(out.get("start_url") or "/") or "/"
    expected = _str(out.get("expected"))
    out["steps"] = _skeleton_steps_for_row(start=start, expected=expected)
    out["ready"] = False
    out["has_assert"] = any(_str(s.get("action")).startswith("assert") for s in out["steps"])
    return out


def skeletonize_plan_matrix(plan: dict[str, Any]) -> dict[str, Any]:
    out = dict(plan)
    matrix = out.get("matrix") if isinstance(out.get("matrix"), list) else []
    out["matrix"] = [skeletonize_matrix_row(r) for r in matrix if isinstance(r, dict)]
    out["case_scripts"] = []
    return out


def matrix_from_checklist(
    *,
    goal: str,
    start_path: str,
    checklist: list[str],
    case_name: str | None = None,
) -> list[dict[str, Any]]:
    """Synthesize a minimal matrix when the LLM omitted one."""
    name = (case_name or goal or "UI 探索").strip() or "UI 探索"
    row = normalize_matrix_row(
        {
            "id": "UC-001",
            "name": name,
            "start_url": start_path or "/",
            "preconditions": "已配置环境与账号（如需）",
            "steps": [],
            "data": "",
            "expected": "目标步骤完成且关键断言通过",
        },
        index=1,
        default_start=start_path or "/",
    )
    return [skeletonize_matrix_row(row)] if row else []


_MATRIX_ACTIONS = ("goto", "fill", "click", "press", "assert_text", "assert_url", "assert_visible")


def _infer_action_from_text(text: str, *, default_start: str = "/") -> dict[str, Any]:
    """Best-effort convert a prose step into a structured matrix action."""
    t = (text or "").strip()
    low = t.lower()
    if re.search(r"打开|进入|goto|导航|访问", t) and ("/" in t or "页" in t or "url" in low):
        path = default_start
        m = re.search(r"(/[^\s，,；;]+)", t)
        if m:
            path = m.group(1)
        return {"action": "goto", "target": path, "value": path, "driver": "playwright", "ready": False}
    if re.search(r"断言|应|可见|出现|跳转|expected|assert", t, re.I):
        if re.search(r"url|路径|地址|跳转", t, re.I):
            return {"action": "assert_url", "target": "", "value": t, "driver": "playwright", "ready": False}
        return {"action": "assert_text", "target": t, "value": t, "driver": "playwright", "ready": False}
    if re.search(r"点击|单击|点|click|tap", t, re.I):
        return {"action": "click", "target": t, "value": "", "driver": "playwright", "ready": False}
    if re.search(r"输入|填写|填|fill|input", t, re.I):
        driver = "midscene" if _CAPTCHA.search(t) and re.search(r"读|识别|图片|图中", t) else "playwright"
        return {"action": "fill", "target": t, "value": "", "driver": driver, "ready": False}
    if re.search(r"按|press|回车|enter", t, re.I):
        return {"action": "press", "target": "", "value": "Enter", "driver": "playwright", "ready": False}
    return {"action": "click", "target": t, "value": "", "driver": "playwright", "ready": False}


def normalize_matrix_step(raw: Any, *, default_start: str = "/") -> dict[str, Any] | None:
    if isinstance(raw, str) and raw.strip():
        return _infer_action_from_text(raw.strip(), default_start=default_start)
    if not isinstance(raw, dict):
        return None
    action = _str(raw.get("action") or raw.get("kind") or raw.get("操作")).lower()
    if action not in _MATRIX_ACTIONS:
        prose = _str(raw.get("target") or raw.get("name") or raw.get("locate") or raw.get("prompt") or raw.get("text"))
        if prose:
            return _infer_action_from_text(prose, default_start=default_start)
        return None
    target = _str(raw.get("target") or raw.get("locate") or raw.get("prompt") or raw.get("selector") or raw.get("name"))
    value = raw.get("value")
    if value is None:
        value = raw.get("assert") or raw.get("text") or ""
    value = _str(value) if not isinstance(value, (int, float, bool)) else value
    driver = _str(raw.get("driver") or "playwright").lower() or "playwright"
    if driver not in ("playwright", "midscene"):
        driver = "playwright"
    if action == "goto" and not target:
        target = _str(value) or default_start
    step: dict[str, Any] = {
        "action": action,
        "target": target,
        "value": value,
        "driver": driver,
        "ready": bool(raw.get("ready")),
    }
    for key in ("placeholder", "role", "name", "selector", "cli_locator", "prompt", "locate", "captcha"):
        if raw.get(key) is not None and _str(raw.get(key)):
            step[key] = raw[key]
    return step


def normalize_matrix_row(raw: Any, *, index: int, default_start: str = "/") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _str(raw.get("name") or raw.get("title") or raw.get("用例名称"))
    if not name:
        return None
    cid = _str(raw.get("id") or raw.get("ID")) or f"UC-{index:03d}"
    steps_raw = raw.get("steps") or raw.get("操作步骤") or raw.get("操作")
    steps: list[dict[str, Any]] = []
    if isinstance(steps_raw, list):
        for item in steps_raw:
            step = normalize_matrix_step(item, default_start=default_start)
            if step:
                steps.append(step)
    elif isinstance(steps_raw, str) and steps_raw.strip():
        for part in re.split(r"[\n;；]", steps_raw):
            step = normalize_matrix_step(part.strip(), default_start=default_start)
            if step:
                steps.append(step)
    start = _str(raw.get("start_url") or raw.get("start_path") or raw.get("起始URL") or default_start) or "/"
    if not start.startswith("/") and not start.startswith("http"):
        start = "/" + start
    expected = _str(raw.get("expected") or raw.get("预期结果") or raw.get("预期"))
    if expected and not any(s.get("action", "").startswith("assert") for s in steps):
        if re.search(r"url|路径|跳转|/|首页", expected, re.I):
            steps.append(
                {
                    "action": "assert_url",
                    "target": expected,
                    "value": expected,
                    "driver": "playwright",
                    "ready": False,
                }
            )
        else:
            steps.append(
                {
                    "action": "assert_text",
                    "target": expected,
                    "value": expected,
                    "driver": "playwright",
                    "ready": False,
                }
            )
    if not any(s.get("action") == "goto" for s in steps):
        steps.insert(
            0,
            {
                "action": "goto",
                "target": start,
                "value": start,
                "driver": "playwright",
                "ready": True,
            },
        )
    ready = bool(raw.get("ready")) if "ready" in raw else all(
        s.get("ready") or s.get("action", "").startswith("assert") or s.get("action") == "goto"
        for s in steps
    )
    return {
        "id": cid,
        "name": name,
        "start_url": start,
        "preconditions": _str(raw.get("preconditions") or raw.get("前置条件") or raw.get("前置")),
        "steps": steps[:24],
        "data": _str(raw.get("data") or raw.get("测试数据") or raw.get("数据")),
        "expected": expected,
        "ready": ready,
    }


def normalize_matrix(
    raw: Any,
    *,
    goal: str = "",
    start_path: str = "/",
    checklist: list[str] | None = None,
    case_name: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for i, item in enumerate(raw, start=1):
            row = normalize_matrix_row(item, index=i, default_start=start_path)
            if row:
                rows.append(row)
    if rows:
        return rows[:30]
    return matrix_from_checklist(
        goal=goal,
        start_path=start_path,
        checklist=list(checklist or []),
        case_name=case_name,
    )


def ensure_plan_matrix(plan: dict[str, Any], *, synthesize_if_empty: bool = True) -> dict[str, Any]:
    """Normalize matrix rows. Explore-first plans pass synthesize_if_empty=False."""
    out = dict(plan)
    goal = _str(out.get("goal"))
    start = _str(out.get("start_path")) or "/"
    checklist = out.get("checklist") if isinstance(out.get("checklist"), list) else []
    case_name = _str(out.get("case_name")) or None
    raw = out.get("matrix")
    if isinstance(raw, list) and not raw and not synthesize_if_empty:
        out["matrix"] = []
        return out
    if raw is None and not synthesize_if_empty:
        out["matrix"] = []
        return out
    out["matrix"] = normalize_matrix(
        raw,
        goal=goal,
        start_path=start,
        checklist=[_str(x) for x in checklist],
        case_name=case_name,
    )
    if not out.get("case_name") and out["matrix"]:
        out["case_name"] = out["matrix"][0].get("name")
    return out


def _selector_blob(step: dict[str, Any]) -> str:
    parts = [
        step.get("selector"),
        step.get("locate"),
        step.get("placeholder"),
        step.get("name"),
        step.get("role"),
        step.get("prompt"),
        step.get("reason"),
        step.get("value"),
        step.get("action"),
        step.get("kind"),
    ]
    return " ".join(_str(p) for p in parts if p is not None)


def infer_driver(step: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Return (driver, reason, prompt). Playwright-first; Midscene only when unstable."""
    explicit = _str(step.get("driver")).lower()
    if explicit in ("playwright", "midscene"):
        reason = _str(step.get("reason")) or None
        prompt = _str(step.get("prompt") or step.get("locate")) or None
        return explicit, reason, prompt

    kind = _str(step.get("kind") or step.get("action")).lower()
    blob = _selector_blob(step)
    captcha = bool(step.get("captcha")) or bool(_CAPTCHA.search(blob))

    if kind in ("ai_act", "ai_tap", "ai_input", "ai_assert", "ai_query", "fill_captcha"):
        if captcha or kind == "fill_captcha":
            return (
                "midscene",
                "captcha-image: not machine-readable via locator",
                _str(step.get("prompt") or step.get("locate")) or "验证码图片中的字符",
            )
        return (
            "midscene",
            _str(step.get("reason")) or "unstable-dom: midscene action",
            _str(step.get("prompt") or step.get("locate")) or None,
        )

    if captcha and kind in ("fill", "input"):
        # Input box itself stays Playwright when we have a locator; read-image is midscene.
        if step.get("placeholder") or _TESTID.search(blob) or _ROLE.search(blob):
            return "playwright", None, None
        return (
            "midscene",
            "captcha-image: not machine-readable via locator",
            _str(step.get("locate")) or "验证码输入框",
        )

    if _CANVAS.search(blob):
        return "midscene", "canvas: target not in a11y tree", _str(step.get("locate")) or None
    if _SHADOW.search(blob) and not (_TESTID.search(blob) or _ROLE.search(blob)):
        return "midscene", "closed-shadow-or-iframe", _str(step.get("locate")) or None

    selector = _str(step.get("selector"))
    if selector and _HASH_CLASS.search(selector) and not (_TESTID.search(selector) or _ROLE.search(selector)):
        return "midscene", "unstable-dom: hash-class/xpath-only", _str(step.get("locate")) or selector
    if selector.startswith("xpath=") or (selector and _DEEP_XPATH.search(selector) and selector.count("/") > 6):
        if not (_TESTID.search(blob) or _ROLE.search(blob) or step.get("placeholder")):
            return "midscene", "unstable-dom: hash-class/xpath-only", _str(step.get("locate")) or selector

    if kind in ("goto", "press", "wait_for", "assert_visible", "assert_text", "assert_url", "done", "screenshot"):
        return "playwright", None, None

    if (
        step.get("placeholder")
        or step.get("role")
        or _TESTID.search(blob)
        or _ROLE.search(blob)
        or _PLACEHOLDER.search(blob)
        or (selector and not _HASH_CLASS.search(selector))
    ):
        return "playwright", None, None

    locate = _str(step.get("locate") or step.get("prompt"))
    if locate and not selector and not step.get("placeholder") and not step.get("role"):
        return "midscene", "snapshot: no usable semantic locator", locate

    return "playwright", None, None


def annotate_step(step: dict[str, Any], *, case_id: str = "UC-001", index: int = 1) -> dict[str, Any]:
    out = dict(step)
    driver, reason, prompt = infer_driver(out)
    out["driver"] = driver
    if reason:
        out["reason"] = reason
    elif "reason" in out and not _str(out.get("reason")):
        out.pop("reason", None)
    if prompt:
        out["prompt"] = prompt
    out.setdefault("case_id", case_id)
    out.setdefault("index", index)
    intent = _str(out.get("intent") or out.get("action") or out.get("kind"))
    if intent:
        out["intent"] = intent
    return out


def annotate_steps(
    steps: list[dict[str, Any]] | None,
    *,
    matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    case_id = "UC-001"
    if matrix and isinstance(matrix[0], dict) and matrix[0].get("id"):
        case_id = str(matrix[0]["id"])
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(steps or [], start=1):
        if not isinstance(raw, dict):
            continue
        out.append(annotate_step(raw, case_id=case_id, index=i))
    return out


def _locator_tier(step: dict[str, Any]) -> int | None:
    blob = _selector_blob(step)
    if _TESTID.search(blob):
        return 1
    if _ROLE.search(blob) or step.get("role"):
        return 2
    selector = _str(step.get("selector"))
    if selector.startswith("#") or re.search(r"\[id=", selector):
        return 3
    if step.get("placeholder") or _PLACEHOLDER.search(blob) or (selector and not selector.startswith("xpath=")):
        return 4
    if selector.startswith("xpath=") or "xpath=" in selector.lower():
        return 5
    return None


def build_explore_report(
    *,
    plan: dict[str, Any],
    steps: list[dict[str, Any]] | None,
    script: str = "",
    engine: str = "",
) -> dict[str, Any]:
    """Build the explore handoff report (matrix + per-step driver)."""
    plan = ensure_plan_matrix(plan)
    matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else []
    annotated = annotate_steps(steps, matrix=matrix)
    midscene_items = [
        {
            "index": s.get("index"),
            "intent": s.get("intent") or s.get("action"),
            "reason": s.get("reason"),
            "prompt": s.get("prompt") or s.get("locate"),
        }
        for s in annotated
        if s.get("driver") == "midscene"
    ]
    report_steps: list[dict[str, Any]] = []
    for s in annotated:
        entry: dict[str, Any] = {
            "case_id": s.get("case_id"),
            "index": s.get("index"),
            "intent": s.get("intent") or s.get("action") or s.get("kind"),
            "driver": s.get("driver") or "playwright",
            "action": s.get("action") or s.get("kind"),
            "selector": s.get("selector") or s.get("placeholder") or "",
            "value": s.get("value"),
            "assert": s.get("assert"),
            "status": s.get("status"),
        }
        tier = _locator_tier(s)
        if tier is not None and entry["driver"] == "playwright":
            entry["locator_tier"] = tier
        if s.get("reason"):
            entry["reason"] = s["reason"]
        if s.get("prompt"):
            entry["prompt"] = s["prompt"]
        if s.get("captcha"):
            entry["captcha"] = True
        report_steps.append(entry)

    mapping = [{"id": r.get("id"), "title": r.get("name")} for r in matrix if isinstance(r, dict)]
    captcha_steps = [s for s in annotated if s.get("captcha") or _CAPTCHA.search(_selector_blob(s))]
    captcha_meta = None
    if captcha_steps:
        captcha_meta = {
            "image_how": "未知（探索未探测到专用验证码图 locator）",
            "length": None,
            "charset": None,
            "refresh": "未知",
            "fail_text": "未知",
            "success_url": "未知",
            "note": "位数/字符集/失败文案以实际页面为准；生成代码时用 aiQuery 读图 + Playwright fill",
        }

    return {
        "case_name": _str(plan.get("case_name")) or (matrix[0].get("name") if matrix else "UI 探索"),
        "goal": _str(plan.get("goal")),
        "start_path": _str(plan.get("start_path")) or "/",
        "engine": engine or _str(plan.get("engine")),
        "matrix": matrix,
        "mapping": mapping,
        "steps": report_steps,
        "midscene_checklist": midscene_items,
        "captcha": captcha_meta,
        "blockers": [],
        "testid_suggestions": [],
        "has_script": bool((script or "").strip()),
        "summary": {
            "total_steps": len(report_steps),
            "playwright_steps": sum(1 for s in report_steps if s.get("driver") == "playwright"),
            "midscene_steps": len(midscene_items),
        },
    }


def _trace_label_blob(item: dict[str, Any]) -> str:
    return " ".join(
        _str(item.get(k))
        for k in ("name", "locate", "placeholder", "prompt", "selector", "cli_locator", "reason", "target")
    )


def _is_captcha_refresh_click(item: dict[str, Any]) -> bool:
    if _str(item.get("reason")) == "captcha_retry_refresh":
        return True
    kind = _str(item.get("kind") or item.get("action"))
    if kind not in ("click", "tap"):
        return False
    return bool(_CAPTCHA_REFRESH.search(_trace_label_blob(item)))


def _is_login_click(item: dict[str, Any]) -> bool:
    kind = _str(item.get("kind") or item.get("action"))
    if kind not in ("click", "tap"):
        return False
    if _is_captcha_refresh_click(item):
        return False
    return bool(_LOGIN_BTN.search(_trace_label_blob(item)))


def _is_captcha_fill_item(item: dict[str, Any]) -> bool:
    kind = _str(item.get("kind") or item.get("action"))
    if item.get("captcha") or kind == "fill_captcha" or item.get("explore_captcha_read"):
        return True
    if kind in ("fill", "input", "ai_input", "ai_query"):
        return bool(_CAPTCHA.search(_trace_label_blob(item)))
    return False


def _is_auth_credential_fill(item: dict[str, Any]) -> bool:
    """Username / password / captcha fills used to infer login boundary."""
    if _is_captcha_fill_item(item):
        return True
    kind = _str(item.get("kind") or item.get("action"))
    if kind not in ("fill", "input", "ai_input", "fill_captcha"):
        return False
    blob = _trace_label_blob(item)
    return bool(_USER_FIELD.search(blob) or _PASS_FIELD.search(blob))


def _login_boundary_index(collapsed: list[dict[str, Any]]) -> int:
    """Inclusive end index of the auth/login segment, or -1 if none / no post-login tail.

    Prefer an explicit login click; otherwise captcha or username/password fills
    when there are subsequent non-auth steps.
    """
    if not collapsed:
        return -1
    last_login = max((i for i, it in enumerate(collapsed) if _is_login_click(it)), default=-1)
    if last_login >= 0:
        return last_login if last_login < len(collapsed) - 1 else -1

    last_auth = max(
        (i for i, it in enumerate(collapsed) if _is_auth_credential_fill(it)),
        default=-1,
    )
    if last_auth < 0 or last_auth >= len(collapsed) - 1:
        return -1
    for j in range(last_auth + 1, len(collapsed)):
        if not _is_auth_credential_fill(collapsed[j]) and not _is_login_click(collapsed[j]):
            return last_auth
    return -1


def _fill_field_key(item: dict[str, Any]) -> str:
    if _is_captcha_fill_item(item):
        return "captcha"
    blob = _trace_label_blob(item)
    if _USER_FIELD.search(blob):
        return "username"
    if _PASS_FIELD.search(blob):
        return "password"
    return (
        f"fill:{_str(item.get('placeholder')) or _str(item.get('name')) or _str(item.get('locate')) or _str(item.get('selector')) or 'field'}"
    )


def _collapse_trace_for_bind(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Collapse explore noise into a codegen-friendly path.

    Drops captcha-refresh retries, repeated midscene noops, duplicate field fills
    (last wins), and repeated login clicks (keep last before post-login tail).
    """
    filtered: list[dict[str, Any]] = []
    seen_mid: set[str] = set()
    for raw in trace or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") == "error":
            continue
        kind = _str(raw.get("kind") or raw.get("action"))
        if kind in ("", "done"):
            continue
        if _is_captcha_refresh_click(raw):
            continue
        driver = _str(raw.get("driver") or "playwright").lower()
        if driver == "midscene" and not raw.get("explore_captcha_read") and not _is_captcha_fill_item(raw):
            prompt = _str(raw.get("prompt") or raw.get("locate") or raw.get("selector"))
            key = f"mid::{prompt}"
            if key in seen_mid:
                continue
            seen_mid.add(key)
        filtered.append(raw)

    if not filtered:
        return []

    last_login = max((i for i, it in enumerate(filtered) if _is_login_click(it)), default=-1)
    last_captcha = max((i for i, it in enumerate(filtered) if _is_captcha_fill_item(it)), default=-1)
    auth_end = max(last_login, last_captcha)

    head: list[dict[str, Any]] = []
    fills: dict[str, dict[str, Any]] = {}
    fill_order: list[str] = []
    captcha_item: dict[str, Any] | None = None
    login_item: dict[str, Any] | None = None
    other_auth: list[dict[str, Any]] = []
    tail: list[dict[str, Any]] = []
    saw_goto = False

    for i, item in enumerate(filtered):
        kind = _trace_action_kind(item)
        if auth_end >= 0 and i > auth_end:
            if _is_login_click(item) or _is_captcha_fill_item(item) or _is_captcha_refresh_click(item):
                continue
            tail.append(item)
            continue
        if kind == "goto":
            if not saw_goto:
                head.append(item)
                saw_goto = True
            continue
        if _is_captcha_fill_item(item):
            # Prefer fill_captcha / captcha fill over bare ai_query for codegen
            if item.get("explore_captcha_read") and kind == "ai_query" and captcha_item:
                continue
            captcha_item = item
            continue
        if _is_login_click(item):
            login_item = item
            continue
        if kind in ("fill", "input"):
            key = _fill_field_key(item)
            if key not in fills:
                fill_order.append(key)
            fills[key] = item
            continue
        other_auth.append(item)

    out: list[dict[str, Any]] = []
    out.extend(head)
    for key in fill_order:
        if key == "captcha":
            continue
        out.append(fills[key])
    if captcha_item is not None:
        out.append(captcha_item)
    out.extend(other_auth)
    if login_item is not None:
        out.append(login_item)
    out.extend(tail)
    return out


def _trace_action_kind(item: dict[str, Any]) -> str:
    kind = _str(item.get("kind") or item.get("action"))
    if kind == "fill_captcha":
        return "fill"
    if kind in ("tap", "ai_tap"):
        return "click"
    if kind in ("input", "ai_input"):
        return "fill"
    return kind


def _apply_trace_to_step(step: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    out = dict(step)
    kind = _trace_action_kind(item)
    if kind:
        out["action"] = kind if kind != "fill_captcha" else "fill"
    if item.get("driver"):
        out["driver"] = _str(item.get("driver")).lower() or out.get("driver") or "playwright"
    for key in (
        "placeholder",
        "role",
        "name",
        "selector",
        "cli_locator",
        "prompt",
        "locate",
        "captcha",
        "value",
        "path",
    ):
        if item.get(key) is not None and _str(item.get(key)):
            out[key] = item[key]
    target = (
        _str(item.get("cli_locator"))
        or _str(item.get("selector"))
        or _str(item.get("placeholder"))
        or _str(item.get("locate"))
        or _str(item.get("prompt"))
        or _str(item.get("name"))
        or _str(out.get("target"))
    )
    if kind == "goto":
        target = _str(item.get("path") or item.get("value") or target)
        out["value"] = target
    if target:
        out["target"] = target
    if kind == "fill" and item.get("value") is not None and not out.get("value"):
        out["value"] = item.get("value")
    if item.get("captcha") or kind == "fill_captcha":
        out["captcha"] = True
    # Bound when we have a usable locator or midscene prompt
    has_locator = bool(
        out.get("cli_locator")
        or out.get("placeholder")
        or out.get("name")
        or out.get("role")
        or (out.get("selector") and not _str(out.get("selector")).startswith("xpath="))
        or (out.get("driver") == "midscene" and (out.get("prompt") or out.get("locate")))
        or out.get("captcha")
        or kind == "goto"
    )
    out["ready"] = has_locator or bool(out.get("action", "").startswith("assert"))
    return out


_CAPTCHA_QUERY_HINT = "只读取验证码图片中的字符"


def _trace_item_to_step(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one explore trace row into a ready matrix step for codegen."""
    kind = _trace_action_kind(item)
    if kind in ("", "done", "ai_query"):
        # ai_query is folded into fill_captcha
        if item.get("explore_captcha_read") and not item.get("captcha"):
            return {
                "action": "fill",
                "driver": "midscene",
                "prompt": _str(item.get("prompt") or _CAPTCHA_QUERY_HINT),
                "locate": _str(item.get("locate") or item.get("prompt")),
                "captcha": True,
                "ready": True,
                "target": _str(item.get("prompt") or "验证码图片"),
            }
        if kind == "ai_query":
            return None
        return None
    step: dict[str, Any] = {
        "action": "fill" if kind == "fill_captcha" else kind,
        "driver": _str(item.get("driver") or "playwright").lower() or "playwright",
        "ready": True,
    }
    if kind == "fill_captcha" or item.get("captcha"):
        step["action"] = "fill"
        step["captcha"] = True
        step["kind"] = "fill_captcha"
    for key in (
        "placeholder",
        "role",
        "name",
        "selector",
        "cli_locator",
        "prompt",
        "locate",
        "value",
        "path",
    ):
        if item.get(key) is not None and _str(item.get(key)):
            step[key] = item[key]
    if kind == "goto":
        path = _str(item.get("path") or item.get("value") or "/")
        step["target"] = path
        step["value"] = path
    else:
        step["target"] = (
            _str(item.get("cli_locator"))
            or _str(item.get("selector"))
            or _str(item.get("placeholder"))
            or _str(item.get("locate"))
            or _str(item.get("name"))
            or _str(item.get("prompt"))
            or kind
        )
    if item.get("value") is not None and kind in ("fill", "fill_captcha", "press"):
        step["value"] = item.get("value")
    return step


def _collapsed_to_steps(collapsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in collapsed:
        if item.get("explore_captcha_read") and item.get("kind") == "ai_query":
            continue
        step = _trace_item_to_step(item)
        if step:
            steps.append(step)
    return steps


def _finalize_hydrated_row(raw: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    row = dict(raw)
    expected = _str(row.get("expected"))
    out_steps = list(steps)
    if expected and not any(_str(s.get("action")).startswith("assert") for s in out_steps):
        out_steps.extend(_skeleton_steps_for_row(start="", expected=expected)[1:])
    for s in out_steps:
        s["ready"] = True
    row["steps"] = out_steps[:40]
    row["ready"] = True
    row["has_assert"] = any(_str(s.get("action")).startswith("assert") for s in out_steps)
    return row


def _row_needs_login_prefix(row: dict[str, Any], index: int) -> bool:
    """Non-login cases need login steps prepended for independent dry-run.

    Skeleton matrices often copy plan start_path (/login) onto every row, so do not
    use start_url alone to skip the prefix — rely on case name / index.
    """
    if index <= 0:
        return False
    name = _str(row.get("name"))
    # Dedicated login cases are self-contained
    if re.search(r"登录|login|sign\s*in", name, re.I):
        return False
    return True


def _login_prefix_steps(collapsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Auth segment through the login boundary (inclusive)."""
    if not collapsed:
        return []
    boundary = _login_boundary_index(collapsed)
    if boundary < 0:
        # Username/password fills without a detected post-login tail
        fills = [
            it
            for it in collapsed
            if _trace_action_kind(it) in ("fill", "fill_captcha", "input", "goto")
            or _is_captcha_fill_item(it)
        ]
        return _collapsed_to_steps(fills[:6]) if fills else []
    return _collapsed_to_steps(collapsed[: boundary + 1])


def _merge_login_prefix(login_steps: list[dict[str, Any]], case_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not login_steps:
        return case_steps
    # Drop leading goto from case if login already navigates
    rest = list(case_steps)
    while rest and _str(rest[0].get("action")) == "goto":
        rest = rest[1:]
    return list(login_steps) + rest


def _split_even(items: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    if n <= 0:
        return []
    if not items:
        return [[] for _ in range(n)]
    if n == 1:
        return [list(items)]
    size = max(1, len(items) // n)
    chunks: list[list[dict[str, Any]]] = []
    for i in range(n):
        start = i * size
        end = len(items) if i == n - 1 else min(len(items), (i + 1) * size)
        chunks.append(items[start:end])
    return chunks


def _segment_name(chunk: list[dict[str, Any]], fallback: str = "后续操作") -> str:
    for item in chunk:
        name = _str(
            item.get("name")
            or item.get("placeholder")
            or item.get("locate")
            or item.get("prompt")
            or item.get("path")
        )
        if not name or len(name) > 40:
            continue
        if _USER_FIELD.search(name) or _PASS_FIELD.search(name) or _CAPTCHA.search(name):
            continue
        return name[:80]
    return fallback


def _split_tail_segments(
    tail: list[dict[str, Any]],
    labels: list[str] | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Split post-login (or whole) trace into labeled chunks for fallback cases."""
    if not tail:
        return []
    labels = [_str(c) for c in (labels or []) if _str(c)]
    if len(labels) >= 2:
        chunks = _split_even(tail, len(labels))
        out: list[tuple[str, list[dict[str, Any]]]] = []
        for i, label in enumerate(labels):
            chunk = chunks[i] if i < len(chunks) else []
            if chunk:
                out.append((label[:80] or f"用例{i + 1}", chunk))
        return out
    if len(labels) == 1:
        return [(labels[0][:80], list(tail))]

    cut_idxs = [0]
    for i, item in enumerate(tail):
        if i == 0:
            continue
        kind = _trace_action_kind(item)
        path = _str(item.get("path"))
        prev_path = _str(tail[i - 1].get("path"))
        if kind == "goto" and path and path != prev_path and "/login" not in path.lower():
            if i - cut_idxs[-1] >= 1:
                cut_idxs.append(i)
            continue
        blob = _trace_label_blob(item)
        if kind in ("click", "tap") and _BIZ_ANCHOR.search(blob) and i - cut_idxs[-1] >= 2:
            cut_idxs.append(i)

    cut_idxs = sorted(set(cut_idxs))
    segments: list[tuple[str, list[dict[str, Any]]]] = []
    for j, start in enumerate(cut_idxs):
        end = cut_idxs[j + 1] if j + 1 < len(cut_idxs) else len(tail)
        chunk = tail[start:end]
        if chunk:
            segments.append((_segment_name(chunk), chunk))
    if len(segments) >= 2:
        return segments

    n = 3 if len(tail) >= 9 else (2 if len(tail) >= 3 else 1)
    if n == 1:
        return [(_segment_name(tail), list(tail))]
    even = _split_even(tail, n)
    return [
        (_segment_name(c, fallback=f"用例{i + 1}"), c)
        for i, c in enumerate(even)
        if c
    ]


def _trace_summary_for_derive(collapsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact indexed actions for post-explore LLM case splitting."""
    out: list[dict[str, Any]] = []
    for i, item in enumerate(collapsed):
        kind = _trace_action_kind(item)
        if kind in ("", "done"):
            continue
        row: dict[str, Any] = {"i": i, "kind": kind}
        for key in ("path", "placeholder", "role", "name", "locate", "selector", "cli_locator", "prompt", "value"):
            if item.get(key) is not None and _str(item.get(key)):
                val = item.get(key)
                if key == "value" and len(_str(val)) > 40:
                    val = _str(val)[:40]
                row[key] = val
        if item.get("captcha") or item.get("explore_captcha_read"):
            row["captcha"] = True
        out.append(row)
    return out[:80]


def _is_mega_single_case(rows: list[dict[str, Any]], collapsed: list[dict[str, Any]]) -> bool:
    """True when LLM returned one case covering most of the explore trace."""
    if len(rows) != 1 or not collapsed:
        return False
    steps = rows[0].get("steps") if isinstance(rows[0].get("steps"), list) else []
    n_steps = len([s for s in steps if isinstance(s, dict) and _str(s.get("action"))])
    n_collapsed = len(collapsed)
    if n_collapsed <= 1:
        return False
    return n_steps >= max(1, int(n_collapsed * 0.8))


def _fallback_matrix_from_trace(
    *,
    trace: list[dict[str, Any]] | None,
    goal: str = "",
    start_path: str = "/",
    case_name: str | None = None,
    checklist: list[str] | None = None,
) -> list[dict[str, Any]]:
    """No LLM / LLM fail: split login + post-login segments (checklist or heuristics)."""
    collapsed = _collapse_trace_for_bind(trace)
    if not collapsed:
        return []
    steps_all = _collapsed_to_steps(collapsed)
    name0 = _str(case_name) or _str(goal) or "探索脚本"
    checks = [c for c in (checklist or []) if _str(c)]
    boundary = _login_boundary_index(collapsed)

    def _single() -> list[dict[str, Any]]:
        return [
            _finalize_hydrated_row(
                {
                    "id": "UC-001",
                    "name": name0[:80],
                    "start_url": start_path or "/",
                    "expected": "",
                },
                steps_all,
            )
        ]

    def _rows_from_segments(
        segments: list[tuple[str, list[dict[str, Any]]]],
        *,
        start_idx: int = 1,
        needs_auth: bool = False,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for offset, (label, chunk) in enumerate(segments):
            steps = _collapsed_to_steps(chunk)
            if not steps:
                continue
            idx = start_idx + offset
            row = _finalize_hydrated_row(
                {
                    "id": f"TC-{idx:02d}",
                    "name": _str(label)[:80] or f"用例{idx}",
                    "start_url": start_path or "/",
                    "expected": _str(label),
                },
                steps,
            )
            if needs_auth:
                row["needs_auth"] = True
            out.append(row)
        return out

    if boundary < 0 or boundary >= len(collapsed) - 1:
        # No clear login boundary: still try multi-segment split on long traces
        if len(collapsed) >= 4:
            rest_checks = checks
            if rest_checks and re.search(r"登录|login|账号", rest_checks[0], re.I):
                rest_checks = rest_checks[1:] or rest_checks
            segments = _split_tail_segments(collapsed, rest_checks if len(rest_checks) > 1 else None)
            rows = _rows_from_segments(segments, start_idx=1, needs_auth=False)
            if len(rows) >= 2:
                return rows
        return _single()

    head = collapsed[: boundary + 1]
    tail = collapsed[boundary + 1 :]
    login_steps = _collapsed_to_steps(head)
    rows: list[dict[str, Any]] = [
        _finalize_hydrated_row(
            {
                "id": "TC-01",
                "name": "登录成功",
                "start_url": start_path or "/",
                "expected": "离开登录页",
            },
            login_steps,
        )
    ]

    rest_checks: list[str]
    if checks:
        rest_checks = checks[1:] if re.search(r"登录|login|账号", checks[0], re.I) else list(checks)
        if not rest_checks:
            rest_checks = checks[1:] or []
    else:
        rest_checks = []

    segments = _split_tail_segments(tail, rest_checks if len(rest_checks) > 1 else None)
    if len(rest_checks) == 1 and not segments:
        segments = [(rest_checks[0], list(tail))]
    elif len(rest_checks) == 1 and len(segments) == 1:
        segments = [(rest_checks[0][:80], segments[0][1])]

    rows.extend(_rows_from_segments(segments, start_idx=2, needs_auth=True))
    return rows if len(rows) >= 2 else (_single() if not rows else rows)


def _apply_login_prefix_to_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark business cases as needs_auth; do not prepend login steps (reuse storageState)."""
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(rows):
        row = dict(raw)
        if _row_needs_login_prefix(row, i):
            row["needs_auth"] = True
        out.append(row)
    return out


def derive_matrix_from_explore(
    *,
    prompt: str = "",
    goal: str = "",
    trace: list[dict[str, Any]] | None,
    llm: Any = None,
    start_path: str = "/",
    checklist: list[str] | None = None,
    case_name: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """After one explore: split collapsed trace into multiple ready matrix rows."""
    collapsed = _collapse_trace_for_bind(trace)
    if not collapsed:
        return []

    summary = _trace_summary_for_derive(collapsed)
    rows: list[dict[str, Any]] = []

    if llm is not None and summary:
        try:
            from app.services.ui_automation.ui_ai_explorer import _extract_json

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是 UI 测试用例拆分助手。根据一次界面探索的操作轨迹，拆成多条可独立执行的用例。"
                        "只输出 JSON 对象，键 matrix(array)。每项："
                        "id(string 如 TC-01), name(string), expected(string 可断言终态), "
                        "step_indexes(array of int，对应输入 trace 摘要里的 i，按该用例需要的操作选取)。"
                        "规则：按业务场景拆分（登录、列表、新建等），不要把整段探索塞进一条；"
                        "登录相关操作通常归 TC-01；后续用例的 step_indexes 只含该业务段（不要重复登录步，系统会自动补登录）；"
                        "每条至少 1 个交互 step_indexes；索引必须来自输入。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_prompt": (prompt or "")[:800],
                            "goal": goal or "",
                            "checklist": list(checklist or [])[:20],
                            "start_path": start_path or "/",
                            "trace": summary,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            raw = llm.chat_complete(messages, model=model, db=None, cfg=llm, scene="UI 探索后拆用例")
            data = _extract_json(raw)
            matrix_raw = []
            if isinstance(data, dict):
                matrix_raw = data.get("matrix") or data.get("cases") or []
            if isinstance(matrix_raw, list):
                for idx, item in enumerate(matrix_raw, start=1):
                    if not isinstance(item, dict):
                        continue
                    name = _str(item.get("name") or item.get("title"))
                    if not name:
                        continue
                    cid = _str(item.get("id")) or f"TC-{idx:02d}"
                    indexes = item.get("step_indexes") or item.get("steps") or []
                    picked: list[dict[str, Any]] = []
                    if isinstance(indexes, list) and indexes and all(isinstance(x, int) for x in indexes):
                        for i in indexes:
                            if 0 <= i < len(collapsed):
                                step = _trace_item_to_step(collapsed[i])
                                if step:
                                    picked.append(step)
                    elif isinstance(indexes, list):
                        for part in indexes:
                            if isinstance(part, int) and 0 <= part < len(collapsed):
                                step = _trace_item_to_step(collapsed[part])
                                if step:
                                    picked.append(step)
                            elif isinstance(part, dict):
                                # Allow {i: n} or full step dict
                                if isinstance(part.get("i"), int) and 0 <= int(part["i"]) < len(collapsed):
                                    step = _trace_item_to_step(collapsed[int(part["i"])])
                                    if step:
                                        picked.append(step)
                                else:
                                    step = normalize_matrix_step(part, default_start=start_path or "/")
                                    if step:
                                        step["ready"] = True
                                        picked.append(step)
                    if not picked:
                        continue
                    expected = _str(item.get("expected"))
                    rows.append(
                        _finalize_hydrated_row(
                            {
                                "id": cid,
                                "name": name[:80],
                                "start_url": _str(item.get("start_url") or start_path) or "/",
                                "expected": expected,
                                "preconditions": _str(item.get("preconditions")),
                            },
                            picked,
                        )
                    )
        except Exception:  # noqa: BLE001
            rows = []

    if rows and _is_mega_single_case(rows, collapsed):
        rows = []

    if not rows:
        rows = _fallback_matrix_from_trace(
            trace=trace,
            goal=goal,
            start_path=start_path or "/",
            case_name=case_name,
            checklist=list(checklist or []),
        )
    return _apply_login_prefix_to_matrix(rows)


def hydrate_matrix_from_trace(
    matrix: list[dict[str, Any]] | None,
    trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """After explore: hydrate each matrix row from case_id-tagged (or split) trace."""
    rows_in = [r for r in (matrix or []) if isinstance(r, dict)]
    if not rows_in:
        return []

    by_case: dict[str, list[dict[str, Any]]] = {}
    for raw in trace or []:
        if not isinstance(raw, dict):
            continue
        cid = _str(raw.get("case_id"))
        if not cid:
            continue
        by_case.setdefault(cid, []).append(raw)

    if by_case:
        out: list[dict[str, Any]] = []
        for i, raw in enumerate(rows_in):
            cid = _str(raw.get("id"))
            items = by_case.get(cid) or []
            if not items:
                out.append(skeletonize_matrix_row(raw))
                continue
            steps = _collapsed_to_steps(_collapse_trace_for_bind(items))
            if not steps:
                out.append(skeletonize_matrix_row(raw))
                continue
            row = _finalize_hydrated_row(raw, steps)
            if _row_needs_login_prefix(raw, i):
                row["needs_auth"] = True
            out.append(row)
        return out

    # No case_id tags: single row gets full trace; multi-row splits at login then distributes tail.
    collapsed = _collapse_trace_for_bind(trace)
    if not collapsed:
        return [skeletonize_matrix_row(r) for r in rows_in]

    if len(rows_in) == 1:
        return [_finalize_hydrated_row(rows_in[0], _collapsed_to_steps(collapsed))]

    last_login = max((i for i, it in enumerate(collapsed) if _is_login_click(it)), default=-1)
    if last_login < 0:
        chunks = _split_even(collapsed, len(rows_in))
        return [
            _finalize_hydrated_row(raw, _collapsed_to_steps(chunk))
            if chunk
            else skeletonize_matrix_row(raw)
            for raw, chunk in zip(rows_in, chunks)
        ]

    head = collapsed[: last_login + 1]
    tail = collapsed[last_login + 1 :]
    login_steps = _collapsed_to_steps(head)
    out = [_finalize_hydrated_row(rows_in[0], login_steps)]
    rest = rows_in[1:]
    if not rest:
        return out
    if not tail:
        out.extend(skeletonize_matrix_row(r) for r in rest)
        return out
    for i, (raw, chunk) in enumerate(zip(rest, _split_even(tail, len(rest))), start=1):
        steps = _collapsed_to_steps(chunk)
        if not steps:
            out.append(skeletonize_matrix_row(raw))
            continue
        row = _finalize_hydrated_row(raw, steps)
        if _row_needs_login_prefix(raw, i):
            row["needs_auth"] = True
        out.append(row)
    return out


def bind_trace_to_matrix(
    matrix: list[dict[str, Any]] | None,
    trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Fill matrix step targets from explore trace (deduped). Marks row.ready.

    Prefer hydrate_matrix_from_trace for post-explore codegen (skeleton → code).
    """
    # If matrix rows look like skeletons (only goto/assert), hydrate from full trace.
    rows_in = [r for r in (matrix or []) if isinstance(r, dict)]
    if rows_in:
        interactive = 0
        for r in rows_in:
            for s in r.get("steps") or []:
                if isinstance(s, dict):
                    a = _str(s.get("action"))
                    if a and a not in ("goto",) and not a.startswith("assert"):
                        interactive += 1
        if interactive <= 1:
            return hydrate_matrix_from_trace(matrix, trace)
    collapsed = _collapse_trace_for_bind(trace)
    rows: list[dict[str, Any]] = []
    cursor = 0
    for raw in matrix or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        steps_out: list[dict[str, Any]] = []
        for step in row.get("steps") or []:
            if not isinstance(step, dict):
                continue
            action = _str(step.get("action"))
            if action.startswith("assert") or action == "goto":
                bound = dict(step)
                if action == "goto":
                    bound["ready"] = True
                    if not bound.get("target"):
                        bound["target"] = bound.get("value") or row.get("start_url") or "/"
                else:
                    # Asserts are codegen-ready from plan text; no locator bind required.
                    bound["ready"] = True
                steps_out.append(bound)
                continue
            if cursor < len(collapsed):
                item = collapsed[cursor]
                # Prefer matching action kinds; otherwise take next interactive step.
                while cursor < len(collapsed):
                    item = collapsed[cursor]
                    tkind = _trace_action_kind(item)
                    if tkind == action or (
                        action in ("fill", "click", "press") and tkind in ("fill", "click", "press", "goto")
                    ):
                        if tkind == "goto" and action != "goto":
                            cursor += 1
                            continue
                        steps_out.append(_apply_trace_to_step(step, item))
                        cursor += 1
                        break
                    cursor += 1
                else:
                    unbound = dict(step)
                    unbound["ready"] = False
                    steps_out.append(unbound)
            else:
                unbound = dict(step)
                unbound["ready"] = False
                steps_out.append(unbound)
        row["steps"] = steps_out
        interactive = [s for s in steps_out if _str(s.get("action")) not in ("goto",) and not _str(s.get("action")).startswith("assert")]
        row["ready"] = bool(steps_out) and all(
            s.get("ready") for s in steps_out if not _str(s.get("action")).startswith("assert")
        ) and (
            not interactive or any(s.get("ready") for s in interactive)
        )
        # Require at least one assert per case for dry-run gate
        row["has_assert"] = any(_str(s.get("action")).startswith("assert") for s in steps_out)
        rows.append(row)
    return rows


def matrix_bind_issues(matrix: list[dict[str, Any]] | None) -> list[str]:
    """Human-readable reasons the matrix cannot drive a passing dry-run.

    Requires at least one ready case with asserts (typically the primary path).
    Unbound secondary cases are skipped by codegen and do not alone fail the gate.
    """
    issues: list[str] = []
    rows = [r for r in (matrix or []) if isinstance(r, dict)]
    if not rows:
        return ["用例矩阵为空"]
    ready_rows = [r for r in rows if r.get("ready")]
    if not ready_rows:
        first = rows[0]
        cid = _str(first.get("id") or first.get("name") or "UC-001")
        unbound = []
        for step in first.get("steps") or []:
            if not isinstance(step, dict):
                continue
            action = _str(step.get("action"))
            if action.startswith("assert") or action == "goto":
                continue
            if not step.get("ready"):
                unbound.append(f"{action} {step.get('target') or ''}".strip())
        if unbound:
            issues.append(f"{cid}: 步骤未绑定 locator（{unbound[0]}）")
        else:
            issues.append(f"{cid}: 用例未就绪")
        return issues
    for row in ready_rows:
        cid = _str(row.get("id") or row.get("name") or "?")
        steps = row.get("steps") or []
        has_assert = any(
            _str(s.get("action")).startswith("assert") for s in steps if isinstance(s, dict)
        )
        if not has_assert:
            issues.append(f"{cid}: 缺少 assert_text/assert_url")
    return issues[:12]
