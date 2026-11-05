"""Judge an automated case execution against the case's declared expectations.

The previous rule was "no step failed and the last action is ``done`` ⇒ passed",
which reports success even when nothing was verified, and reports "passed" for a
run whose business expectation actually failed to materialise.

This module makes the judgement explicit:

1. Collect the case's verification points: every step expectation plus the
   overall expectation.
2. Collect assertion evidence from the execution trace (``assert_*`` rows...).
3. A run can only pass when *every* verification point is covered by an
   assertion that succeeded, and the trace ended normally.
4. Missing expectations, missing assertions or uncovered points are reported as
   ``blocked`` with a readable reason, never as ``passed``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.functional.design_trace import has_step_expectation

PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"

_ASSERTION_PREFIXES = ("assert", "verify", "expect")
_TRACE_TEXT_KEYS = ("assert", "expected", "value", "text", "selector", "message", "reason")
_URL_HINTS = ("url", "地址", "链接", "跳转", "路径", "页面")
_SPACE_RE = re.compile(r"[\s\u3000]+")
_PUNCT_RE = re.compile(r"[，。、；：！？,.;:!?\"'“”‘’（）()\[\]【】<>《》|\-—_/\\]+")


@dataclass(frozen=True)
class ExpectationPoint:
    """One thing the case says must hold after execution."""

    order: int
    source: str
    action: str
    expected: str


@dataclass
class Judgement:
    status: str
    reason: str = ""
    covered: list[int] = field(default_factory=list)
    uncovered: list[ExpectationPoint] = field(default_factory=list)
    assertion_count: int = 0

    @property
    def coverage(self) -> float:
        total = len(self.covered) + len(self.uncovered)
        return 1.0 if total == 0 else len(self.covered) / total

    def as_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "assertion_count": self.assertion_count,
            "covered_count": len(self.covered),
            "uncovered_count": len(self.uncovered),
            "coverage": round(self.coverage, 4),
            "uncovered": [
                {"order": p.order, "source": p.source, "expected": p.expected}
                for p in self.uncovered
            ],
        }


def normalize_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _longest_common_substring(a: str, b: str) -> int:
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ca = a[i - 1]
        for j in range(1, len(b) + 1):
            if ca == b[j - 1] and ca != " ":
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def texts_match(expected: str, evidence: str) -> bool:
    """Loose match between a declared expectation and an assertion payload.

    The agent words assertions itself, so an exact match is not realistic. A
    shared run of at least two significant characters (or either side
    containing the other) counts as covered; the outcome is only used to decide
    "was this expectation verified", never to auto-approve a failure.
    """
    left = normalize_text(expected)
    right = normalize_text(evidence)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    if _longest_common_substring(left, right) >= 2:
        return True
    # URL/redirect expectations are often asserted with a path only.
    if any(hint in left for hint in _URL_HINTS):
        return bool(re.search(r"/|https?://", right))
    return False


def case_expectations(case: Any) -> list[ExpectationPoint]:
    """Verification points declared by the case (steps first, then overall)."""
    points: list[ExpectationPoint] = []
    steps = sorted(getattr(case, "steps", None) or [], key=lambda s: s.order)
    for step in steps:
        expected = str(getattr(step, "expected", "") or "").strip()
        if has_step_expectation(expected):
            points.append(
                ExpectationPoint(
                    order=int(getattr(step, "order", 0) or 0),
                    source="step",
                    action=str(getattr(step, "action", "") or ""),
                    expected=expected,
                )
            )
    overall = str(getattr(case, "overall_expected", "") or "").strip()
    if has_step_expectation(overall):
        points.append(ExpectationPoint(order=0, source="overall", action="", expected=overall))
    return points


def _is_assertion(row: dict[str, Any]) -> bool:
    action = str(row.get("action") or "").strip().lower()
    return action.startswith(_ASSERTION_PREFIXES)


def _row_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _TRACE_TEXT_KEYS:
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def assertion_evidence(trace: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in trace or []:
        if not isinstance(row, dict):
            continue
        if not _is_assertion(row):
            continue
        out.append(row)
    return out


def match_expectations(
    points: list[ExpectationPoint], evidence: list[dict[str, Any]]
) -> tuple[list[int], list[ExpectationPoint]]:
    covered: list[int] = []
    uncovered: list[ExpectationPoint] = []
    for point in points:
        hit = any(texts_match(point.expected, _row_text(row)) for row in evidence)
        if hit:
            covered.append(point.order)
        else:
            uncovered.append(point)
    return covered, uncovered


def judge_trace(trace: list[Any] | None, case: Any | None) -> Judgement:
    """Decide a case execution outcome from its trace and the case expectations."""
    rows = [t for t in (trace or []) if isinstance(t, dict)]
    if not rows:
        return Judgement(status=FAILED, reason="未产生执行步骤")

    errors = [t for t in rows if str(t.get("status") or "").lower() == "error"]
    if errors:
        last_err = errors[-1]
        msg = str(last_err.get("error") or last_err.get("message") or "步骤执行失败").strip()
        return Judgement(status=FAILED, reason=msg or "步骤执行失败")

    evidence = assertion_evidence(rows)
    points = case_expectations(case) if case is not None else []

    if not points:
        return Judgement(
            status=BLOCKED,
            reason="用例未定义可校验的预期结果，无法判定通过；请补充每步预期或总体预期",
            assertion_count=len(evidence),
        )
    if not evidence:
        return Judgement(
            status=BLOCKED,
            reason="执行动作已完成，但没有任何断言证据；请在预期中明确可验证的断言点",
            assertion_count=0,
        )

    covered, uncovered = match_expectations(points, evidence)
    last = rows[-1]
    action = str(last.get("action") or "").strip().lower()
    # A successful last assertion is not a completion signal: the agent must
    # close the run explicitly. Otherwise a truncated trace looks green.
    if action != "done":
        return Judgement(
            status=FAILED,
            reason="执行未正常结束（未收到 done）",
            covered=covered,
            uncovered=uncovered,
            assertion_count=len(evidence),
        )
    if uncovered:
        preview = "；".join(f"步骤{p.order}「{p.expected[:24]}」" if p.order else f"总体「{p.expected[:24]}」" for p in uncovered[:3])
        more = f" 等 {len(uncovered)} 项" if len(uncovered) > 3 else ""
        return Judgement(
            status=BLOCKED,
            reason=f"动作完成但未覆盖必测预期：{preview}{more}",
            covered=covered,
            uncovered=uncovered,
            assertion_count=len(evidence),
        )
    return Judgement(
        status=PASSED,
        reason="",
        covered=covered,
        uncovered=[],
        assertion_count=len(evidence),
    )
