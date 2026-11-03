"""Export API run reports as JUnit XML or simple HTML."""

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
            "name": f"api-run-{run_id}",
            "tests": str(len(steps)),
            "failures": str(sum(1 for s in steps if s.get("status") == "failed")),
            "errors": str(sum(1 for s in steps if s.get("status") == "error")),
            "skipped": str(sum(1 for s in steps if s.get("status") == "skipped")),
        },
    )
    suite.set("project_id", str(project_id))
    suite.set("run_status", status)
    for st in steps:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"project.{project_id}",
                "name": str(st.get("case_name") or f"case-{st.get('case_id')}"),
                "time": str((st.get("duration_ms") or 0) / 1000.0),
            },
        )
        s = st.get("status")
        if s == "failed":
            fail = ET.SubElement(case, "failure", {"message": st.get("error_message") or "assertion failed"})
            fail.text = "\n".join(
                str(a.get("detail") or "") for a in (st.get("assertion_results_json") or []) if not a.get("ok")
            )
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
        asserts = "".join(
            f"<li style='color:{'#389e0d' if a.get('ok') else '#cf1322'}'>{html.escape(str(a.get('detail') or a.get('type')))}</li>"
            for a in (st.get("assertion_results_json") or [])
        )
        body = html.escape((st.get("response_body_text") or "")[:4000])
        rows.append(
            f"""
            <tr>
              <td>{st.get('order')}</td>
              <td>{html.escape(str(st.get('case_name') or ''))}</td>
              <td style="color:{color}">{html.escape(str(st.get('status')))}</td>
              <td>{st.get('response_status') or ''}</td>
              <td>{st.get('duration_ms') or ''}</td>
              <td><ul>{asserts}</ul></td>
            </tr>
            <tr><td colspan="6"><pre style="white-space:pre-wrap;font-size:12px">{body}</pre></td></tr>
            """
        )
    summary_txt = html.escape(str(summary or {}))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>API Run #{run_id}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #eee;padding:8px;vertical-align:top}}
th{{background:#fafafa;text-align:left}}
</style></head>
<body>
<h1>API Run #{run_id}</h1>
<p>project={project_id} · status=<b>{html.escape(status)}</b></p>
<p>summary: {summary_txt}</p>
<table>
<thead><tr><th>#</th><th>Case</th><th>Status</th><th>HTTP</th><th>ms</th><th>Assertions</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>
"""
