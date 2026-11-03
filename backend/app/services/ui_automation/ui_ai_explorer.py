"""AI-assisted UI exploration: functional case → real browser → UiSteps + Playwright code."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiAiExploreJob, UiEnvironment
from app.services.ai import llm
from app.services.ai.effective_ai_settings import LlmRuntime, get_llm_runtime
from app.services.ui_automation.ui_run_executor import _build_ctx, _load_env
from app.services.ui_automation.ui_url_guard import UnsafeUiUrlError, assert_same_host, join_base_and_path
from app.user_messages import TEST_CASE_NOT_FOUND, UI_ENV_BASE_URL_REQUIRED, UI_ENV_NOT_FOUND

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 25
_SUPPORTED_ACTIONS = ("goto", "click", "fill", "select_option", "press", "assert_visible", "assert_text", "assert_url", "wait_for", "done")
_PW_MAX_ATTEMPTS = 3
_PW_ATTEMPT_TIMEOUT_MS = 2500
_MIDSCENE_FAIL_REASON = "playwright: 3 attempts failed"
_CAPTCHA_HINT = re.compile(r"验证码|captcha|verify[\s_-]?code|vcode", re.I)


def _first_json_object(text: str) -> str | None:
    """Return the first top-level `{...}` span, tolerating trailing sibling objects."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        chunk = _first_json_object(text)
        if not chunk:
            raise
        return json.loads(chunk)


_PLACEHOLDER_EXPECTED = {"", "—", "-", "无"}


def _step_expected(raw: str | None) -> str:
    text = (raw or "").strip()
    if text in _PLACEHOLDER_EXPECTED:
        return ""
    return text


def build_plan_from_testcase(
    *,
    title: str,
    steps: list[dict[str, str]],
    base_url: str,
    llm_cfg: LlmRuntime,
    model: str | None = None,
    test_data: str | None = None,
    overall_expected: str | None = None,
    precondition: str | None = None,
    extra_prompt: str | None = None,
    skip_login: bool = False,
) -> dict[str, Any]:
    step_lines = "\n".join(
        f"{i + 1}. 操作: {s.get('action', '')}"
        + (f" | 期望: {exp}" if (exp := _step_expected(s.get("expected"))) else "")
        for i, s in enumerate(steps)
    )
    skip_login_rule = (
        "当前浏览器已注入登录态或 Token，不要规划登录/注册步骤。"
        if skip_login
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Web UI 自动化探索规划助手。根据这一条功能用例写出探索计划。"
                "只输出 JSON 对象，键："
                "goal(string), start_path(string, 以/开头的相对路径), "
                "checklist(array of string 探索检查点)。"
                "这是单独执行的一条用例：不要输出 matrix，不要扩展其它正反场景。"
                "goal 必须覆盖提供的步骤、测试数据和总体期望。"
                "步骤中的期望占位符（—）忽略，以总体期望为准。"
                f"{skip_login_rule}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"被测 base_url: {base_url}\n"
                f"用例标题: {title}\n"
                f"前置条件: {(precondition or '').strip() or '(无)'}\n"
                f"测试数据: {(test_data or '').strip() or '(无)'}\n"
                f"总体期望: {(overall_expected or '').strip() or '(无)'}\n"
                f"补充提示: {(extra_prompt or '').strip() or '(无)'}\n"
                f"步骤:\n{step_lines or '(无步骤，请根据标题规划)'}\n"
            ),
        },
    ]
    raw = llm.chat_complete(messages, model=model, db=None, cfg=llm_cfg, scene="UI 探索规划")
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("计划不是 JSON 对象")
    start = str(data.get("start_path") or "/").strip() or "/"
    if not start.startswith("/"):
        start = "/" + start
    checklist = data.get("checklist") if isinstance(data.get("checklist"), list) else []
    return {
        "goal": str(data.get("goal") or title),
        "start_path": start,
        "checklist": [str(x) for x in checklist][:20],
        "case_name": title,
        "matrix": [],
        "test_data": (test_data or "").strip(),
        "expected": (overall_expected or "").strip(),
        "preconditions": (precondition or "").strip(),
        "_single_case": True,
    }


def nonempty_ctx_keys(ctx: dict[str, str] | None) -> list[str]:
    """Template keys advertised to decide — only keys with non-empty values."""
    out: list[str] = []
    for k, v in (ctx or {}).items():
        if str(k).strip() and str(v or "").strip():
            out.append(str(k))
    return out


