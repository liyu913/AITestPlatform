"""Playwright-cli explore loop: snapshot → LLM decide → click/fill by ref → report → codegen."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.ui_automation.playwright_cli_session import (
    PlaywrightCliSession,
    is_element_ref,
    is_snapshot_ref_locator,
    join_explore_url,
    normalize_ref,
)
from app.services.ui_automation.ui_node_script import (
    _CAPTCHA_QUERY_PROMPT,
    actions_to_node_script,
)
from app.services.ui_automation.ui_runner import render_template
from app.user_messages import CAPTCHA_INVALID

log = logging.getLogger(__name__)

_CAPTCHA = re.compile(r"验证码|captcha|verify[\s_-]?code|vcode", re.I)
_DEFAULT_MAX_STEPS = 25
REPEATED_MIDSCENE_NOOP = "repeated_midscene_noop"
CAPTCHA_READ_FINGERPRINT = "midscene::captcha-read"
CAPTCHA_NEED_HUMAN = "captcha_need_human"
CAPTCHA_MAX_AUTO_RETRIES = 3
PROBLEM_TEXT_NOT_PROGRESS = "problem_text_not_progress"
PROBLEM_REACT_NUDGE = (
    "页面已出现问题/校验提示。不要 assert 该提示。"
    "请根据提示与空控件选择纠偏下一步（常见：fill 空必填；否则换控件/关错态后继续），再推进 goal。"
)
_CAPTCHA_REFRESH = re.compile(r"换一张|刷新验证码|刷新|reload.*captcha|refresh.*captcha", re.I)
_CAPTCHA_ERROR = re.compile(
    r"(?:验证码|captcha|verify[\s_-]?code).{0,24}(?:错误|失败|过期|无效|invalid|incorrect|expired|wrong)|"
    r"(?:invalid|incorrect|expired|wrong).{0,24}(?:captcha|verify[\s_-]?code|code)",
    re.I,
)
# Captcha input placeholders look like 「请输入…」but are not form-validation problems.
_CAPTCHA_PLACEHOLDER = re.compile(
    r"请输入图中|图中字符|enter\s*(the\s*)?(code|characters)|verification\s*code",
    re.I,
)
_PROBLEM_HINT = re.compile(
    r"请输入|必填|不能为空|请选择|格式不正确|操作失败|提交失败|"
    r"required|is required|cannot be empty|must (?:not be empty|be filled)",
    re.I,
)
_SNAP_TEXTBOX_REF = re.compile(
    r"(?:textbox|input)[^\n\[]{0,80}\[ref=(e\d+)\]|"
    r"\[ref=(e\d+)\][^\n]{0,40}(?:textbox|验证码|captcha|请输入)",
    re.I,
)
_SNAP_NAMED = re.compile(
    r'-\s+(\w+)\s+"([^"]+)"[^\n]*\[ref=(e\d+)\]',
    re.I,
)


_USER_FIELD = re.compile(r"账号|用户名|username|user\s*name", re.I)
_PASS_FIELD = re.compile(r"密码|password|passwd", re.I)
_DIALOG_LINE = re.compile(r'^\s*-\s+dialog\b', re.I)
_FIELD_ROLES = frozenset({"textbox", "input", "searchbox", "combobox"})


def _decision_blob(decision: dict[str, Any]) -> str:
    return " ".join(
        str(decision.get(k) or "")
        for k in ("selector", "placeholder", "name", "locate", "prompt", "reason")
    )


def _is_captcha_decision(decision: dict[str, Any]) -> bool:
    return bool(_CAPTCHA.search(_decision_blob(decision)))


def fill_field_kind(decision: dict[str, Any]) -> str:
    """username | password | captcha | empty. Password checked before username."""
    blob = _decision_blob(decision)
    if _CAPTCHA.search(blob):
        return "captcha"
    if _PASS_FIELD.search(blob):
        return "password"
    if _USER_FIELD.search(blob):
        return "username"
    return ""


def is_plausible_credential_value(value: str, *, kind: str = "") -> bool:
    """Reject outcome copy / step indexes that must not be typed into auth fields."""
    text = str(value or "").strip().strip("\"'")
    if not text or len(text) > 64:
        return False
    if re.match(
        r"^(?:登录成功|登录失败|成功|失败|通过|离开|正确|错误|无效|"
        r"密码|账号|用户名|username|password|passwd|pwd|\d+\.?)$",
        text,
        re.I,
    ):
        return False
    if kind == "username" and re.match(r"^密码", text):
        return False
    # Template vars are OK (resolved later)
    if text.startswith("{{") and text.endswith("}}"):
        return True
    return True


def bind_fill_value_from_ctx(decision: dict[str, Any], ctx: dict[str, str] | None) -> dict[str, Any]:
    """No-op for auth overrides — Agent owns fill values (templates resolved at act time).

    Kept as a pass-through so call sites stay stable. Do not reintroduce regex
    username/password forcing here; that fights ReAct decide.
    """
    return decision


def format_snapshot_action_candidates(snapshot: str, *, limit: int = 24) -> str:
    """Enumerate textboxes/buttons from snapshot for decide nudges (no policy pick)."""
    controls = _snapshot_controls(snapshot)
    lines: list[str] = []
    for c in controls:
        if c.get("kind") != "control":
            continue
        role = str(c.get("role") or "")
        if role not in ("textbox", "input", "searchbox", "combobox", "button"):
            continue
        name = str(c.get("name") or "").strip()
        ref = str(c.get("ref") or "").strip()
        if not ref:
            continue
        label = f'{role} "{name}"' if name else role
        lines.append(f"- {label} [ref={ref}]")
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    return "候选控件（必须从中选 selector/ref 行动，勿编造）:\n" + "\n".join(lines)


def _clear_then_fill(cli: PlaywrightCliSession, target: str, text: str) -> None:
    """Clear input then fill so corrections overwrite prior wrong values."""
    try:
        cli.fill(target, "")
    except Exception:  # noqa: BLE001
        pass
    cli.fill(target, text)


_SECRET_FIELD = re.compile(r"密码|password|passwd|pwd|验证码|captcha", re.I)


def mask_explore_value(decision: dict[str, Any], ctx: dict[str, str] | None, value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    pwd = str((ctx or {}).get("password") or "")
    if pwd and text == pwd:
        return "***"
    blob = " ".join(
        str(decision.get(k) or "")
        for k in ("selector", "placeholder", "name", "locate", "prompt", "reason")
    )
    if _is_captcha_decision(decision) or _SECRET_FIELD.search(blob):
        return "***"
    if len(text) > 40:
        return text[:40] + "…"
    return text


def format_cli_action_log(
    action: str,
    decision: dict[str, Any],
    *,
    locator: str = "",
    ctx: dict[str, str] | None = None,
) -> str:
    """One-line explore log: case, action, locator/ref, masked value, reason."""
    case_id = str(decision.get("case_id") or "").strip()
    sel = str(decision.get("selector") or "").strip()
    loc = (locator or str(decision.get("cli_locator") or "")).strip()
    reason = str(decision.get("reason") or "").strip()
    driver = str(decision.get("driver") or "playwright").strip().lower() or "playwright"
    parts: list[str] = []
    if case_id:
        parts.append(f"用例 {case_id}")
    parts.append(action)
    if loc:
        parts.append(loc)
    elif sel:
        parts.append(f"sel={sel}")
    else:
        hint = str(
            decision.get("placeholder") or decision.get("name") or decision.get("locate") or ""
        ).strip()
        if hint:
            parts.append(hint)
    if action in ("fill", "goto", "press"):
        shown = mask_explore_value(decision, ctx, str(decision.get("value") or ""))
        if shown:
            parts.append(f"value={shown}")
    if driver and driver != "playwright":
        parts.append(f"driver={driver}")
    if reason:
        parts.append(f"reason={reason[:80]}")
    return " · ".join(parts)


def midscene_noop_fingerprint(decision: dict[str, Any]) -> str:
    """Stable key for midscene explore steps. Captcha reads share one bucket."""
    driver = str(decision.get("driver") or "").strip().lower()
    if driver != "midscene":
        return ""
    prompt = str(
        decision.get("prompt")
        or decision.get("locate")
        or decision.get("selector")
        or "目标控件"
    ).strip()
    if _is_captcha_decision(decision) or _CAPTCHA.search(prompt):
        return CAPTCHA_READ_FINGERPRINT
    action = str(decision.get("action") or "click").strip() or "click"
    return f"midscene::{action}::{prompt}"


def _midscene_cdp_action(decision: dict[str, Any]) -> str:
    """Map explore decision → step.mjs action (query|fill|tap)."""
    action = str(decision.get("action") or "").strip().lower()
    if action in ("fill", "input", "type", "ai_input"):
        return "fill"
    if action in ("ai_query", "query", "aiquery"):
        return "query"
    return "tap"


def trace_has_midscene_fingerprint(trace: list[dict[str, Any]], fingerprint: str) -> bool:
    if not fingerprint:
        return False
    for item in trace or []:
        if not isinstance(item, dict):
            continue
        if fingerprint == CAPTCHA_READ_FINGERPRINT and (
            item.get("captcha")
            or item.get("kind") == "fill_captcha"
            or item.get("explore_captcha_read")
            or midscene_noop_fingerprint(item) == CAPTCHA_READ_FINGERPRINT
        ):
            return True
        if str(item.get("driver") or "").lower() != "midscene":
            continue
        if midscene_noop_fingerprint(item) == fingerprint:
            return True
    return False


def snapshot_has_captcha_error(snapshot: str) -> bool:
    """True when snapshot looks like a captcha validation failure (any app copy)."""
    text = str(snapshot or "")
    if not text:
        return False
    if CAPTCHA_INVALID in text:
        return True
    return bool(_CAPTCHA_ERROR.search(text))


def _line_is_captcha_placeholder(line: str) -> bool:
    """Skip captcha input copy so 「请输入图中字符」is not treated as form validation."""
    if _CAPTCHA_PLACEHOLDER.search(line):
        return True
    if _CAPTCHA.search(line) and re.search(r"请输入|textbox|input|placeholder", line, re.I):
        return True
    return False


def snapshot_has_problem_hint(snapshot: str) -> bool:
    """True when snapshot shows form validation / error copy (not captcha placeholders)."""
    text = str(snapshot or "")
    if not text:
        return False
    for line in text.splitlines():
        if _line_is_captcha_placeholder(line):
            continue
        if _PROBLEM_HINT.search(line):
            return True
    return False


def assert_targets_problem_text(decision: dict[str, Any]) -> bool:
    """True when assert value/text is problem/validation copy, not a success criterion."""
    blob = " ".join(
        str(decision.get(k) or "") for k in ("value", "assert", "reason", "name", "selector")
    )
    if not blob.strip():
        return False
    if _CAPTCHA_PLACEHOLDER.search(blob):
        return False
    return bool(_PROBLEM_HINT.search(blob))


_PLEASE_ENTER = re.compile(r"请输入\s*([^\s，。；;：:]{1,24})", re.I)
_FIELD_FROM_REQUIRED = re.compile(
    r"([^\s，。；;：:]{1,24})\s*(?:不能为空|必填|is required|cannot be empty)",
    re.I,
)


def _problem_field_hints(snapshot: str, decision: dict[str, Any] | None = None) -> list[str]:
    """Extract field name hints from validation copy / decision blob (e.g. 标题 from 请输入标题)."""
    hints: list[str] = []
    blobs: list[str] = [str(snapshot or "")]
    if decision:
        blobs.append(
            " ".join(str(decision.get(k) or "") for k in ("value", "assert", "reason", "name"))
        )
    for blob in blobs:
        for m in _PLEASE_ENTER.finditer(blob):
            token = (m.group(1) or "").strip()
            if token and not _CAPTCHA_PLACEHOLDER.search(token) and token not in hints:
                hints.append(token)
        for m in _FIELD_FROM_REQUIRED.finditer(blob):
            token = (m.group(1) or "").strip()
            if token and token not in hints:
                hints.append(token)
    return hints


def _snapshot_textboxes(snapshot: str) -> list[dict[str, str]]:
    """Named textbox/input refs from accessibility snapshot (skip captcha placeholders)."""
    rows: list[dict[str, str]] = []
    for line in str(snapshot or "").splitlines():
        if _line_is_captcha_placeholder(line):
            continue
        low = line.lower()
        if "textbox" not in low and "input" not in low and 'role="textbox"' not in low:
            # Still allow named rows from _SNAP_NAMED when role is textbox
            pass
        m = _SNAP_NAMED.search(line)
        if not m:
            continue
        role, name, ref = m.group(1), m.group(2), m.group(3)
        if role.lower() not in ("textbox", "input", "searchbox", "combobox"):
            continue
        if _line_is_captcha_placeholder(line) or _CAPTCHA.search(name or ""):
            continue
        rows.append({"role": role.lower(), "name": name, "ref": ref.lower()})
    if rows:
        return rows
    # Fallback: any textbox ref without a clean name
    for m in _SNAP_TEXTBOX_REF.finditer(snapshot or ""):
        ref = (m.group(1) or m.group(2) or "").lower()
        if not ref:
            continue
        # Find the line for captcha filter
        for line in str(snapshot or "").splitlines():
            if ref in line.lower() and not _line_is_captcha_placeholder(line):
                rows.append({"role": "textbox", "name": "", "ref": ref})
                break
    return rows


def _fill_value_for_field(
    field_name: str,
    *,
    plan: dict[str, Any] | None,
    ctx: dict[str, str] | None,
) -> str:
    """Pick fill value: test_data / ctx / placeholder based on field name."""
    plan = plan or {}
    ctx = ctx or {}
    test_data = str(plan.get("test_data") or "").strip()
    goal = str(plan.get("goal") or "").strip()
    name_l = (field_name or "").lower()

    if _USER_FIELD.search(field_name or "") and ctx.get("username"):
        return str(ctx["username"])
    if _PASS_FIELD.search(field_name or "") and ctx.get("password"):
        return str(ctx["password"])

    if test_data:
        # Prefer a segment that mentions the field, else first non-empty chunk
        for part in re.split(r"[;；\n|/]", test_data):
            part = part.strip()
            if not part:
                continue
            if field_name and field_name in part:
                # "标题：xxx" / "标题 xxx"
                m = re.search(rf"{re.escape(field_name)}\s*[:：=]?\s*(.+)$", part)
                if m and m.group(1).strip():
                    return m.group(1).strip()[:80]
                return part[:80]
        # No field match: use first chunk if short
        first = re.split(r"[;；\n|/]", test_data)[0].strip()
        if first:
            m = re.search(r"[:：=]\s*(.+)$", first)
            return (m.group(1).strip() if m else first)[:80]

    if goal and field_name and field_name in goal:
        return f"测试{field_name}"[:40]
    if field_name:
        return f"测试{field_name}"[:40]
    if goal:
        return goal[:40]
    return "测试数据"


def force_fill_for_problem_hint(
    snapshot: str,
    plan: dict[str, Any] | None = None,
    ctx: dict[str, str] | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a fill decision to correct validation/empty-field problem state.

    Returns None when no suitable textbox ref can be found.
    """
    boxes = _snapshot_textboxes(snapshot)
    if not boxes:
        return None
    hints = _problem_field_hints(snapshot, decision)
    chosen: dict[str, str] | None = None
    for hint in hints:
        for box in boxes:
            if hint and hint in (box.get("name") or ""):
                chosen = box
                break
        if chosen:
            break
    if not chosen:
        chosen = boxes[0]
    field_name = (chosen.get("name") or (hints[0] if hints else "")).strip()
    value = _fill_value_for_field(field_name, plan=plan, ctx=ctx)
    out: dict[str, Any] = {
        "action": "fill",
        "selector": chosen["ref"],
        "value": value,
        "reason": "problem_react_fill",
        "driver": "playwright",
        "name": field_name or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def _snapshot_controls(snapshot: str) -> list[dict[str, Any]]:
    """Ordered named controls + dialog markers from accessibility snapshot lines."""
    rows: list[dict[str, Any]] = []
    for line in str(snapshot or "").splitlines():
        if _DIALOG_LINE.search(line):
            rows.append({"kind": "dialog", "line": line})
            continue
        m = _SNAP_NAMED.search(line)
        if not m:
            continue
        role, name, ref = m.group(1), m.group(2), m.group(3)
        rows.append(
            {
                "kind": "control",
                "role": role.lower(),
                "name": name,
                "ref": ref.lower(),
            }
        )
    return rows


def _scope_controls(
    controls: list[dict[str, Any]],
    *,
    filled_ref: str,
) -> list[dict[str, Any]]:
    """If filled_ref sits in a dialog block, return that dialog's controls; else all controls."""
    only = [c for c in controls if c.get("kind") == "control"]
    ref = str(filled_ref or "").strip().lower()
    if ref.startswith("ref="):
        ref = ref[4:]
    if not ref:
        return only

    # Find dialog span containing filled_ref
    dialog_start = -1
    filled_idx = -1
    for i, row in enumerate(controls):
        if row.get("kind") == "dialog":
            dialog_start = i
            continue
        if row.get("kind") == "control" and row.get("ref") == ref:
            filled_idx = i
            break
    if filled_idx < 0:
        return only
    if dialog_start < 0 or dialog_start > filled_idx:
        return only

    end = len(controls)
    for j in range(filled_idx + 1, len(controls)):
        if controls[j].get("kind") == "dialog":
            end = j
            break
    return [c for c in controls[dialog_start + 1 : end] if c.get("kind") == "control"]


def force_click_submit(
    snapshot: str,
    *,
    filled_ref: str = "",
) -> dict[str, Any] | None:
    """Build a click decision from snapshot structure (no submit-label hardcoding).

    Scope: dialog containing filled_ref, else whole page.
    Candidates: buttons after the last field control in scope; else all buttons in scope.
    Pick: last candidate button (cancel/refresh usually precede confirm/login).
    """
    controls = _snapshot_controls(snapshot)
    scoped = _scope_controls(controls, filled_ref=filled_ref)
    if not scoped:
        return None

    last_field_i = -1
    for i, c in enumerate(scoped):
        if c.get("role") in _FIELD_ROLES:
            last_field_i = i
    after_fields = scoped[last_field_i + 1 :] if last_field_i >= 0 else scoped
    candidates = [c for c in after_fields if c.get("role") == "button"]
    if not candidates:
        candidates = [c for c in scoped if c.get("role") == "button"]
    if not candidates:
        return None
    chosen = candidates[-1]
    return {
        "action": "click",
        "selector": chosen["ref"],
        "driver": "playwright",
        "role": chosen["role"],
        "name": chosen["name"],
        "reason": "fill_streak_submit",
    }


def last_trace_action_kind(trace: list[dict[str, Any]] | None) -> str:
    """Last non-empty action/kind from explore trace."""
    for item in reversed(trace or []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("action") or item.get("kind") or "").strip()
        if kind and kind != "decide":
            return kind
    return ""


def _find_captcha_refresh_target(cli: PlaywrightCliSession, snapshot: str = "") -> str | None:
    """Locate captcha refresh/reload button ref from current snapshot."""
    snap = snapshot or ""
    if not snap:
        try:
            snap = cli.snapshot()
        except Exception:  # noqa: BLE001
            snap = ""
    for line in (snap or "").splitlines():
        if not _CAPTCHA_REFRESH.search(line):
            continue
        if "button" not in line.lower() and "link" not in line.lower() and "[ref=" not in line:
            continue
        m = re.search(r"\[ref=(e\d+)\]", line, re.I)
        if m:
            return m.group(1).lower()
    return None


def _refresh_button_name_from_snapshot(snapshot: str, ref: str) -> str:
    needle = f"[ref={ref}]"
    for line in (snapshot or "").splitlines():
        if needle not in line:
            continue
        m = re.search(r'(?:button|link)\s+"([^"]+)"', line, re.I)
        if m:
            return m.group(1)
    return "refresh captcha"


def retry_captcha_after_failure(
    cli: PlaywrightCliSession,
    *,
    midscene_model_config: dict[str, str] | None,
    on_log: Callable[[str], None] | None = None,
    snapshot: str = "",
) -> list[dict[str, Any]]:
    """Refresh captcha image (if found) then Midscene read + CLI fill. Returns trace rows."""

    def _log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:  # noqa: BLE001
                pass

    rows: list[dict[str, Any]] = []
    snap = snapshot or ""
    refresh_ref = _find_captcha_refresh_target(cli, snap)
    if refresh_ref:
        btn_name = _refresh_button_name_from_snapshot(snap, refresh_ref)
        cli.click(refresh_ref)
        rows.append(
            {
                "kind": "click",
                "action": "click",
                "selector": refresh_ref,
                "name": btn_name,
                "status": "ok",
                "driver": "playwright",
                "reason": "captcha_retry_refresh",
            }
        )
        _log(f"验证码失败：已点击刷新控件（{btn_name}）")
    else:
        _log("验证码失败：未找到刷新控件，直接重读")

    captcha_rows = _execute_captcha_midscene_read(
        cli,
        {
            "action": "fill",
            "driver": "midscene",
            "prompt": _CAPTCHA_QUERY_PROMPT,
            "locate": "验证码图片中的字符",
        },
        model_config=midscene_model_config,
        on_log=on_log,
    )
    rows.extend(captcha_rows)
    return rows


def _find_captcha_fill_target(cli: PlaywrightCliSession) -> str:
    """Prefer snapshot textbox ref near captcha labels; no site-specific placeholder fallback."""
    try:
        snap = cli.snapshot()
    except Exception:  # noqa: BLE001
        snap = ""
    for line in (snap or "").splitlines():
        if not _CAPTCHA.search(line) and not re.search(r"请输入|enter\s*code|verification", line, re.I):
            continue
        # Prefer textbox/input lines over buttons/images
        if "button" in line.lower() and "textbox" not in line.lower() and "input" not in line.lower():
            continue
        m = re.search(r"\[ref=(e\d+)\]", line, re.I)
        if m:
            return m.group(1).lower()
    for m in _SNAP_TEXTBOX_REF.finditer(snap or ""):
        ref = m.group(1) or m.group(2)
        if ref:
            return ref.lower()
    raise RuntimeError("snapshot 中未找到验证码输入框 ref，无法自动填码")


def _page_path(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path if path.startswith("/") else "/" + path
    except Exception:  # noqa: BLE001
        return "/"


def locator_fields_from_snapshot(snap: str, ref: str) -> dict[str, str]:
    """Parse role + accessible name for a snapshot ref (e23 → button/登录)."""
    from app.services.ui_automation.ui_runner import sanitize_accessible_name

    try:
        want = normalize_ref(ref)
    except ValueError:
        return {}
    for m in _SNAP_NAMED.finditer(snap or ""):
        role, name, found = m.group(1), m.group(2), m.group(3)
        if found.lower() != want:
            continue
        role = (role or "").strip().lower()
        name = sanitize_accessible_name((name or "").strip())
        if not role or role == "generic" or not name:
            continue
        return {"role": role, "name": name}
    return {}


def _usable_locator_expr(expr: str) -> str:
    from app.services.ui_automation.ui_runner import rewrite_getby_name

    text = (expr or "").strip()
    if not text or is_snapshot_ref_locator(text):
        return ""
    return rewrite_getby_name(text)


def _selector_from_locator_expr(expr: str) -> dict[str, str]:
    """Map `page.getByRole(...)` style string into action fields for codegen."""
    from app.services.ui_automation.ui_runner import sanitize_accessible_name

    from app.services.ui_automation.ui_runner import rewrite_getby_name

    out: dict[str, str] = {}
    text = rewrite_getby_name((expr or "").strip())
    if not text:
        return out
    out["selector"] = text
    role_m = re.search(
        r"getByRole\(\s*['\"](\w+)['\"]\s*,\s*\{\s*name:\s*['\"]([^'\"]+)['\"]",
        text,
    )
    if role_m:
        out["role"] = role_m.group(1)
        out["name"] = sanitize_accessible_name(role_m.group(2))
        return out
    ph = re.search(r"getByPlaceholder\(\s*['\"]([^'\"]+)['\"]", text)
    if ph:
        out["placeholder"] = ph.group(1)
        return out
    lab = re.search(r"getByLabel\(\s*['\"]([^'\"]+)['\"]", text)
    if lab:
        out["name"] = sanitize_accessible_name(lab.group(1))
        return out
    tid = re.search(r"getByTestId\(\s*['\"]([^'\"]+)['\"]", text)
    if tid:
        out["selector"] = f"testid={tid.group(1)}"
        return out
    return out


def _action_from_decision(
    decision: dict[str, Any],
    *,
    locator_expr: str = "",
    kind_override: str | None = None,
) -> dict[str, Any]:
    action = str(decision.get("action") or "").strip()
    kind = kind_override or (
        "fill_captcha"
        if action == "fill" and _is_captcha_decision(decision)
        else "fill"
        if action == "fill"
        else "click"
        if action == "click"
        else "press"
        if action == "press"
        else "goto"
        if action == "goto"
        else action
    )
    row: dict[str, Any] = {
        "kind": kind,
        "action": "fill" if kind == "fill_captcha" else action,
        "status": "ok",
        "driver": str(decision.get("driver") or "playwright").lower() or "playwright",
    }
    if decision.get("reason"):
        row["reason"] = decision["reason"]
    if decision.get("prompt"):
        row["prompt"] = decision["prompt"]
        row["locate"] = decision.get("locate") or decision["prompt"]
    if decision.get("locate"):
        row["locate"] = decision["locate"]
    for key in ("placeholder", "role", "name"):
        if decision.get(key):
            row[key] = decision[key]
    usable = _usable_locator_expr(locator_expr)
    if usable:
        row.update(_selector_from_locator_expr(usable))
        row["cli_locator"] = usable
    elif decision.get("selector") and not is_snapshot_ref_locator(str(decision.get("selector"))):
        row["selector"] = decision["selector"]
    if decision.get("value") is not None and kind in ("fill", "fill_captcha", "goto", "press"):
        row["value"] = decision["value"]
    if kind == "fill_captcha":
        row["captcha"] = True
        # Read image is midscene; input box stays playwright in codegen.
        if row.get("driver") == "midscene" and not decision.get("prompt"):
            row["driver"] = "playwright"
    return row


def _execute_captcha_midscene_read(
    cli: PlaywrightCliSession,
    decision: dict[str, Any],
    *,
    model_config: dict[str, str] | None,
    on_log: Callable[[str], None] | None,
) -> list[dict[str, Any]]:
    """Midscene aiQuery on live page, then playwright-cli fill captcha input."""

    def _log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:  # noqa: BLE001
                pass

    cdp = getattr(cli, "cdp_endpoint", None) or ""
    prompt = str(decision.get("prompt") or decision.get("locate") or _CAPTCHA_QUERY_PROMPT).strip()
    if not cdp:
        raise RuntimeError("无 CDP，无法在探索中调用 Midscene 读验证码")
    if not model_config:
        raise RuntimeError("未配置 Midscene 模型，无法读验证码")

    from app.services.ui_automation.node_ui_runner import midscene_query_over_cdp

    _log("Midscene：正在识别验证码图片…")
    text = midscene_query_over_cdp(
        cdp_endpoint=cdp,
        prompt=prompt or _CAPTCHA_QUERY_PROMPT,
        model_config=model_config,
        timeout_ms=getattr(cli, "timeout_ms", 15000) or 15000,
        on_log=on_log,
    )
    if not text:
        raise RuntimeError("Midscene aiQuery 未返回验证码字符")
    masked = f"{text[:2]}{'*' * max(0, len(text) - 2)}" if len(text) > 2 else "**"
    _log(f"Midscene：已识别验证码 {masked}")

    target = _find_captcha_fill_target(cli)
    cli.fill(target, text)
    locator_expr = ""
    try:
        if is_element_ref(target):
            locator_expr = _usable_locator_expr(cli.generate_locator(normalize_ref(target)))
            if not locator_expr:
                try:
                    fields = locator_fields_from_snapshot(cli.snapshot(), target)
                except Exception:  # noqa: BLE001
                    fields = {}
                for key, val in fields.items():
                    if val and not decision.get(key):
                        decision[key] = val
    except Exception as loc_ex:  # noqa: BLE001
        log.info("generate-locator after captcha fill failed: %s", loc_ex)

    mid_row: dict[str, Any] = {
        "kind": "ai_query",
        "action": "ai_query",
        "driver": "midscene",
        "prompt": prompt or _CAPTCHA_QUERY_PROMPT,
        "locate": prompt or "验证码图片中的字符",
        "reason": "captcha-image: midscene aiQuery",
        "status": "ok",
        "explore_captcha_read": True,
        "captcha": True,
    }
    fill_decision = {
        **decision,
        "action": "fill",
        "driver": "playwright",
        "value": "{{captcha}}",
        "prompt": prompt or _CAPTCHA_QUERY_PROMPT,
    }
    # Prefer live generate-locator; keep LLM-provided name/placeholder if present
    if decision.get("placeholder"):
        fill_decision["placeholder"] = decision["placeholder"]
    if decision.get("name"):
        fill_decision["name"] = decision["name"]
    fill_row = _action_from_decision(
        fill_decision,
        locator_expr=locator_expr,
        kind_override="fill_captcha",
    )
    fill_row["status"] = "ok"
    fill_row["captcha"] = True
    fill_row["value"] = "{{captcha}}"
    if locator_expr:
        fill_row["cli_locator"] = locator_expr
    _log(f"playwright-cli：已填入验证码框（{target}）")
    return [mid_row, fill_row]


def _execute_generic_midscene(
    cli: PlaywrightCliSession,
    decision: dict[str, Any],
    *,
    model_config: dict[str, str] | None,
    ctx: dict[str, str] | None,
    on_log: Callable[[str], None] | None,
) -> dict[str, Any]:
    """Run Midscene aiTap/aiInput/aiQuery via CDP for non-captcha explore steps."""

    def _log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:  # noqa: BLE001
                pass

    action = str(decision.get("action") or "click").strip()
    prompt = str(
        decision.get("prompt")
        or decision.get("locate")
        or decision.get("selector")
        or "目标控件"
    ).strip()
    cdp_action = _midscene_cdp_action(decision)
    kind = {"fill": "ai_input", "query": "ai_query"}.get(cdp_action, "ai_tap")
    row = _action_from_decision(
        decision,
        kind_override=kind if action in ("click", "fill", "ai_query", "query") else action,
    )
    row["driver"] = "midscene"
    row["prompt"] = prompt
    row["locate"] = prompt
    row["reason"] = row.get("reason") or "midscene: visual / unstable target"

    cdp = getattr(cli, "cdp_endpoint", None) or ""
    if not cdp:
        row["status"] = "error"
        row["error"] = "无 CDP，无法执行 Midscene 步"
        _log(row["error"])
        return row
    if not model_config:
        row["status"] = "error"
        row["error"] = "未配置 Midscene 模型，跳过视觉步"
        _log(row["error"])
        return row

    value = decision.get("value")
    if isinstance(value, str) and "{{" in value:
        value = render_template(value, ctx or {})
    value_str = "" if value is None else str(value)

    from app.services.ui_automation.node_ui_runner import midscene_step_over_cdp

    _log(f"Midscene：执行 {cdp_action}（{prompt[:60]}）")
    midscene_step_over_cdp(
        cdp_endpoint=cdp,
        action=cdp_action,
        prompt=prompt,
        value=value_str,
        model_config=model_config,
        timeout_ms=getattr(cli, "timeout_ms", 15000) or 15000,
        on_log=on_log,
    )
    row["status"] = "ok"
    row["explore_midscene_act"] = True
    if cdp_action == "fill" and value_str:
        row["value"] = value_str
    _log(format_cli_action_log(action or cdp_action, row, ctx=ctx))
    return row


def apply_cli_decision(
    cli: PlaywrightCliSession,
    decision: dict[str, Any],
    *,
    base_url: str,
    ctx: dict[str, str],
    url: str,
    on_log: Callable[[str], None] | None = None,
    midscene_model_config: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Execute one LLM decision via playwright-cli. Returns (trace rows, updated url)."""

    def _log(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:  # noqa: BLE001
                pass

    decision = dict(decision)
    action_pre = str(decision.get("action") or "done").strip()
    # Enrich ref → name from live snapshot before field-kind / bind (e10 alone is ambiguous).
    if action_pre == "fill":
        sel0 = str(decision.get("selector") or "").strip()
        if is_element_ref(sel0) and not (decision.get("name") or decision.get("placeholder")):
            try:
                fields = locator_fields_from_snapshot(cli.snapshot(), sel0)
            except Exception:  # noqa: BLE001
                fields = {}
            for key, val in fields.items():
                if val and not decision.get(key):
                    decision[key] = val
    decision = bind_fill_value_from_ctx(decision, ctx)
    action = str(decision.get("action") or "done").strip()
    driver = str(decision.get("driver") or "playwright").strip().lower() or "playwright"
    rows: list[dict[str, Any]] = []

    # Midscene: captcha recipe (aiQuery+fill) or generic aiTap/aiInput/aiQuery over CDP.
    if driver == "midscene":
        fp = midscene_noop_fingerprint(decision)
        if fp and decision.get("_trace_has_same"):
            _log(f"跳过重复 midscene 步: {decision.get('prompt') or decision.get('locate')}")
            return [], url
        if fp == CAPTCHA_READ_FINGERPRINT:
            try:
                return (
                    _execute_captcha_midscene_read(
                        cli,
                        decision,
                        model_config=midscene_model_config,
                        on_log=on_log,
                    ),
                    url,
                )
            except Exception as ex:  # noqa: BLE001
                _log(f"验证码 Midscene 读图失败: {ex}")
                row = _action_from_decision(
                    decision,
                    kind_override="click" if action == "click" else action,
                )
                row["driver"] = "midscene"
                row["prompt"] = row.get("prompt") or _CAPTCHA_QUERY_PROMPT
                row["locate"] = row["prompt"]
                row["status"] = "error"
                row["error"] = str(ex)[:500]
                row["explore_captcha_read"] = True
                row["captcha"] = True
                rows.append(row)
                return rows, url
        try:
            row = _execute_generic_midscene(
                cli,
                decision,
                model_config=midscene_model_config,
                ctx=ctx,
                on_log=on_log,
            )
        except Exception as ex:  # noqa: BLE001
            _log(f"Midscene 执行失败: {ex}")
            prompt = str(
                decision.get("prompt")
                or decision.get("locate")
                or decision.get("selector")
                or "目标控件"
            ).strip()
            row = _action_from_decision(
                decision,
                kind_override="ai_tap" if action == "click" else action,
            )
            row["driver"] = "midscene"
            row["prompt"] = prompt
            row["locate"] = prompt
            row["status"] = "error"
            row["error"] = str(ex)[:500]
        rows.append(row)
        return rows, url

    sel = decision.get("selector")
    val = decision.get("value")
    if isinstance(sel, str) and "{{" in sel:
        sel = render_template(sel, ctx)
        decision["selector"] = sel
    if isinstance(val, str) and "{{" in val:
        val = render_template(val, ctx)
        decision["value"] = val

    if action == "goto":
        path = str(val or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        cli.goto(join_explore_url(base_url, path))
        url = join_explore_url(base_url, path)
        rows.append(
            {
                "kind": "goto",
                "action": "goto",
                "path": path,
                "value": path,
                "status": "ok",
                "driver": "playwright",
            }
        )
        _log(format_cli_action_log("goto", {**decision, "value": path}, ctx=ctx))
        return rows, url

    if action in ("click", "fill"):
        if not sel:
            raise ValueError("缺少 selector/ref")
        target = str(sel).strip()
        locator_expr = ""
        if is_element_ref(target):
            ref = normalize_ref(target)
            if action == "click":
                cli.click(ref)
            else:
                if _is_captcha_decision(decision):
                    # Prefer live Midscene read + fill when CDP is available
                    if getattr(cli, "cdp_endpoint", None) and midscene_model_config:
                        return (
                            _execute_captcha_midscene_read(
                                cli,
                                {**decision, "driver": "midscene"},
                                model_config=midscene_model_config,
                                on_log=on_log,
                            ),
                            url,
                        )
                    locator_expr = _usable_locator_expr(cli.generate_locator(ref))
                    if not locator_expr:
                        try:
                            fields = locator_fields_from_snapshot(cli.snapshot(), ref)
                        except Exception:  # noqa: BLE001
                            fields = {}
                        for key, val in fields.items():
                            if val and not decision.get(key):
                                decision[key] = val
                    row = _action_from_decision(
                        decision,
                        locator_expr=locator_expr,
                        kind_override="fill_captcha",
                    )
                    row["status"] = "ok"
                    rows.append(row)
                    _log(
                        format_cli_action_log("fill", decision, locator=locator_expr, ctx=ctx)
                        + " · 验证码框已记录 locator（无 CDP/Midscene，读图留给脚本）"
                    )
                    return rows, url
                _clear_then_fill(cli, ref, str(val or ""))
            try:
                locator_expr = _usable_locator_expr(cli.generate_locator(ref))
            except Exception as loc_ex:  # noqa: BLE001
                log.info("generate-locator failed: %s", loc_ex)
                locator_expr = ""
            if not locator_expr:
                try:
                    fields = locator_fields_from_snapshot(cli.snapshot(), ref)
                except Exception:  # noqa: BLE001
                    fields = {}
                for key, val in fields.items():
                    if val and not decision.get(key):
                        decision[key] = val
        else:
            if action == "click":
                cli.click(target)
            else:
                if _is_captcha_decision(decision):
                    row = _action_from_decision(decision, kind_override="fill_captcha")
                    row["status"] = "ok"
                    rows.append(row)
                    _log(format_cli_action_log("fill", decision, ctx=ctx) + " · 验证码框已记录")
                    return rows, url
                _clear_then_fill(cli, target, str(val or ""))
            locator_expr = target if target.startswith("page.") else ""
            if not locator_expr and target.startswith("placeholder="):
                ph = target.split("=", 1)[1].strip().strip("\"'")
                decision["placeholder"] = ph
            if not locator_expr and target.startswith("label="):
                decision["name"] = target.split("=", 1)[1].strip().strip("\"'")
            if not locator_expr and target.startswith("role="):
                decision["selector"] = target
        row = _action_from_decision(decision, locator_expr=locator_expr)
        row["status"] = "ok"
        rows.append(row)
        _log(format_cli_action_log(action, decision, locator=locator_expr, ctx=ctx))
        return rows, url

    if action == "press":
        cli.press(str(val or "Enter"))
        rows.append(
            {
                "kind": "press",
                "action": "press",
                "value": str(val or "Enter"),
                "status": "ok",
                "driver": "playwright",
            }
        )
        _log(format_cli_action_log("press", decision, ctx=ctx))
        return rows, url

    if action in ("assert_visible", "assert_text") and assert_targets_problem_text(decision):
        row = {
            **decision,
            "kind": action,
            "action": action,
            "status": "skipped",
            "reason": PROBLEM_TEXT_NOT_PROGRESS,
            "driver": "playwright",
            "explore_problem_assert_skip": True,
        }
        rows.append(row)
        _log(
            format_cli_action_log(action, decision, ctx=ctx)
            + " · 跳过：问题/校验文案不算进度，请纠偏"
        )
        return rows, url

    rows.append({**decision, "kind": action, "status": "ok", "driver": "playwright"})
    return rows, url


def run_cli_explore_loop(
    *,
    base_url: str,
    plan: dict[str, Any],
    ctx: dict[str, str],
    llm_cfg: LlmRuntime,
    headed: bool = False,
    max_steps: int = _DEFAULT_MAX_STEPS,
    timeout_ms: int = 15000,
    should_cancel: Callable[[], bool] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
    on_screenshot: Callable[[bytes, dict[str, Any]], str | None] | None = None,
    session: PlaywrightCliSession | None = None,
    midscene_model_config: dict[str, str] | None = None,
    storage_state: dict[str, Any] | str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Explore via LangGraph (prompt decide + playwright-cli + Midscene captcha query)."""
    from app.services.ui_automation.ui_explore_graph import run_ui_explore_graph

    return run_ui_explore_graph(
        base_url=base_url,
        plan=plan,
        ctx=ctx,
        llm_cfg=llm_cfg,
        headed=headed,
        max_steps=max_steps,
        timeout_ms=timeout_ms,
        should_cancel=should_cancel,
        on_step=on_step,
        on_log=on_log,
        on_need_human=on_need_human,
        on_screenshot=on_screenshot,
        session=session,
        midscene_model_config=midscene_model_config,
        storage_state=storage_state,
        extra_http_headers=extra_http_headers,
        local_storage=local_storage,
    )


def actions_from_cli_trace(trace: list[dict[str, Any]], *, start_path: str = "/") -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in trace:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "error":
            continue
        kind = str(item.get("kind") or item.get("action") or "").strip()
        if kind in ("", "done", "ai_query"):
            # ai_query is folded into fill_captcha codegen (aiQuery + fill)
            continue
        if kind == "goto":
            actions.append({"kind": "goto", "path": str(item.get("path") or item.get("value") or start_path)})
            continue
        row = dict(item)
        row["kind"] = "fill" if kind == "fill_captcha" else kind
        if kind == "fill_captcha":
            row["kind"] = "fill_captcha"
        # Prefer codegen-friendly fields from generate-locator
        if item.get("cli_locator") and not item.get("placeholder") and not item.get("role"):
            # Keep cli_locator as locate hint; actions_to_node_script uses placeholder/role/selector
            pass
        if item.get("driver") == "midscene" and kind in ("click", "tap"):
            row["kind"] = "click"
            row["locate"] = item.get("locate") or item.get("prompt") or "按钮"
        actions.append(row)
    if not any(a.get("kind") == "goto" for a in actions):
        actions.insert(0, {"kind": "goto", "path": start_path or "/"})
    return actions


def compile_cli_trace_script(
    trace: list[dict[str, Any]],
    *,
    start_path: str = "/",
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
) -> str:
    actions = actions_from_cli_trace(trace, start_path=start_path)
    return actions_to_node_script(
        actions,
        ctx=ctx,
        username=username,
        password=password,
        start_path=start_path or "/",
        ensure_submit=False,
    )
