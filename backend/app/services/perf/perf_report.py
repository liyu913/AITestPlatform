"""Performance run reports: HTML export and metric helpers."""

from __future__ import annotations

import html
from typing import Any


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_metrics(summary: dict[str, Any] | None) -> dict[str, float | int | None]:
    s = summary if isinstance(summary, dict) else {}
    return {
        "total_requests": int(s["total_requests"]) if s.get("total_requests") is not None else None,
        "failures": int(s["failures"]) if s.get("failures") is not None else None,
        "fail_ratio": _num(s.get("fail_ratio")),
        "rps": _num(s.get("rps")),
        "avg_ms": _num(s.get("avg_ms")),
        "p50_ms": _num(s.get("p50_ms")),
        "p90_ms": _num(s.get("p90_ms")),
        "p95_ms": _num(s.get("p95_ms")),
        "p99_ms": _num(s.get("p99_ms")),
        "elapsed_sec": _num(s.get("elapsed_sec")),
    }


def compare_metrics(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return metric deltas. Lower is better for latency/error; higher is better for rps."""
    cur = extract_metrics(current)
    base = extract_metrics(baseline)
    keys = [
        ("p95_ms", "lower"),
        ("p90_ms", "lower"),
        ("avg_ms", "lower"),
        ("fail_ratio", "lower"),
        ("rps", "higher"),
        ("total_requests", "higher"),
    ]
    out: list[dict[str, Any]] = []
    for key, better in keys:
        c = cur.get(key)
        b = base.get(key)
        delta = None
        delta_pct = None
        improved = None
        if isinstance(c, (int, float)) and isinstance(b, (int, float)):
            delta = float(c) - float(b)
            if b != 0:
                delta_pct = (delta / abs(float(b))) * 100.0
            if better == "lower":
                improved = delta < 0
            else:
                improved = delta > 0
            if abs(delta) < 1e-9:
                improved = None
        out.append(
            {
                "metric": key,
                "current": c,
                "baseline": b,
                "delta": round(delta, 4) if isinstance(delta, float) else delta,
                "delta_pct": round(delta_pct, 2) if isinstance(delta_pct, float) else delta_pct,
                "better": better,
                "improved": improved,
            }
        )
    return out


def build_html_report(
    *,
    run_id: int,
    project_id: int,
    status: str,
    summary: dict[str, Any] | None,
    config: dict[str, Any] | None,
    compare_rows: list[dict[str, Any]] | None = None,
) -> str:
    s = summary if isinstance(summary, dict) else {}
    cfg = config if isinstance(config, dict) else {}
    metrics = extract_metrics(s)
    endpoints = s.get("endpoints") if isinstance(s.get("endpoints"), list) else []
    sla = s.get("sla") if isinstance(s.get("sla"), dict) else {}

    ep_rows = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        ep_rows.append(
            "<tr>"
            f"<td>{html.escape(str(ep.get('name') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('requests') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('failures') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('fail_ratio') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('avg_ms') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('p95_ms') or ''))}</td>"
            f"<td>{html.escape(str(ep.get('rps') or ''))}</td>"
            "</tr>"
        )

    compare_html = ""
    if compare_rows:
        crow = []
        for r in compare_rows:
            color = ""
            if r.get("improved") is True:
                color = "color:#389e0d"
            elif r.get("improved") is False:
                color = "color:#cf1322"
            crow.append(
                "<tr>"
                f"<td>{html.escape(str(r.get('metric')))}</td>"
                f"<td>{html.escape(str(r.get('current')))}</td>"
                f"<td>{html.escape(str(r.get('baseline')))}</td>"
                f"<td style='{color}'>{html.escape(str(r.get('delta')))}</td>"
                f"<td style='{color}'>{html.escape(str(r.get('delta_pct')))}%</td>"
                "</tr>"
            )
        compare_html = (
            "<h2>基线对比</h2><table><thead><tr>"
            "<th>指标</th><th>当前</th><th>基线</th><th>差值</th><th>变化%</th>"
            f"</tr></thead><tbody>{''.join(crow)}</tbody></table>"
        )

    sla_checks = sla.get("checks") if isinstance(sla.get("checks"), list) else []
    sla_lis = "".join(
        f"<li>{'✓' if c.get('ok') else '✗'} {html.escape(str(c.get('name')))}: "
        f"期望≤{html.escape(str(c.get('expected')))} 实际={html.escape(str(c.get('actual')))}</li>"
        for c in sla_checks
        if isinstance(c, dict)
    )

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Perf Run {run_id}</title>"
        "<style>body{font-family:sans-serif;margin:24px}table{border-collapse:collapse;width:100%;margin:12px 0}"
        "td,th{border:1px solid #ccc;padding:6px;text-align:left}"
        "h1,h2{margin-top:24px}</style></head><body>"
        f"<h1>压测报告 #{run_id}</h1>"
        f"<p>project={project_id} status=<b>{html.escape(status)}</b> "
        f"scenario={html.escape(str(cfg.get('scenario_name') or cfg.get('scenario_id') or ''))}</p>"
        f"<p>users={html.escape(str(cfg.get('users') or ''))} "
        f"spawn_rate={html.escape(str(cfg.get('spawn_rate') or ''))} "
        f"run_time_sec={html.escape(str(cfg.get('run_time_sec') or ''))}</p>"
        "<h2>汇总</h2><ul>"
        f"<li>总请求: {html.escape(str(metrics.get('total_requests')))}</li>"
        f"<li>失败: {html.escape(str(metrics.get('failures')))}</li>"
        f"<li>错误率: {html.escape(str(metrics.get('fail_ratio')))}</li>"
        f"<li>RPS: {html.escape(str(metrics.get('rps')))}</li>"
        f"<li>平均(ms): {html.escape(str(metrics.get('avg_ms')))}</li>"
        f"<li>P50/P90/P95/P99: {html.escape(str(metrics.get('p50_ms')))} / "
        f"{html.escape(str(metrics.get('p90_ms')))} / {html.escape(str(metrics.get('p95_ms')))} / "
        f"{html.escape(str(metrics.get('p99_ms')))}</li>"
        "</ul>"
        f"<h2>SLA</h2><p>passed={html.escape(str(sla.get('passed')))}</p><ul>{sla_lis}</ul>"
        f"{compare_html}"
        "<h2>接口拆分</h2><table><thead><tr>"
        "<th>接口</th><th>请求</th><th>失败</th><th>错误率</th><th>平均</th><th>P95</th><th>RPS</th>"
        f"</tr></thead><tbody>{''.join(ep_rows) or '<tr><td colspan=7>无</td></tr>'}</tbody></table>"
        "</body></html>"
    )
