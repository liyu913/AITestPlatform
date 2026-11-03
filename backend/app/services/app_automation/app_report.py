"""Export APP run reports as JUnit XML or simple HTML."""

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
            "name": f"app-run-{run_id}",
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
                "classname": f"project.{project_id}.app",
                "name": name,
                "time": str((st.get("duration_ms") or 0) / 1000.0),
            },
        )
        msg = st.get("error_message") or ""
        if st.get("status") == "failed":
            fail = ET.SubElement(case, "failure", {"message": str(msg)[:500]})
            fail.text = str(msg)
        elif st.get("status") == "error":
            err = ET.SubElement(case, "error", {"message": str(msg)[:500]})
            err.text = str(msg)
        elif st.get("status") == "skipped":
            ET.SubElement(case, "skipped")
    rough = ET.tostring(suite, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


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
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(st.get('order', '')))}</td>"
            f"<td>{html.escape(str(st.get('case_name') or ''))}</td>"
            f"<td>{html.escape(str(st.get('action') or ''))}</td>"
            f"<td>{html.escape(str(st.get('selector') or ''))}</td>"
            f"<td>{html.escape(str(st.get('status') or ''))}</td>"
            f"<td>{html.escape(str(st.get('duration_ms') or ''))}</td>"
            f"<td>{html.escape(str(st.get('error_message') or ''))}</td>"
            "</tr>"
        )
    summary_text = html.escape(str(summary or {}))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>APP Run {run_id}</title>"
        "<style>body{font-family:sans-serif}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:6px;text-align:left}</style></head><body>"
        f"<h1>APP Run #{run_id}</h1>"
        f"<p>project={project_id} status={html.escape(status)}</p>"
        f"<p>summary={summary_text}</p>"
        "<table><thead><tr><th>#</th><th>Case</th><th>Action</th><th>Selector</th>"
        "<th>Status</th><th>ms</th><th>Error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )
