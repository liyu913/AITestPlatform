"""Export UI run reports as JUnit XML or simple HTML."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from typing import Any
from xml.dom import minidom


def build_junit_xml(
    *,
    run_id: int,
    project_id: int,
    status: str,
    steps: list[dict[str, Any]],
) -> str:
    suite = ET.Element(
        "testsuite",
        {
            "name": f"ui-run-{run_id}",
            "tests": str(len(steps)),
            "failures": str(sum(1 for s in steps if s.get("status") == "failed")),
            "errors": str(sum(1 for s in steps if s.get("status") == "error")),
            "skipped": str(sum(1 for s in steps if s.get("status") == "skipped")),
        },
    )
    suite.set("project_id", str(project_id))
    suite.set("run_status", status)
    for st in steps:
        name = str(st.get("case_name") or "") or f"{st.get('action')}-{st.get('order')}"
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"project.{project_id}.ui",
                "name": name,
                "time": str((st.get("duration_ms") or 0) / 1000.0),
            },
        )
        s = st.get("status")
        if s == "failed":
            fail = ET.SubElement(case, "failure", {"message": st.get("error_message") or "failed"})
            fail.text = st.get("error_message") or ""
        elif s == "error":
            err = ET.SubElement(case, "error", {"message": st.get("error_message") or "error"})
            err.text = st.get("error_message") or ""
        elif s == "skipped":
            ET.SubElement(case, "skipped")
    rough = ET.tostring(suite, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ")


def build_html_report(
    *,
    run_id: int,
    project_id: int,
    status: str,
    summary: dict[str, Any] | None,
    steps: list[dict[str, Any]],
) -> str:
    rows = []
    for st in steps:
        color = {"passed": "#389e0d", "failed": "#cf1322", "error": "#cf1322", "skipped": "#8c8c8c"}.get(
            str(st.get("status")), "#000"
        )
        rows.append(
            f"""
            <tr>
              <td>{st.get('order')}</td>
              <td>{html.escape(str(st.get('case_name') or ''))}</td>
              <td>{html.escape(str(st.get('action') or ''))}</td>
              <td>{html.escape(str(st.get('selector') or ''))}</td>
              <td style="color:{color}">{html.escape(str(st.get('status')))}</td>
              <td>{st.get('duration_ms') or ''}</td>
              <td>{html.escape(str(st.get('error_message') or ''))}</td>
            </tr>
            """
        )
    summary_txt = html.escape(str(summary or {}))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>UI Run #{run_id}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #eee;padding:8px;vertical-align:top;font-size:13px}}
th{{background:#fafafa;text-align:left}}
</style></head>
<body>
<h1>UI Run #{run_id}</h1>
<p>project={project_id} · status=<b>{html.escape(status)}</b></p>
<p>summary: {summary_txt}</p>
<table>
<thead><tr><th>#</th><th>Case</th><th>Action</th><th>Selector</th><th>Status</th><th>ms</th><th>Error</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>
"""
