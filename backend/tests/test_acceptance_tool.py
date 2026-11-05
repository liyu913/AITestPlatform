"""B7 acceptance runbook: dry-run plan, evaluation thresholds and a fake-API end-to-end run."""

from __future__ import annotations

import json
import re

import httpx

import tools.acceptance_first_release as acceptance


def _config(**over) -> acceptance.Config:
    base = dict(
        base_url="http://fake",
        username="admin",
        password="p",
        manual=4,
        api_cases=3,
        ui_cases=3,
        requirements=2,
        known_defects=2,
        out_dir=__import__("pathlib").Path("."),
    )
    base.update(over)
    return acceptance.Config(**base)


def test_dry_run_prints_scope_without_network():
    result = acceptance.run(_config(dry_run=True))
    assert result["status"] == "dry_run"
    plan = result["plan"]
    assert plan["scope"] == {"manual": 4, "api": 3, "ui": 3, "total": 10, "requirements": 2}


class FakeBackend:
    """Minimal stateful stand-in for the platform endpoints the runbook uses."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.attempts: dict[int, list[str]] = {}
        self.result: dict[int, str] = {}
        self.defects: dict[int, dict] = {}
        self.next_id = 100
        self.report: dict | None = None

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.content else {}

        def ok(payload, status=200):
            return httpx.Response(status, json=payload)

        if path == "/api/v1/auth/login":
            return ok({"access_token": "tok", "token_type": "bearer"})
        if path == "/api/v1/users/me":
            return ok({"id": 1, "username": "admin", "is_superuser": True})
        if path == "/api/v1/projects/" and method == "GET":
            return ok([])
        if path == "/api/v1/projects/" and method == "POST":
            return ok({"id": 1, "name": body["name"]}, 201)

        m = re.fullmatch(r"/api/v1/projects/1/(api-envs|ui-envs)/", path)
        if m and method == "GET":
            return ok([])
        if m and method == "POST":
            return ok({"id": 1 if m.group(1) == "api-envs" else 2, "name": body["name"]}, 201)

        for resource in ("requirements", "test-cases", "api-cases", "ui-cases", "test-plans"):
            if path == f"/api/v1/projects/1/{resource}/" and method == "POST":
                return ok({"id": self.new_id()}, 201)

        if re.fullmatch(r"/api/v1/projects/1/test-plans/\d+/cycles/", path) and method == "POST":
            return ok({"id": 1}, 201)

        if path == "/api/v1/projects/1/cycles/1/items" and method == "POST":
            items = []
            for case_id in body.get("case_ids", []):
                items.append({"id": self.new_id(), "asset_type": "functional", "asset_id": case_id})
            for asset in body.get("assets", []):
                items.append(
                    {"id": self.new_id(), "asset_type": asset["asset_type"], "asset_id": asset["asset_id"]}
                )
            self.items = items
            for item in items:
                self.result[item["id"]] = "untested"
            return ok(items, 201)

        if path == "/api/v1/projects/1/cycles/1/items/assign":
            return ok([])

        m = re.fullmatch(r"/api/v1/projects/1/cycles/1/items/(\d+)/attempts", path)
        if m:
            item_id = int(m.group(1))
            self.attempts.setdefault(item_id, []).append(body["status"])
            if body["status"] in ("passed", "failed", "blocked"):
                self.result[item_id] = body["status"]
            return ok({"attempt_no": len(self.attempts[item_id]), "status": body["status"]}, 201)

        if path == "/api/v1/projects/1/cycles/1/items/" and method == "GET":
            rows = []
            for item in self.items:
                rows.append(
                    {
                        **item,
                        "result": self.result[item["id"]],
                        "attempt_no": len(self.attempts.get(item["id"], [])),
                    }
                )
            return ok({"items": rows, "total": len(rows), "page": 1, "page_size": 100})

        m = re.fullmatch(r"/api/v1/projects/1/defects/from-cycle-item/(\d+)", path)
        if m:
            defect_id = self.new_id()
            self.defects[defect_id] = {"id": defect_id, "category": "product", "status": "open"}
            return ok(self.defects[defect_id], 201)

        m = re.fullmatch(r"/api/v1/projects/1/defects/(\d+)/verify", path)
        if m:
            defect = self.defects[int(m.group(1))]
            defect.update({"status": "resolved", "verification_result": body["result"], "fix_build": body["fix_build"]})
            return ok(dict(defect))

        m = re.fullmatch(r"/api/v1/projects/1/defects/(\d+)", path)
        if m and method == "PATCH":
            defect = self.defects[int(m.group(1))]
            defect["status"] = body["status"]
            return ok(dict(defect))

        if path == "/api/v1/projects/1/reports/cycles/1" and method == "POST":
            total = len(self.items)
            passed = sum(1 for v in self.result.values() if v == "passed")
            failed = sum(1 for v in self.result.values() if v == "failed")
            self.report = {
                "id": 9,
                "summary_text": f"通过 {passed} / 失败 {failed}",
                "snapshot": {
                    "metrics": {
                        "selected_total": total,
                        "passed": passed,
                        "failed": failed,
                        "blocked": 0,
                        "untested": total - passed - failed,
                        "running": 0,
                        "skipped": 0,
                        "pass_rate": passed / (passed + failed) if passed + failed else None,
                        "completion_rate": (passed + failed) / total if total else None,
                        "retried_to_pass": 0,
                        "flaky": 0,
                    },
                    "requirements": [
                        {"requirement_id": 1, "case_total": 2, "blind": failed > 0},
                        {"requirement_id": 2, "case_total": 2, "blind": failed > 0},
                    ],
                    "risks": [{"code": "failed", "message": f"{failed} 项失败", "severity": "high"}],
                },
            }
            return ok({"id": 9, **self.report}, 201)

        if path == "/api/v1/projects/1/reports/9" and method == "GET":
            return ok(self.report)

        return httpx.Response(404, json={"message": f"unhandled {method} {path}"})


def test_runbook_executes_scope_and_evaluates_without_automated_runs():
    backend = FakeBackend()
    client = httpx.Client(
        base_url="http://fake", transport=httpx.MockTransport(backend), trust_env=False
    )
    config = _config()
    result = acceptance.run(config, client=client)

    checks = {c["name"]: c for c in result["evaluation"]["checks"]}
    assert result["counts"] == {
        "items": 10,
        "manual_recorded": 4,
        "api_runs": 0,
        "ui_runs": 0,
        "defects": 2,
    }
    # Manual results: 75% of 4 = 3 passed, 1 failed -> one defect per failed item (limit 2).
    assert checks["scope_total"]["passed"] is True
    assert checks["manual_recorded"]["passed"] is True
    assert checks["known_defects_closed"]["passed"] is True
    assert checks["requirement_coverage"]["passed"] is True
    assert checks["report_snapshot"]["passed"] is True
    # Automated items were skipped on purpose: the run must be reported as incomplete.
    assert checks["automated_executed"]["passed"] is False
    assert result["status"] == "incomplete"


def test_evaluation_flags_wrong_scope_and_missing_coverage():
    snapshot = {
        "metrics": {
            "selected_total": 5,
            "passed": 1,
            "failed": 0,
            "blocked": 0,
            "untested": 4,
            "running": 0,
            "skipped": 0,
        },
        "requirements": [{"requirement_id": 1, "case_total": 0}],
        "risks": [],
    }
    outcome = acceptance.evaluate(
        snapshot=snapshot,
        items=[],
        defects=[],
        config=_config(run_api=True, run_ui=True),
        api_runs=[],
        ui_runs=[],
        manual=[],
    )
    failed = {c["name"] for c in outcome["checks"] if not c["passed"]}
    assert {"scope_total", "manual_recorded", "automated_executed", "requirement_coverage"} <= failed
    assert outcome["status"] == "incomplete"