def decide_next_action(
    *,
    plan: dict[str, Any],
    snapshot: str,
    url: str,
    trace: list[dict[str, Any]],
    ctx_keys: list[str],
    llm_cfg: LlmRuntime,
    model: str | None = None,
) -> dict[str, Any]:
    recent = trace[-8:]
    keys = [str(k).strip() for k in (ctx_keys or []) if str(k).strip()]
    if keys:
        var_rule = (
            f"可用模板变量键: {', '.join(keys)}，可用 {{{{key}}}} 写法。"
            "fill 时优先按控件可见名/placeholder/语义匹配上述键，value 用对应 {{key}}；"
            "禁止编造已有键对应的值；不要把 A 键的值填进明显不匹配的 B 控件。"
        )
    else:
        var_rule = (
            "可用模板变量键: 无。"
            "fill 的 value 必须从 goal / test_data / active_case / matrix 步骤文案取字面值；"
            "禁止输出未定义的 {{…}} 或空 value。"
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Playwright UI 探索代理。根据 accessibility 快照与用户目标决定下一步。"
                "每次只输出一个 JSON 对象（禁止一次输出多个 JSON 或数组），字段："
                "action 取值 goto|click|fill|select_option|press|assert_visible|assert_text|assert_url|wait_for|done；"
                "selector(string)：优先 snapshot 中的 ref（如 e12 / ref=e12）；"
                "其次 role=textbox[name=可见名]、role=button[name=可见名]、label=可见名、"
                "placeholder=可见占位符、testid=x；输入框不要用 text=；"
                "value(string, fill/goto/press/assert 用)；"
                "assert(string, wait_for 状态或 assert_text)；"
                "reason(string)；可选 placeholder/role/name（必须来自当前 snapshot 可见文案）；"
                "可选 case_id(string)：当前正在探索的矩阵用例 id。"
                "有 dialog 时优先操作弹窗内控件。优先 ref，其次 data-testid、role+name、label、placeholder。"
                "问题态→React：若 snapshot 出现校验/错误/操作无效（如必填、不能为空、失败、错误提示；"
                "不含输入框自身 placeholder「请输入…」），或提交后弹窗未关且字段仍空，"
                "下一步必须纠偏推进 goal（fill 空必填、换控件、关错态等），"
                "禁止对错误/校验文案本身反复 assert_text/assert_visible；禁止在必填仍空时再次 click 提交。"
                "纠偏与取值由你决策（系统不替你改写 action）："
                "fill value 只来自 goal / test_data / active_case 步骤字面值 / 已声明 {{key}}；"
                "禁止把用例标题、expected、「登录成功」等结果态文案当作 fill value；"
                "成功态文案仅用于 assert_*，不要填进输入框。"
                "若 plan 含 _explore_nudge 或 candidates：必须遵守；下一步 selector 优先用候选列表中的 ref。"
                "连续对同一 ref fill 无效时：改为 click 候选中的表单按钮，勿再 fill 同一框。"
                "输入框纠偏时仍可 fill（执行层会先清空再写入）。"
                "若 snapshot/plan 含 already_filled_refs 或 _filled_refs：这些 ref 已填成功，禁止再 fill。"
                "assert_* 仅用于成功态；禁止对校验/错误提示反复 assert。"
                "驱动：有稳定 ref 用 playwright；无 ref/canvas/需视觉理解时 driver=midscene + prompt。"
                "验证码：可用 midscene 读图；填框用 playwright；读图后 click 提交类按钮，勿重复读图。"
                "禁止在可用 Playwright locator 时滥用 midscene。"
                "若提供了 active_case：action=done 仅表示当前用例完成，remaining_cases 非空时系统会切下一用例。"
                "若无 matrix / 无 active_case（先探索模式）：按 goal 与 checklist 尽量覆盖相关功能后再 action=done；"
                "不要过早结束，checklist 未覆盖完时继续操作。"
                "goto 的 value 必须是相对路径以 / 开头。"
                f"{var_rule}"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": plan.get("goal"),
                    "checklist": plan.get("checklist"),
                    "matrix": plan.get("matrix"),
                    "active_case": plan.get("_active_case"),
                    "remaining_cases": plan.get("_remaining_cases") or [],
                    "test_data": plan.get("test_data") or "",
                    "current_url": url,
                    "recent_trace": recent,
                    "nudge": plan.get("_explore_nudge") or "",
                    "filled_refs": plan.get("_filled_refs") or [],
                    "snapshot": snapshot[:12000],
                },
                ensure_ascii=False,
            ),
        },
    ]
    raw = llm.chat_complete(messages, model=model, db=None, cfg=llm_cfg, scene="UI 探索决策")
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("下一步不是 JSON 对象")
    action = str(data.get("action") or "done").strip()
    if action not in _SUPPORTED_ACTIONS:
        action = "done"
    driver = str(data.get("driver") or "").strip().lower()
    out: dict[str, Any] = {
        "action": action,
        "selector": (str(data.get("selector") or "").strip() or None),
        "value": data.get("value"),
        "assert": data.get("assert"),
        "reason": str(data.get("reason") or ""),
    }
    if driver in ("playwright", "midscene"):
        out["driver"] = driver
    prompt = str(data.get("prompt") or "").strip()
    if prompt:
        out["prompt"] = prompt
    for key in ("placeholder", "role", "name", "locate"):
        text = str(data.get(key) or "").strip()
        if text:
            out[key] = text
    case_id = str(data.get("case_id") or "").strip()
    if not case_id:
        active = plan.get("_active_case") if isinstance(plan.get("_active_case"), dict) else {}
        case_id = str((active or {}).get("id") or "").strip()
    if case_id:
        out["case_id"] = case_id
    return out


def _simplify_aria(node: Any, depth: int = 0, limit: int = 6) -> list[str]:
    """Flatten a11y tree. Nameless generic wrappers do not consume depth (Ant Design modals)."""
    if depth > limit or not isinstance(node, dict):
        return []
    lines: list[str] = []
    role = str(node.get("role") or "")
    name = str(node.get("name") or "")
    passthrough = role in ("", "generic") and not name
    if not passthrough and (role or name):
        indent = "  " * depth
        extra = ""
        for k in ("value", "description", "checked", "disabled", "expanded", "selected"):
            if k in node and node[k] not in (None, ""):
                extra += f" {k}={node[k]}"
        lines.append(f"{indent}{role}: {name}{extra}".rstrip())
    next_depth = depth if passthrough else depth + 1
    for child in node.get("children") or []:
        lines.extend(_simplify_aria(child, next_depth, limit))
    return lines


def _dom_form_hints(page: Any) -> list[str]:
    """Supplement a11y snapshot with label/placeholder from open dialog or page forms."""
    try:
        rows = page.evaluate(
            """() => {
              const dlg = document.querySelector('[role="dialog"]');
              const root = dlg || document.body;
              if (!root) return [];
              return [...root.querySelectorAll('input:not([type="hidden"]), textarea, select')]
                .slice(0, 24)
                .map((el) => {
                  let label = '';
                  if (el.id) {
                    const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (lab) label = (lab.innerText || '').trim();
                  }
                  if (!label) {
                    const wrap = el.closest('.ant-form-item, [class*="form-item"], label');
                    const lab = wrap && (wrap.matches('label') ? wrap : wrap.querySelector('label'));
                    if (lab) label = (lab.innerText || '').trim();
                  }
                  label = label.replace(/^\\s*[＊*]\\s*/, '').trim();
                  return {
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    label,
                    placeholder: el.getAttribute('placeholder') || '',
                    name: el.getAttribute('name') || '',
                  };
                });
            }"""
        )
    except Exception:  # noqa: BLE001
        return []
    lines: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        bits = [f"dom-{row.get('tag') or 'input'}"]
        label = str(row.get("label") or "").strip()
        placeholder = str(row.get("placeholder") or "").strip()
        name = str(row.get("name") or "").strip()
        if label:
            bits.append(f"label={label}")
        if placeholder:
            bits.append(f"placeholder={placeholder}")
        if name:
            bits.append(f"name={name}")
        if len(bits) > 1:
            lines.append("  " + " ".join(bits))
    return lines


