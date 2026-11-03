"""Export functional test cases to Excel (.xlsx) or XMind Zen (.xmind)."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from typing import Iterable

from openpyxl import Workbook

from app.models.functional.testcase import TestCase, TestCaseStep

STEPS_EXPECTED_PLACEHOLDER = "—"

XLSX_HEADERS = [
    "ID",
    "标题",
    "模块",
    "优先级",
    "状态",
    "类型",
    "前置条件",
    "测试数据",
    "步骤",
    "总体期望",
    "关联需求ID",
]


def _sorted_steps(case: TestCase) -> list[TestCaseStep]:
    return sorted(case.steps, key=lambda s: (s.order, s.id))


def format_steps_text(case: TestCase) -> str:
    steps = _sorted_steps(case)
    if not steps:
        return ""
    lines: list[str] = []
    for i, st in enumerate(steps, start=1):
        action = (st.action or "").strip()
        expected = (st.expected or "").strip()
        if expected and expected != STEPS_EXPECTED_PLACEHOLDER:
            lines.append(f"{i}. {action}\n   期望：{expected}")
        else:
            lines.append(f"{i}. {action}")
    return "\n\n".join(lines)


def build_xlsx_bytes(cases: Iterable[TestCase]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    ws.append(XLSX_HEADERS)
    for c in cases:
        ws.append(
            [
                c.id,
                c.title or "",
                (c.module or "").strip(),
                c.priority or "",
                c.status or "",
                c.case_type or "",
                c.precondition or "",
                c.test_data or "",
                format_steps_text(c),
                c.overall_expected or "",
                c.requirement_id if c.requirement_id is not None else "",
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _topic(title: str, children: list[dict] | None = None) -> dict:
    node: dict = {"id": str(uuid.uuid4()), "title": title}
    if children:
        node["children"] = {"attached": children}
    return node


def build_xmind_bytes(cases: Iterable[TestCase]) -> bytes:
    by_module: dict[str, list[TestCase]] = {}
    for c in cases:
        key = (c.module or "").strip() or "未分类"
        by_module.setdefault(key, []).append(c)

    module_topics: list[dict] = []
    for module_name, module_cases in by_module.items():
        case_topics: list[dict] = []
        for c in module_cases:
            kids: list[dict] = []
            meta_parts = [
                f"优先级: {c.priority or '—'}",
                f"状态: {c.status or '—'}",
                f"类型: {c.case_type or '—'}",
            ]
            kids.append(_topic(" · ".join(meta_parts)))
            if (c.precondition or "").strip():
                kids.append(_topic(f"前置条件: {c.precondition.strip()}"))
            if (c.test_data or "").strip():
                kids.append(_topic(f"测试数据: {c.test_data.strip()}"))
            for i, st in enumerate(_sorted_steps(c), start=1):
                action = (st.action or "").strip()
                expected = (st.expected or "").strip()
                if expected and expected != STEPS_EXPECTED_PLACEHOLDER:
                    kids.append(_topic(f"步骤{i}: {action}"))
                    kids.append(_topic(f"期望{i}: {expected}"))
                else:
                    kids.append(_topic(f"步骤{i}: {action}"))
            if (c.overall_expected or "").strip():
                kids.append(_topic(f"总体期望: {c.overall_expected.strip()}"))
            case_topics.append(_topic(c.title or f"用例#{c.id}", kids))
        module_topics.append(_topic(module_name, case_topics))

    sheet = {
        "id": str(uuid.uuid4()),
        "title": "用例导出",
        "rootTopic": _topic("用例导出", module_topics),
    }
    content = json.dumps([sheet], ensure_ascii=False)
    meta = json.dumps({"creator": {"name": "Raina TestHub"}}, ensure_ascii=False)
    manifest = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0" password-hint="">
  <file-entry full-path="content.json" media-type="application/json"/>
  <file-entry full-path="metadata.json" media-type="application/json"/>
</manifest>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", content.encode("utf-8"))
        zf.writestr("metadata.json", meta.encode("utf-8"))
        zf.writestr("manifest.json", json.dumps({"file-entries": {
            "content.json": {},
            "metadata.json": {},
        }}, ensure_ascii=False).encode("utf-8"))
        zf.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))
    return buf.getvalue()
