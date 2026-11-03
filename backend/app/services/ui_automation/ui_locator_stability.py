"""Explore-grounded locator extraction, stability ranking, and allowlist checks."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.ui_automation.playwright_cli_session import is_snapshot_ref_locator

# Lower = more stable (aligned with recorder / explore report tiers).
TIER_TESTID = 1
TIER_ROLE = 2
TIER_LABEL = 3
TIER_PLACEHOLDER = 4
TIER_TEXT = 5
TIER_CSS = 6
TIER_OTHER = 99

_GETBY_PREFIX = re.compile(
    r"^(?:page\.)?(getByRole|getByPlaceholder|getByText|getByLabel|getByTestId|getByTitle|getByAltText)\s*\(",
    re.I,
)
# Capture page.getBy* / page.locator(...) chains up to .click/.fill/.first etc.
_SCRIPT_LOCATOR = re.compile(
    r"(page\.(?:getBy(?:Role|Label|Placeholder|Text|TestId|Title|AltText)|locator)\([^;\n]+?\))"
    r"(?:\.(?:first|nth|locator|filter)\([^)]*\))*",
    re.I,
)
_EXPECT_LINE = re.compile(r"^\s*(?:await\s+)?expect\s*\(", re.I)
_WAIT_URL = re.compile(r"waitForURL|toHaveURL|goto\s*\(", re.I)


def _js(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_locator_expr(expr: str) -> str:
    """Normalize a page.* locator for comparison (quotes / whitespace / trailing actions)."""
    s = (expr or "").strip().rstrip(";").strip()
    if not s:
        return ""
    # Drop await and trailing interaction methods
    s = re.sub(r"^\s*await\s+", "", s)
    s = re.sub(
        r"\.(?:click|fill|type|press|check|uncheck|hover|focus|blur|selectOption)\s*\([^)]*\)\s*$",
        "",
        s,
    )
    s = s.strip().rstrip(";").strip()
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # Normalize quotes to double
    s = s.replace("'", '"')
    return s


def locator_tier(expr: str) -> int:
    """Stability tier for a page.* / getBy* / shorthand locator expression."""
    s = (expr or "").strip()
    if not s or is_snapshot_ref_locator(s):
        return TIER_OTHER
    low = s.lower()
    if "getbytestid" in low or low.startswith("testid=") or low.startswith("data-testid="):
        return TIER_TESTID
    if "getbyrole" in low or low.startswith("role="):
        return TIER_ROLE
    if "getbylabel" in low or low.startswith("label="):
        return TIER_LABEL
    if "getbyplaceholder" in low or low.startswith("placeholder="):
        return TIER_PLACEHOLDER
    if "getbytext" in low or low.startswith("text="):
        return TIER_TEXT
    if "page.locator(" in low or low.startswith("page.locator"):
        return TIER_CSS
    if _GETBY_PREFIX.match(s):
        return TIER_OTHER
    return TIER_CSS if s else TIER_OTHER


def rank_locator_exprs(exprs: list[str]) -> list[str]:
    """Dedupe and sort by stability (best first)."""
    seen: set[str] = set()
    scored: list[tuple[int, str, str]] = []
    for raw in exprs:
        norm = normalize_locator_expr(raw)
        if not norm or norm in seen:
            continue
        if is_snapshot_ref_locator(norm):
            continue
        seen.add(norm)
        scored.append((locator_tier(norm), norm, raw.strip()))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [raw for _tier, _norm, raw in scored]


def _role_name_expr(role: str, name: str) -> str:
    role = (role or "").strip()
    name = (name or "").strip()
    if role and name:
        return f"page.getByRole({_js(role)}, {{ name: {_js(name)} }})"
    if role:
        return f"page.getByRole({_js(role)})"
    return ""


def extract_step_locators(step: dict[str, Any] | None) -> list[str]:
    """Build ranked page.* locator candidates from one explore/matrix step."""
    if not isinstance(step, dict):
        return []
    raws: list[str] = []
    for key in ("cli_locator", "selector"):
        val = str(step.get(key) or "").strip()
        if val and not is_snapshot_ref_locator(val):
            raws.append(val)
    placeholder = str(step.get("placeholder") or "").strip()
    if placeholder:
        raws.append(f"placeholder={placeholder}")
    role = str(step.get("role") or "").strip()
    name = str(step.get("name") or "").strip()
    if role or name:
        expr = _role_name_expr(role or "button", name) if name else _role_name_expr(role, "")
        if expr:
            raws.append(expr)
    # label= shorthand sometimes stored in selector already
    label = str(step.get("label") or "").strip()
    if label:
        raws.append(f"label={label}")

    # Convert shorthand → page.* via lightweight rules (avoid importing ui_node_script)
    converted: list[str] = []
    for raw in raws:
        expr = _to_page_expr(raw)
        if expr:
            converted.append(expr)
    return rank_locator_exprs(converted)


def _to_page_expr(raw: str) -> str:
    s = (raw or "").strip().rstrip(";").strip()
    if not s or is_snapshot_ref_locator(s):
        return ""
    if s.startswith("page."):
        return s
    if _GETBY_PREFIX.match(s):
        return s if s.startswith("page.") else f"page.{s}"
    if s.startswith("placeholder="):
        ph = s.split("=", 1)[1].strip().strip("\"'")
        return f"page.getByPlaceholder({_js(ph)})"
    if s.startswith("label="):
        lab = s.split("=", 1)[1].strip().strip("\"'")
        return f"page.getByLabel({_js(lab)})"
    if s.startswith("text="):
        t = s[5:].strip().strip("\"'")
        return f"page.getByText({_js(t)})"
    if s.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and name.endswith("]"):
                name = name[:-1]
            name = (name or "").strip("\"'")
            if name:
                return f"page.getByRole({_js(role)}, {{ name: {_js(name)} }})"
            return f"page.getByRole({_js(role)})"
    if s.startswith("testid=") or s.startswith("data-testid="):
        tid = s.split("=", 1)[1].strip().strip("\"'")
        return f"page.getByTestId({_js(tid)})"
    return f"page.locator({_js(s)})"


def collect_explored_locators(
    *,
    matrix_row: dict[str, Any] | None = None,
    report_steps: list[dict[str, Any]] | None = None,
    case_id: str | None = None,
) -> list[str]:
    """Collect stability-ranked locators for a case from matrix + explore report."""
    exprs: list[str] = []
    cid = (case_id or "").strip()
    if isinstance(matrix_row, dict):
        if not cid:
            cid = str(matrix_row.get("id") or "").strip()
        for step in matrix_row.get("steps") or []:
            if isinstance(step, dict):
                exprs.extend(extract_step_locators(step))
    for step in report_steps or []:
        if not isinstance(step, dict):
            continue
        if cid and str(step.get("case_id") or "").strip() not in ("", cid):
            continue
        # report steps may only have selector string
        exprs.extend(extract_step_locators(step))
        sel = str(step.get("selector") or "").strip()
        if sel:
            expr = _to_page_expr(sel)
            if expr:
                exprs.append(expr)
    return rank_locator_exprs(exprs)


def script_locator_exprs(code: str, *, interaction_only: bool = True) -> set[str]:
    """Extract normalized page.* locator bases from a script."""
    out: set[str] = set()
    for line in (code or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if interaction_only and (_EXPECT_LINE.match(stripped) or _WAIT_URL.search(stripped)):
            # Assert / navigation lines: getByText here is allowed freely
            continue
        if interaction_only and stripped.startswith("await page.goto"):
            continue
        for m in _SCRIPT_LOCATOR.finditer(line):
            norm = normalize_locator_expr(m.group(1))
            if norm and not is_snapshot_ref_locator(norm):
                out.add(norm)
    return out


def locators_allowed(suggested: str, allowlist: list[str] | set[str]) -> bool:
    """True when interaction locators in suggested are ⊆ allowlist (normalized).

    Empty allowlist → True (nothing to enforce). Assert-only getByText / URL not checked.
    """
    allowed_norm = {normalize_locator_expr(x) for x in allowlist if normalize_locator_expr(x)}
    if not allowed_norm:
        return True
    used = script_locator_exprs(suggested, interaction_only=True)
    if not used:
        return True
    for loc in used:
        if loc not in allowed_norm:
            # Soft match: allow if any allowlist entry contains the same getBy* core
            if not _soft_in_allowlist(loc, allowed_norm):
                return False
    return True


def _soft_in_allowlist(loc: str, allowed: set[str]) -> bool:
    """Allow minor quote/spacing diffs already normalized; reject new names/testids."""
    if loc in allowed:
        return True
    # getByText in interaction is rare; still require allowlist hit
    return False


def pick_best_raw_candidate(raws: list[str]) -> str:
    """Pick the most stable raw candidate (testid > role > …)."""
    ranked = rank_locator_exprs([_to_page_expr(r) or r for r in raws if r])
    return ranked[0] if ranked else ""