def take_snapshot(page: Any) -> str:
    try:
        tree = page.accessibility.snapshot()
        if not tree:
            aria_lines: list[str] = []
        else:
            aria_lines = _simplify_aria(tree)
        form_lines = _dom_form_hints(page)
        lines = list(aria_lines)
        if form_lines:
            lines.append("form-fields:")
            lines.extend(form_lines)
        if lines:
            return "\n".join(lines[:450])
        if tree:
            return json.dumps(tree, ensure_ascii=False)[:8000]
        return "(empty snapshot)"
    except Exception as ex:  # noqa: BLE001
        return f"(snapshot error: {ex})"


def _strip_required_star(name: str) -> str:
    return re.sub(r"^[\s*＊]+", "", name or "").strip()


def _hint_from_selector(selector: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not selector:
        return out
    s = selector.strip()
    role_m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
    if role_m:
        out["role"] = role_m.group(1)
        if role_m.group(2):
            name = role_m.group(2)
            if name.endswith("]"):
                name = name[:-1]
            out["name"] = _strip_required_star(name.strip("\"'"))
        return out
    if s.startswith("text="):
        out["text"] = _strip_required_star(s[5:].strip("\"'"))
        return out
    if s.startswith("label="):
        out["label"] = _strip_required_star(s[6:].strip("\"'"))
        return out
    if s.startswith("placeholder="):
        out["placeholder"] = s[len("placeholder=") :].strip("\"'")
        return out
    if s.startswith("testid=") or s.startswith("data-testid="):
        out["testid"] = s.split("=", 1)[1].strip("\"'")
        return out
    if s.startswith("[data-testid="):
        found = re.search(r'data-testid=["\']?([^"\'\]]+)', s)
        if found:
            out["testid"] = found.group(1)
            return out
    out["css"] = s
    return out


def _scope_root(page: Any) -> Any:
    """Prefer the topmost visible dialog so modal forms are not obscured by the page behind."""
    try:
        dialog = page.get_by_role("dialog")
        count = dialog.count()
        if count > 0:
            last = dialog.nth(count - 1)
            if last.is_visible():
                return last
    except Exception:  # noqa: BLE001
        pass
    return page


def _resolve_locator(root: Any, selector: str | None):
    """Delegate to ui_runner so getByRole(...) is never treated as CSS."""
    from app.services.ui_automation.ui_runner import resolve_page_locator

    if not selector:
        raise ValueError("缺少 selector")
    # Strip required-field star from role=/label= names before shared resolve
    s = selector.strip()
    if s.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and name.endswith("]"):
                name = name[:-1]
            kwargs = {"name": _strip_required_star(name.strip("\"'"))} if name else {}
            return root.get_by_role(role, **kwargs)
    if s.startswith("label="):
        return root.get_by_label(_strip_required_star(s[6:].strip("\"'")))
    return resolve_page_locator(root, s)


def _build_playwright_candidates(
    root: Any,
    *,
    action: str,
    selector: str | None,
    decision: dict[str, Any],
) -> list[tuple[str, Any]]:
    """Ordered Playwright locators: role → label → placeholder → testid → text(click only)."""
    hints = _hint_from_selector(selector)
    for key in ("role", "name", "placeholder", "locate"):
        raw = decision.get(key)
        if raw and key not in hints:
            hints[key] = _strip_required_star(str(raw)) if key in ("name", "locate") else str(raw)

    name = _strip_required_star(
        hints.get("name") or hints.get("label") or hints.get("text") or hints.get("locate") or ""
    )
    role = hints.get("role") or ("textbox" if action == "fill" else "button")
    blob = " ".join(
        [
            name,
            str(selector or ""),
            str(hints.get("locate") or ""),
            str(hints.get("placeholder") or ""),
            str(decision.get("prompt") or ""),
        ]
    )
    is_captcha_field = bool(_CAPTCHA_HINT.search(blob)) and "图" not in (hints.get("locate") or "")
    candidates: list[tuple[str, Any]] = []
    seen: set[str] = set()

    def add(dsl: str, loc: Any) -> None:
        if dsl in seen:
            return
        seen.add(dsl)
        candidates.append((dsl, loc))

    # Captcha: prefer LLM-provided name/placeholder from snapshot; never invent site defaults.
    if is_captcha_field and action in ("fill", "click") and name:
        add(
            f'role=textbox[name="{name}"]',
            root.get_by_role("textbox", name=re.compile(re.escape(name))),
        )
        add(f"label={name}", root.get_by_label(name))

    if name:
        add(f'role={role}[name="{name}"]', root.get_by_role(role, name=re.compile(re.escape(name))))
        if action == "fill" and role != "textbox":
            add(
                f'role=textbox[name="{name}"]',
                root.get_by_role("textbox", name=re.compile(re.escape(name))),
            )
        add(f"label={name}", root.get_by_label(name))

    placeholder = str(hints.get("placeholder") or "").strip()
    # Label text mis-recorded as placeholder (e.g. placeholder=验证码) — skip; use name/role instead.
    if placeholder and _CAPTCHA_HINT.search(placeholder) and len(placeholder) <= 8:
        placeholder = ""
    if placeholder:
        add(f"placeholder={placeholder}", root.get_by_placeholder(placeholder))

    testid = str(hints.get("testid") or "").strip()
    if testid:
        add(f"testid={testid}", root.get_by_test_id(testid))

    if selector and not (action == "fill" and selector.strip().startswith("text=")):
        sel = selector.strip()
        # Skip bogus captcha placeholder=验证码 (label mistaken for placeholder).
        if is_captcha_field and re.match(r'placeholder=["\']?(验证码|captcha)["\']?$', sel, re.I):
            sel = ""
        if sel:
            try:
                add(sel, _resolve_locator(root, sel))
            except Exception:  # noqa: BLE001
                pass

    if action == "click" and name:
        add(f"text={name}", root.get_by_text(name, exact=False))

    css = hints.get("css")
    if css and css not in seen:
        try:
            add(css, root.locator(css))
        except Exception:  # noqa: BLE001
            pass

    return candidates


def _is_retryable_action_error(ex: BaseException) -> bool:
    msg = str(ex).lower()
    needles = (
        "intercepts pointer events",
        "not visible",
        "not stable",
        "timeout",
        "covered",
        "outside of the viewport",
        "element is not attached",
        "waiting for",
    )
    return any(n in msg for n in needles)


def _midscene_prompt(*, action: str, selector: str | None, decision: dict[str, Any]) -> str:
    for key in ("prompt", "locate"):
        text = str(decision.get(key) or "").strip()
        if text:
            return text
    hints = _hint_from_selector(selector)
    name = (
        hints.get("name")
        or hints.get("label")
        or hints.get("text")
        or hints.get("placeholder")
        or (selector or "").strip()
        or "目标控件"
    )
    name = _strip_required_star(name)
    if action == "fill":
        return f"弹窗或表单中的「{name}」输入框"
    return f"「{name}」按钮或可点击控件"


def _try_playwright_click_or_fill(
    *,
    page: Any,
    action: str,
    selector: str | None,
    value: Any,
    decision: dict[str, Any],
    timeout_ms: int,
) -> dict[str, Any]:
    root = _scope_root(page)
    candidates = _build_playwright_candidates(root, action=action, selector=selector, decision=decision)
    if not candidates:
        raise ValueError("缺少 selector")
    attempt_ms = min(_PW_ATTEMPT_TIMEOUT_MS, max(800, int(timeout_ms)))
    errors: list[str] = []
    last_error: BaseException | None = None
    strategies_tried = 0

    def _run(loc: Any) -> None:
        target = loc.first
        target.wait_for(state="visible", timeout=attempt_ms)
        if action == "click":
            target.click(timeout=attempt_ms)
        else:
            target.fill(str(value or ""), timeout=attempt_ms)

    for dsl, loc in candidates:
        if strategies_tried >= _PW_MAX_ATTEMPTS:
            break
        strategies_tried += 1
        try:
            _run(loc)
            return {"selector": dsl, "driver": "playwright", "attempts": strategies_tried}
        except Exception as ex:  # noqa: BLE001
            last_error = ex
            errors.append(f"{dsl}: {ex}")
            if _is_retryable_action_error(ex):
                try:
                    page.wait_for_timeout(300)
                    _run(loc)
                    return {"selector": dsl, "driver": "playwright", "attempts": strategies_tried}
                except Exception as ex2:  # noqa: BLE001
                    last_error = ex2
                    errors.append(f"{dsl}(retry): {ex2}")
            continue

    msg = "; ".join(errors[-4:]) if errors else "定位失败"
    raise RuntimeError(f"{_MIDSCENE_FAIL_REASON}: {msg}") from last_error


def _fallback_midscene_step(
    *,
    page: Any,
    action: str,
    selector: str | None,
    value: Any,
    decision: dict[str, Any],
    cdp_endpoint: str | None,
    model_config: dict[str, str] | None,
    timeout_ms: int,
) -> dict[str, Any]:
    prompt = _midscene_prompt(action=action, selector=selector, decision=decision)
    if not cdp_endpoint:
        raise RuntimeError(f"{_MIDSCENE_FAIL_REASON}; 无 CDP，无法同页 Midscene 降级（{prompt}）")
    from app.services.ui_automation.node_ui_runner import midscene_step_over_cdp

    midscene_step_over_cdp(
        cdp_endpoint=cdp_endpoint,
        action=action,
        prompt=prompt,
        value="" if value is None else str(value),
        model_config=model_config,
        timeout_ms=timeout_ms,
    )
    kind = "ai_input" if action == "fill" else "ai_tap"
    return {
        "driver": "midscene",
        "reason": _MIDSCENE_FAIL_REASON,
        "prompt": prompt,
        "locate": prompt,
        "kind": kind,
        "selector": selector,
    }


def apply_browser_action(
    *,
    page: Any,
    base_url: str,
    decision: dict[str, Any],
    ctx: dict[str, str],
    timeout_ms: int = 15000,
    cdp_endpoint: str | None = None,
    model_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    from app.services.ui_automation.ui_runner import render_template

    action = decision["action"]
    sel = decision.get("selector")
    val = decision.get("value")
    asrt = decision.get("assert")
    if isinstance(val, str) and "{{" in val:
        val = render_template(val, ctx)
    if isinstance(sel, str) and "{{" in sel:
        sel = render_template(sel, ctx)
    if isinstance(asrt, str) and "{{" in asrt:
        asrt = render_template(asrt, ctx)

    detail: dict[str, Any] = {"action": action, "selector": sel, "value": val, "assert": asrt}
    if action == "goto":
        path = str(val or "/").strip() or "/"
        url = join_base_and_path(base_url, path)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        assert_same_host(base_url, page.url)
        detail["url"] = url
    elif action in ("click", "fill"):
        explicit = str(decision.get("driver") or "").strip().lower()
        if explicit == "midscene":
            mid = _fallback_midscene_step(
                page=page,
                action=action,
                selector=sel,
                value=val,
                decision=decision,
                cdp_endpoint=cdp_endpoint,
                model_config=model_config,
                timeout_ms=timeout_ms,
            )
            detail.update(mid)
        else:
            try:
                hit = _try_playwright_click_or_fill(
                    page=page,
                    action=action,
                    selector=sel,
                    value=val,
                    decision=decision,
                    timeout_ms=timeout_ms,
                )
                detail.update(hit)
            except Exception as pw_ex:  # noqa: BLE001
                log.info("playwright %s failed after retries: %s", action, pw_ex)
                mid = _fallback_midscene_step(
                    page=page,
                    action=action,
                    selector=sel,
                    value=val,
                    decision=decision,
                    cdp_endpoint=cdp_endpoint,
                    model_config=model_config,
                    timeout_ms=timeout_ms,
                )
                detail.update(mid)
                detail["playwright_error"] = str(pw_ex)[:500]
    elif action == "select_option":
        loc = _resolve_locator(_scope_root(page), sel)
        loc.first.select_option(str(val or ""), timeout=timeout_ms)
    elif action == "press":
        loc = _resolve_locator(_scope_root(page), sel)
        loc.first.press(str(val or "Enter"), timeout=timeout_ms)
    elif action == "assert_visible":
        loc = _resolve_locator(_scope_root(page), sel)
        loc.first.wait_for(state="visible", timeout=timeout_ms)
    elif action == "assert_text":
        expected = str(asrt if asrt is not None else val or "")
        text = _resolve_locator(_scope_root(page), sel).first.inner_text(timeout=timeout_ms)
        if expected not in text:
            raise AssertionError(f"文案不包含: {expected!r} actual={text!r}")
        detail["actual_text"] = text
    elif action == "assert_url":
        expected = str(asrt if asrt is not None else val or "")
        cur = page.url
        assert_same_host(base_url, cur)
        if expected not in cur:
            raise AssertionError(f"URL 不包含: {expected!r}")
        detail["url"] = cur
    elif action == "wait_for":
        state = str(asrt or val or "visible")
        if state in ("networkidle", "load", "domcontentloaded"):
            page.wait_for_load_state(state, timeout=timeout_ms)
        elif state == "url":
            page.wait_for_url(f"**{val or ''}**", timeout=timeout_ms)
        elif state == "hidden":
            _resolve_locator(_scope_root(page), sel).first.wait_for(state="hidden", timeout=timeout_ms)
        else:
            _resolve_locator(_scope_root(page), sel).first.wait_for(state="visible", timeout=timeout_ms)
    elif action == "done":
        pass
    else:
        raise ValueError(f"未知动作: {action}")
    detail["page_url"] = getattr(page, "url", "")
    return detail


def selector_to_dsl(selector: str | None) -> str | None:
    if not selector:
        return None
    s = selector.strip()
    if s.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and name.endswith("]"):
                name = name[:-1]
            name = (name or "").strip("\"'")
            if name:
                return f'role={role}[name="{name}"]'
            return f"role={role}"
    if s.startswith("text="):
        t = s[5:].strip("\"'")
        return f'text={t}'
    if s.startswith("testid="):
        return f'[data-testid="{s[7:].strip(chr(34)+chr(39))}"]'
    if s.startswith("data-testid="):
        return f'[data-testid="{s.split("=",1)[1].strip(chr(34)+chr(39))}"]'
    return s


def trace_to_ui_steps(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in trace:
        action = item.get("action")
        if action in (None, "done"):
            continue
        if item.get("status") not in (None, "ok", "passed"):
            # keep successful explore steps only for final case
            if item.get("status") == "error":
                continue
        step: dict[str, Any] = {"action": action}
        sel = selector_to_dsl(item.get("selector"))
        if sel:
            step["selector"] = sel
        if item.get("value") is not None and action in ("goto", "fill", "select_option", "press", "assert_url", "assert_text"):
            step["value"] = str(item.get("value"))
        if item.get("assert") is not None and action in ("wait_for", "assert_text", "assert_url"):
            step["assert"] = str(item.get("assert"))
        for key in ("driver", "reason", "prompt", "locate", "placeholder", "role", "name"):
            if item.get(key):
                step[key] = item.get(key)
        # normalize role= selectors: our runner uses CSS; map role/text to getBy-friendly CSS-ish
        # For UiStep DSL runner which uses page.locator(), convert role/text to playwright-compatible
        if sel and (sel.startswith("role=") or sel.startswith("text=")):
            # keep as selector string; runner will use locator() — convert to CSS-ish for fill/click
            step["selector"] = _locator_string_for_dsl(sel)
        steps.append(step)
    return steps


def _locator_string_for_dsl(sel: str) -> str:
    """Map explore locator shorthand to something page.locator can use, or keep getBy-style as CSS."""
    s = sel.strip()
    if s.startswith("text="):
        t = s[5:].strip("\"'")
        return f"text={t}"
    if s.startswith("role="):
        m = re.match(r'role=([^\s\[]+)(?:\[name="?([^\]"]+)"?\])?', s)
        if m:
            role, name = m.group(1), m.group(2)
            if name:
                # Playwright CSS engine supports role= with name
                return f'role={role}[name="{name}"]'
            return f"role={role}"
    return s


_TPL = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_ELEMENT_REF = re.compile(r"^@([A-Za-z0-9_.\-]+)$")

DEFAULT_UI_SCRIPT = """def run(page):
    page.goto("/")
    expect(page.locator("body")).to_be_visible()
"""


def _py_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _py_value(value: str | None) -> str:
    text = "" if value is None else str(value)
    if not _TPL.search(text):
        return _py_literal(text)
    parts: list[str] = []
    last = 0
    for match in _TPL.finditer(text):
        if match.start() > last:
            parts.append(_py_literal(text[last : match.start()]))
        parts.append(f"env[{_py_literal(match.group(1))}]")
        last = match.end()
    if last < len(text):
        parts.append(_py_literal(text[last:]))
    return " + ".join(parts) if parts else '""'


def _lookup_element(key: str, elements: dict[str, str]) -> str | None:
    if key in elements:
        return elements[key]
    lowered = {k.lower(): v for k, v in elements.items()}
    return lowered.get(key.lower())


def _selector_for_script(step: dict[str, Any], elements: dict[str, str]) -> str | None:
    raw_el = str(step.get("element") or "").strip()
    raw_sel = str(step.get("selector") or "").strip()
    key = raw_el
    if not key and raw_sel:
        matched = _ELEMENT_REF.match(raw_sel)
        if matched:
            key = matched.group(1)
    if key:
        found = _lookup_element(key, elements)
        if found:
            return found
    if raw_sel and not raw_sel.startswith("@"):
        return raw_sel
    return None


def _prefixed_text(selector: str, prefix: str) -> str:
    return selector[len(prefix) :].strip().strip("\"'")


_PY_GETBY = re.compile(
    r"^(getByRole|getByPlaceholder|getByText|getByLabel|getByTestId|get_by_role|get_by_placeholder|"
    r"get_by_text|get_by_label|get_by_test_id)\s*\(",
    re.I,
)


def _py_locator(selector: str | None) -> str:
    if not selector:
        return ""
    s = selector.strip().rstrip(";").strip()
    from app.services.ui_automation.playwright_cli_session import is_snapshot_ref_locator

    if is_snapshot_ref_locator(s):
        return ""
    # Already a Playwright locator call — do not wrap in page.locator("getBy...")
    if s.startswith("page."):
        return s
    if _PY_GETBY.match(s):
        # JS-style getByRole(...) from explore → prefix page.
        if s.startswith("getBy") or s.startswith("get_by"):
            # Prefer Python API when given JS getByRole
            m = re.match(
                r"getByRole\(\s*['\"](\w+)['\"]\s*,\s*\{\s*name:\s*['\"]([^'\"]*)['\"]",
                s,
                re.I,
            )
            if m:
                return (
                    f"page.get_by_role({_py_literal(m.group(1))}, "
                    f"name={_py_literal(m.group(2))}, exact=True)"
                )
            m2 = re.match(r"getByPlaceholder\(\s*['\"]([^'\"]*)['\"]", s, re.I)
            if m2:
                return f"page.get_by_placeholder({_py_literal(m2.group(1))}, exact=True)"
            m3 = re.match(r"getByText\(\s*['\"]([^'\"]*)['\"]", s, re.I)
            if m3:
                return f"page.get_by_text({_py_literal(m3.group(1))})"
            m4 = re.match(r"getByLabel\(\s*['\"]([^'\"]*)['\"]", s, re.I)
            if m4:
                return f"page.get_by_label({_py_literal(m4.group(1))}, exact=True)"
            m5 = re.match(r"getByTestId\(\s*['\"]([^'\"]*)['\"]", s, re.I)
            if m5:
                return f"page.get_by_test_id({_py_literal(m5.group(1))})"
            if s.startswith("get_by_"):
                return f"page.{s}"
        return f"page.{s}" if not s.startswith("page.") else s
    role = re.match(r'role=([^\s\[]+)(?:\[name="?([^\]"]+)"?\])?', s)
    if s.startswith("role=") and role:
        name = role.group(2)
        if name:
            return (
                f"page.get_by_role({_py_literal(role.group(1))}, name={_py_literal(name)}, exact=True)"
            )
        return f"page.get_by_role({_py_literal(role.group(1))})"
    if s.startswith("text="):
        return f"page.get_by_text({_py_literal(s[5:].strip(chr(34) + chr(39)))})"
    if s.startswith("testid=") or s.startswith("data-testid="):
        tid = s.split("=", 1)[1].strip("\"'")
        return f"page.get_by_test_id({_py_literal(tid)})"
    if s.startswith("[data-testid="):
        found = re.search(r'data-testid=["\']?([^"\'\]]+)', s)
        if found:
            return f"page.get_by_test_id({_py_literal(found.group(1))})"
    if s.startswith("label="):
        return f"page.get_by_label({_py_literal(_prefixed_text(s, 'label='))}, exact=True)"
    if s.startswith("placeholder="):
        return f"page.get_by_placeholder({_py_literal(_prefixed_text(s, 'placeholder='))}, exact=True)"
    if is_snapshot_ref_locator(s):
        return ""
    return f"page.locator({_py_literal(s)})"


def _step_statement(step: dict[str, Any], elements: dict[str, str]) -> str | None:
    action = str(step.get("action") or "").strip()
    if action in ("", "done") or step.get("status") == "error":
        return None
    if str(step.get("driver") or "").lower() == "midscene" or action in ("ai_tap", "ai_input"):
        # Python scripts cannot call Midscene; mixed traces use Node via trace_to_explore_script.
        return None
    loc = _py_locator(_selector_for_script(step, elements))
    val = step.get("value")
    asrt = step.get("assert")
    if action == "goto":
        return f"page.goto({_py_value(str(val or '/'))})"
    if not loc:
        return None
    if action == "click":
        return f"{loc}.click()"
    if action == "fill":
        return f"{loc}.fill({_py_value(None if val is None else str(val))})"
    if action == "select_option":
        return f"{loc}.select_option({_py_value(None if val is None else str(val))})"
    if action == "press":
        return f"{loc}.press({_py_value(str(val or 'Enter'))})"
    if action == "hover":
        return f"{loc}.hover()"
    if action == "assert_visible":
        return f"expect({loc}).to_be_visible()"
    if action == "assert_text":
        expected = asrt if asrt is not None else val
        return f"expect({loc}).to_contain_text({_py_value(None if expected is None else str(expected))})"
    if action == "assert_url":
        expected = asrt if asrt is not None else val
        text = "" if expected is None else str(expected)
        if _TPL.search(text):
            return f"expect(page).to_have_url(re.compile(re.escape({_py_value(text)})))"
        return f"expect(page).to_have_url(re.compile({_py_literal(re.escape(text))}))"
    if action == "wait_for":
        state = str(asrt or val or "visible")
        if state in ("networkidle", "load", "domcontentloaded"):
            return f"page.wait_for_load_state({_py_literal(state)})"
        if state == "url":
            return f"page.wait_for_url({_py_value(None if val is None else str(val))})"
        if state == "hidden":
            return f'{loc}.wait_for(state="hidden")'
        return f'{loc}.wait_for(state="visible")'
    if action == "screenshot":
        return "page.screenshot()"
    return None


def steps_to_script(steps: list[dict[str, Any]], *, elements: dict[str, str] | None = None) -> str:
    """Compile step DSL into a Python Playwright ``def run(page)`` script. Empty input yields ""."""
    el_map = elements or {}
    body: list[str] = []
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        stmt = _step_statement(raw, el_map)
        if stmt:
            body.append(f"    {stmt}")
    if not body:
        return ""
    return "def run(page):\n" + "\n".join(body) + "\n"


def _trace_has_midscene(trace: list[dict[str, Any]]) -> bool:
    for item in trace:
        if not isinstance(item, dict):
            continue
        if str(item.get("driver") or "").lower() == "midscene":
            return True
        if str(item.get("kind") or "").lower() in ("ai_tap", "ai_input", "ai_act"):
            return True
        if str(item.get("action") or "").lower() in ("ai_tap", "ai_input"):
            return True
    return False


def _trace_to_node_actions(trace: list[dict[str, Any]], *, start_path: str = "/") -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in trace:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "error":
            continue
        action = str(item.get("action") or "").strip()
        if action in ("", "done"):
            continue
        driver = str(item.get("driver") or "").lower()
        kind = str(item.get("kind") or "").lower()
        if action == "goto":
            actions.append({"kind": "goto", "path": str(item.get("value") or start_path or "/")})
            continue
        if driver == "midscene" or kind in ("ai_tap", "ai_input") or action in ("ai_tap", "ai_input"):
            locate = str(item.get("locate") or item.get("prompt") or item.get("selector") or "")
            if action == "fill" or kind == "ai_input" or action == "ai_input":
                actions.append(
                    {
                        "kind": "fill",
                        "locate": locate or "输入框",
                        "value": "" if item.get("value") is None else str(item.get("value")),
                        "driver": "midscene",
                    }
                )
            else:
                actions.append({"kind": "click", "locate": locate or "按钮", "driver": "midscene"})
            continue
        if action == "fill":
            row: dict[str, Any] = {
                "kind": "fill",
                "selector": item.get("selector") or "",
                "value": "" if item.get("value") is None else str(item.get("value")),
            }
            for key in ("placeholder", "role", "name", "locate"):
                if item.get(key):
                    row[key] = item.get(key)
            actions.append(row)
        elif action == "click":
            row = {"kind": "click", "selector": item.get("selector") or ""}
            for key in ("role", "name", "locate", "placeholder"):
                if item.get(key):
                    row[key] = item.get(key)
            actions.append(row)
        elif action == "press":
            actions.append(
                {
                    "kind": "press",
                    "locate": item.get("locate") or item.get("selector") or "",
                    "value": item.get("value") or "Enter",
                }
            )
    return actions


def trace_to_playwright_code(
    *,
    title: str,
    base_url: str,
    trace: list[dict[str, Any]],
) -> str:
    del title, base_url
    kept: list[dict[str, Any]] = []
    for item in trace:
        action = item.get("action")
        if action in (None, "done") or item.get("status") == "error":
            continue
        kept.append(item)
    return steps_to_script(kept)


def trace_to_explore_script(
    *,
    title: str,
    base_url: str,
    trace: list[dict[str, Any]],
    start_path: str = "/",
    username: str = "",
    password: str = "",
) -> str:
    """Python Playwright when all steps are stable; Node + Midscene when any step fell back."""
    del title, base_url
    if _trace_has_midscene(trace):
        from app.services.ui_automation.ui_node_script import actions_to_node_script

        actions = _trace_to_node_actions(trace, start_path=start_path)
        return actions_to_node_script(
            actions,
            username=username,
            password=password,
            start_path=start_path or "/",
            ensure_submit=False,
        )
    return trace_to_playwright_code(title="", base_url="", trace=trace)


def run_explore_loop(
    *,
    page: Any,
    base_url: str,
    plan: dict[str, Any],
    ctx: dict[str, str],
    llm_cfg: LlmRuntime,
    model: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_ms: int = 15000,
    should_cancel: Callable[[], bool] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    cdp_endpoint: str | None = None,
    model_config: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    # initial goto
    start = plan.get("start_path") or "/"
    trace: list[dict[str, Any]] = []
    consecutive_errors = 0

    def _note(item: dict[str, Any]) -> None:
        if on_step:
            try:
                on_step(item)
            except Exception:  # noqa: BLE001
                pass

    try:
        url = join_base_and_path(base_url, start)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        assert_same_host(base_url, page.url)
        item = {
            "action": "goto",
            "value": start,
            "status": "ok",
            "page_url": page.url,
            "reason": "start_path",
        }
        trace.append(item)
        _note(item)
    except Exception as ex:  # noqa: BLE001
        item = {"action": "goto", "value": start, "status": "error", "error": str(ex)}
        trace.append(item)
        _note(item)
        return trace

    for _ in range(max_steps):
        if should_cancel and should_cancel():
            break
        snap = take_snapshot(page)
        try:
            decision = decide_next_action(
                plan=plan,
                snapshot=snap,
                url=page.url,
                trace=trace,
                ctx_keys=nonempty_ctx_keys(ctx),
                llm_cfg=llm_cfg,
                model=model,
            )
        except Exception as ex:  # noqa: BLE001
            item = {"action": "done", "status": "error", "error": f"LLM 决策失败: {ex}"}
            trace.append(item)
            _note(item)
            break
        if decision["action"] == "done":
            item = {**decision, "status": "ok", "page_url": page.url}
            trace.append(item)
            _note(item)
            break
        try:
            detail = apply_browser_action(
                page=page,
                base_url=base_url,
                decision=decision,
                ctx=ctx,
                timeout_ms=timeout_ms,
                cdp_endpoint=cdp_endpoint,
                model_config=model_config,
            )
            item = {**decision, **detail, "status": "ok"}
            consecutive_errors = 0
            trace.append(item)
            _note(item)
        except UnsafeUiUrlError as ex:
            item = {
                **decision,
                "status": "error",
                "error": str(ex),
                "page_url": getattr(page, "url", ""),
            }
            trace.append(item)
            _note(item)
            break
        except Exception as ex:  # noqa: BLE001
            item = {
                **decision,
                "status": "error",
                "error": str(ex),
                "page_url": getattr(page, "url", ""),
            }
            consecutive_errors += 1
            trace.append(item)
            _note(item)
            # Keep exploring after click/fill misses; stop on repeated hard failures.
            if consecutive_errors >= 3:
                break
    return trace


def execute_explore_job(
    db: Session,
    job_id: int,
    *,
    llm_cfg: LlmRuntime | None = None,
    browser_factory: Callable[[], Any] | None = None,
) -> None:
    job = db.get(UiAiExploreJob, job_id)
    if job is None:
        log.warning("ui ai explore job %s not found", job_id)
        return
    if job.status == "cancelled":
        return
    job.status = "running"
    job.error_message = ""
    db.commit()

    tc = (
        db.query(TestCase)
        .options(joinedload(TestCase.steps))
        .filter(TestCase.id == job.testcase_id, TestCase.project_id == job.project_id)
        .first()
    )
    if tc is None:
        job.status = "failed"
        job.error_message = TEST_CASE_NOT_FOUND
        db.commit()
        return

    env = _load_env(db, job.project_id, job.env_id)
    if env is None:
        job.status = "failed"
        job.error_message = UI_ENV_NOT_FOUND
        db.commit()
        return
    base_url = (env.base_url or "").strip()
    if not base_url:
        job.status = "failed"
        job.error_message = UI_ENV_BASE_URL_REQUIRED
        db.commit()
        return

    ctx = _build_ctx(env, job.account_name)
    cfg = llm_cfg or get_llm_runtime(db)
    manual_steps = [{"action": s.action, "expected": s.expected} for s in (tc.steps or [])]
    max_steps = DEFAULT_MAX_STEPS
    if isinstance(job.plan_json, dict) and isinstance(job.plan_json.get("_max_steps"), int):
        max_steps = max(1, min(50, int(job.plan_json["_max_steps"])))

    def _cancelled() -> bool:
        db.refresh(job)
        return job.status == "cancelled"

    try:
        plan = build_plan_from_testcase(
            title=tc.title,
            steps=manual_steps,
            base_url=base_url,
            llm_cfg=cfg,
        )
        plan["_max_steps"] = max_steps
        job.plan_json = plan
        db.commit()

        timeout_ms = int(getattr(env, "default_timeout_ms", None) or 15000)
        browser_name = job.browser or env.browser or "chromium"

        cdp_endpoint: str | None = None
        mid_cfg: dict[str, str] | None = None
        try:
            from app.services.ai.effective_ai_settings import get_midscene_runtime

            mid_cfg = get_midscene_runtime(db).as_model_config()
        except Exception:  # noqa: BLE001
            mid_cfg = None

        if browser_factory is not None:
            page = browser_factory()
            trace = run_explore_loop(
                page=page,
                base_url=base_url,
                plan=plan,
                ctx=ctx,
                llm_cfg=cfg,
                max_steps=max_steps,
                timeout_ms=timeout_ms,
                should_cancel=_cancelled,
                cdp_endpoint=None,
                model_config=mid_cfg,
            )
        else:
            from playwright.sync_api import sync_playwright

            from app.services.ui_automation.node_ui_runner import (
                cdp_endpoint_for_port,
                chromium_launch_args,
                free_cdp_port,
            )

            with sync_playwright() as p:
                use_name = browser_name if browser_name in ("chromium", "firefox", "webkit") else "chromium"
                launcher = getattr(p, use_name)
                launch_kwargs: dict[str, Any] = {"headless": True}
                if use_name == "chromium":
                    cdp_port = free_cdp_port()
                    launch_kwargs["args"] = chromium_launch_args(cdp_port=cdp_port)
                    cdp_endpoint = cdp_endpoint_for_port(cdp_port)
                browser = launcher.launch(**launch_kwargs)
                try:
                    vp = env.viewport_json if isinstance(env.viewport_json, dict) else {"width": 1280, "height": 720}
                    page = browser.new_page(
                        viewport={"width": int(vp.get("width") or 1280), "height": int(vp.get("height") or 720)}
                    )
                    page.set_default_timeout(timeout_ms)
                    trace = run_explore_loop(
                        page=page,
                        base_url=base_url,
                        plan=plan,
                        ctx=ctx,
                        llm_cfg=cfg,
                        max_steps=max_steps,
                        timeout_ms=timeout_ms,
                        should_cancel=_cancelled,
                        cdp_endpoint=cdp_endpoint,
                        model_config=mid_cfg,
                    )
                finally:
                    browser.close()

        if _cancelled():
            job.status = "cancelled"
            job.trace_json = trace
            db.commit()
            return

        job.trace_json = trace
        steps = trace_to_ui_steps(trace)
        code = trace_to_explore_script(
            title=tc.title,
            base_url=base_url,
            trace=trace,
            start_path=str(plan.get("start_path") or "/"),
            username=str(ctx.get("username") or ""),
            password=str(ctx.get("password") or ""),
        )
        job.result_steps_json = steps
        job.playwright_code = code
        if not (code or "").strip():
            job.status = "failed"
            job.error_message = "探索未产生可执行脚本"
        else:
            job.status = "succeeded"
        db.commit()
    except AppError as ex:
        job.status = "failed"
        job.error_message = ex.message
        db.commit()
    except Exception as ex:  # noqa: BLE001
        log.exception("ui ai explore job failed id=%s", job_id)
        job.status = "failed"
        job.error_message = str(ex)
        db.commit()
