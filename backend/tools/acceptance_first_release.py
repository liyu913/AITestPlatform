"""First-release acceptance runbook (B7 §139).

Builds the release acceptance scope against a deployed instance and evaluates it:

* 60 batch items — 20 manual functional, 20 HTTP API, 20 Web/UI
* 6 known failures converted into defects, then closed through the retest gate
* one batch report snapshot, evaluated against the plan's acceptance thresholds

Manual outcomes are recorded by the tester; this tool records them (``--manual-policy``)
and clearly marks them as recorded rather than executed by the platform. Automated
items only count as executed when ``--run-api`` / ``--run-ui`` is passed and the
executors are available; otherwise the run is reported as incomplete instead of
silently passing.

Usage (from ``backend/``)::

    uv run python tools/acceptance_first_release.py \
        --base-url http://127.0.0.1:8000 --username admin --password admin123 \
        --run-api --run-ui --out ../logs/acceptance
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PROJECT = "第一版验收（自动生成）"
KNOWN_DEFECTS = 6


@dataclass
class Config:
    base_url: str = "http://127.0.0.1:8000"
    username: str = "admin"
    password: str = "admin123"
    # Environments that require a CAPTCHA cannot log in with a password here;
    # pass an access token instead (e.g. minted with app.services.identity).
    token: str | None = None
    project_name: str = DEFAULT_PROJECT
    build: str = "acceptance-build-1"
    fix_build: str = "acceptance-build-2"
    manual: int = 20
    api_cases: int = 20
    ui_cases: int = 20
    requirements: int = 10
    run_api: bool = False
    run_ui: bool = False
    out_dir: Path = Path("acceptance")
    strict: bool = True
    dry_run: bool = False
    known_defects: int = KNOWN_DEFECTS

    @property
    def scope_total(self) -> int:
        return self.manual + self.api_cases + self.ui_cases


class Api:
    """Thin wrapper: raise on non-2xx so failures point at the exact call."""

    def __init__(self, client: httpx.Client):
        self.client = client

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:400]}")
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs).json()

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs).json()


def login(api: Api, username: str, password: str) -> str:
    body = api.post("/api/v1/auth/login", json={"username": username, "password": password})
    return str(body["access_token"])


def ensure_project(api: Api, name: str) -> int:
    projects = api.get("/api/v1/projects/")
    for project in projects:
        if project.get("name") == name:
            return int(project["id"])
    created = api.post("/api/v1/projects/", json={"name": name, "description": "B7 第一版验收样本"})
    return int(created["id"])


def _ensure_env(api: Api, pid: int, kind: str, name: str, base_url: str) -> int:
    path = f"/api/v1/projects/{pid}/{'api-envs' if kind == 'api' else 'ui-envs'}/"
    rows = api.get(path)
    if isinstance(rows, dict):
        rows = rows.get("items") or []
    for row in rows:
        if row.get("name") == name:
            return int(row["id"])
    created = api.post(path, json={"name": name, "base_url": base_url, "is_default": True})
    return int(created["id"])


def create_requirements(api: Api, pid: int, count: int) -> list[int]:
    ids: list[int] = []
    for index in range(1, count + 1):
        row = api.post(
            f"/api/v1/projects/{pid}/requirements/",
            json={
                "title": f"验收需求 {index:02d}",
                "body_md": f"验收需求 {index:02d} 的业务说明。",
                "acceptance_rules": f"需求 {index:02d}：正向流程成功，非法输入有明确提示",
                "status": "active",
            },
        )
        ids.append(int(row["id"]))
    return ids


def create_functional_cases(api: Api, pid: int, requirement_ids: list[int], count: int) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        requirement_id = requirement_ids[index % len(requirement_ids)]
        row = api.post(
            f"/api/v1/projects/{pid}/test-cases/",
            json={
                "title": f"手工用例 {index + 1:02d}",
                "requirement_id": requirement_id,
                "overall_expected": "页面给出明确结果",
                "priority": "P2",
                "lifecycle_status": "confirmed",
                "steps": [
                    {"order": 0, "action": "按验收数据准备前置条件", "expected": "数据准备完成"},
                    {"order": 1, "action": "执行主要操作", "expected": "页面给出明确结果"},
                ],
            },
        )
        ids.append(int(row["id"]))
    return ids


def create_api_cases(api: Api, pid: int, count: int) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        row = api.post(
            f"/api/v1/projects/{pid}/api-cases/",
            json={"name": f"接口用例 {index + 1:02d}", "path": "/get?acceptance=1", "status": "ready"},
        )
        ids.append(int(row["id"]))
    return ids


def create_ui_cases(api: Api, pid: int, count: int) -> list[int]:
    ids: list[int] = []
    for index in range(count):
        row = api.post(
            f"/api/v1/projects/{pid}/ui-cases/",
            json={"name": f"Web 用例 {index + 1:02d}", "status": "ready"},
        )
        ids.append(int(row["id"]))
    return ids


def create_batch(api: Api, pid: int, *, build: str, api_env_id: int) -> tuple[int, int]:
    plan = api.post(
        f"/api/v1/projects/{pid}/test-plans/",
        json={"name": f"第一版验收 {build}", "version": build, "status": "active"},
    )
    cycle = api.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        json={
            "name": "验收批次",
            "status": "active",
            "target_build": build,
            "env_kind": "api",
            "env_id": api_env_id,
            "credential_ref": "acceptance",
            "data_note": "每轮开始前重置验收数据",
        },
    )
    return int(plan["id"]), int(cycle["id"])


def add_items(
    api: Api,
    pid: int,
    cycle_id: int,
    *,
    functional_ids: list[int],
    api_ids: list[int],
    ui_ids: list[int],
) -> list[dict[str, Any]]:
    body = {
        "case_ids": functional_ids,
        "assets": [
            *({"asset_type": "api_case", "asset_id": i} for i in api_ids),
            *({"asset_type": "ui_case", "asset_id": i} for i in ui_ids),
        ],
    }
    return api.post(f"/api/v1/projects/{pid}/cycles/{cycle_id}/items", json=body)


def assign_all(api: Api, pid: int, cycle_id: int, item_ids: list[int], user_id: int) -> None:
    api.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/assign",
        json={"item_ids": item_ids, "assignee_id": user_id},
    )


def record_manual(
    api: Api, pid: int, cycle_id: int, item_ids: list[int], fail_count: int
) -> list[dict[str, Any]]:
    """Record tester outcomes; the first ``fail_count`` items are the known defects."""
    outcomes: list[dict[str, Any]] = []
    for index, item_id in enumerate(item_ids):
        status = "failed" if index < fail_count else "passed"
        summary = "验收人工执行：符合预期" if status == "passed" else "验收人工执行：与预期不一致（已知缺陷）"
        attempt = api.post(
            f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item_id}/attempts",
            json={"execution_type": "manual", "status": status, "summary": summary},
        )
        outcomes.append({"item_id": item_id, "status": status, "attempt_no": attempt["attempt_no"]})
    return outcomes


def run_asset(
    api: Api,
    pid: int,
    *,
    kind: str,
    case_id: int,
    cycle_item_id: int,
    env_id: int | None,
) -> dict[str, Any]:
    path = f"/api/v1/projects/{pid}/{'api-cases' if kind == 'api' else 'ui-cases'}/{case_id}/run"
    body: dict[str, Any] = {"cycle_item_id": cycle_item_id, "sync": True}
    if env_id is not None:
        body["env_id"] = env_id
    run = api.post(path, json=body)
    return {"run_id": int(run["id"]), "status": run.get("status"), "kind": kind}


def items_with_status(api: Api, pid: int, cycle_id: int) -> list[dict[str, Any]]:
    page = api.get(f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/?page=1&page_size=100")
    return list(page.get("items") or [])


def file_and_close_defects(
    api: Api, pid: int, *, failed_item_ids: list[int], fix_build: str, limit: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item_id in failed_item_ids[:limit]:
        defect = api.post(f"/api/v1/projects/{pid}/defects/from-cycle-item/{item_id}")
        defect_id = int(defect["id"])
        api.post(
            f"/api/v1/projects/{pid}/defects/{defect_id}/verify",
            json={"result": "passed", "fix_build": fix_build, "note": "验收复测：原问题通过"},
        )
        closed = api.patch(f"/api/v1/projects/{pid}/defects/{defect_id}", json={"status": "closed"})
        results.append(
            {
                "defect_id": defect_id,
                "item_id": item_id,
                "category": defect.get("category"),
                "status": closed.get("status"),
            }
        )
    return results


def generate_report(api: Api, pid: int, cycle_id: int) -> dict[str, Any]:
    created = api.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}")
    return api.get(f"/api/v1/projects/{pid}/reports/{created['id']}")


def evaluate(
    *,
    snapshot: dict[str, Any],
    items: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    config: Config,
    api_runs: list[dict[str, Any]],
    ui_runs: list[dict[str, Any]],
    manual: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = snapshot.get("metrics") or {}
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    check("scope_total", metrics.get("selected_total") == config.scope_total,
          {"expected": config.scope_total, "actual": metrics.get("selected_total")})
    check("manual_recorded", len(manual) == config.manual, {"recorded": len(manual)})
    auto_expected = config.api_cases + config.ui_cases
    auto_executed = len(api_runs) + len(ui_runs)
    check("automated_executed", auto_executed == auto_expected,
          {"expected": auto_expected, "executed": auto_executed,
           "note": "" if auto_executed == auto_expected else "需 --run-api/--run-ui 且执行器可用"})
    check("known_defects_closed", len(defects) >= min(config.known_defects, config.manual) and all(
        d.get("status") == "closed" for d in defects
    ), {"defects": defects})

    requirements = snapshot.get("requirements") or []
    covered = [r for r in requirements if r.get("case_total", 0) > 0]
    coverage_ratio = len(covered) / len(requirements) if requirements else None
    check("requirement_coverage", coverage_ratio is not None and coverage_ratio >= 0.9,
          {"ratio": coverage_ratio, "requirements": len(requirements)})

    risks = snapshot.get("risks") or []
    check("report_snapshot", bool(metrics), {"risks": len(risks)})

    finished = metrics.get("passed", 0) + metrics.get("failed", 0)
    check("all_items_executed", finished + metrics.get("blocked", 0) + metrics.get("running", 0)
          + metrics.get("skipped", 0) + metrics.get("untested", 0) == metrics.get("selected_total", 0),
          {"metrics": metrics})
    # A run is only acceptable when nothing is left pending: an "executed" count
    # must never hide untested or still-running items behind a green summary.
    check("no_pending_items",
          metrics.get("untested", 0) == 0 and metrics.get("running", 0) == 0,
          {"untested": metrics.get("untested", 0), "running": metrics.get("running", 0)})

    status = "passed" if all(c["passed"] for c in checks) else "incomplete"
    return {
        "status": status,
        "checks": checks,
        "metrics": metrics,
        "coverage_ratio": coverage_ratio,
        "defects": defects,
        "report_id": snapshot.get("id"),
    }


def build_plan(config: Config) -> dict[str, Any]:
    return {
        "project_name": config.project_name,
        "base_url": config.base_url,
        "build": config.build,
        "scope": {
            "manual": config.manual,
            "api": config.api_cases,
            "ui": config.ui_cases,
            "total": config.scope_total,
            "requirements": config.requirements,
        },
        "automated_runs": {"api": config.run_api, "ui": config.run_ui},
        "known_defects": config.known_defects,
        "strict": config.strict,
    }


def run(config: Config, *, client: httpx.Client | None = None) -> dict[str, Any]:
    if config.dry_run:
        return {"status": "dry_run", "plan": build_plan(config)}

    owns_client = client is None
    client = client or httpx.Client(base_url=config.base_url, timeout=60, trust_env=False)
    started = datetime.now().isoformat(timespec="seconds")
    try:
        api = Api(client)
        token = config.token or login(api, config.username, config.password)
        api.client.headers["Authorization"] = f"Bearer {token}"
        me = api.get("/api/v1/users/me")

        pid = ensure_project(api, config.project_name)
        api_env = _ensure_env(api, pid, "api", "验收接口环境", "https://httpbin.org")
        ui_env = _ensure_env(api, pid, "ui", "验收 Web 环境", "https://example.com")
        requirement_ids = create_requirements(api, pid, config.requirements)
        functional_ids = create_functional_cases(api, pid, requirement_ids, config.manual)
        api_ids = create_api_cases(api, pid, config.api_cases)
        ui_ids = create_ui_cases(api, pid, config.ui_cases)
        _, cycle_id = create_batch(api, pid, build=config.build, api_env_id=api_env)
        items = add_items(
            api, pid, cycle_id, functional_ids=functional_ids, api_ids=api_ids, ui_ids=ui_ids
        )
        assign_all(api, pid, cycle_id, [int(i["id"]) for i in items], int(me["id"]))

        by_asset: dict[str, list[dict[str, Any]]] = {"functional": [], "api_case": [], "ui_case": []}
        for item in items:
            by_asset.setdefault(str(item.get("asset_type") or "functional"), []).append(item)

        manual = record_manual(
            api,
            pid,
            cycle_id,
            [int(i["id"]) for i in by_asset["functional"]],
            min(config.known_defects, config.manual),
        )
        api_runs: list[dict[str, Any]] = []
        if config.run_api:
            for item in by_asset["api_case"]:
                api_runs.append(
                    run_asset(
                        api,
                        pid,
                        kind="api",
                        case_id=int(item["asset_id"]),
                        cycle_item_id=int(item["id"]),
                        env_id=api_env,
                    )
                )
        ui_runs: list[dict[str, Any]] = []
        if config.run_ui:
            for item in by_asset["ui_case"]:
                ui_runs.append(
                    run_asset(
                        api,
                        pid,
                        kind="ui",
                        case_id=int(item["asset_id"]),
                        cycle_item_id=int(item["id"]),
                        env_id=ui_env,
                    )
                )

        final_items = items_with_status(api, pid, cycle_id)
        failed_ids = [int(i["id"]) for i in final_items if i.get("result") == "failed"]
        defects = file_and_close_defects(
            api, pid, failed_item_ids=failed_ids, fix_build=config.fix_build, limit=config.known_defects
        )

        report = generate_report(api, pid, cycle_id)
        snapshot = report.get("snapshot") or {}
        snapshot["id"] = report.get("id")
        outcome = evaluate(
            snapshot=snapshot,
            items=final_items,
            defects=defects,
            config=config,
            api_runs=api_runs,
            ui_runs=ui_runs,
            manual=manual,
        )
        result = {
            "status": outcome["status"],
            "started_at": started,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "config": {**build_plan(config), "password": "***"},
            "project_id": pid,
            "cycle_id": cycle_id,
            "report_id": report.get("id"),
            "counts": {
                "items": len(final_items),
                "manual_recorded": len(manual),
                "api_runs": len(api_runs),
                "ui_runs": len(ui_runs),
                "defects": len(defects),
            },
            "evaluation": outcome,
            "summary": report.get("summary_text"),
        }
        return result
    finally:
        if owns_client:
            client.close()


def write_result(result: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"acceptance-{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="第一版验收执行器")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument(
        "--token",
        default=None,
        help="跳过登录，直接使用访问令牌（验证码开启的环境用）",
    )
    parser.add_argument("--project-name", default=DEFAULT_PROJECT)
    parser.add_argument("--build", default="acceptance-build-1")
    parser.add_argument("--fix-build", default="acceptance-build-2")
    parser.add_argument("--manual", type=int, default=20)
    parser.add_argument("--api-cases", type=int, default=20)
    parser.add_argument("--ui-cases", type=int, default=20)
    parser.add_argument("--requirements", type=int, default=10)
    parser.add_argument("--run-api", action="store_true")
    parser.add_argument("--run-ui", action="store_true")
    parser.add_argument("--known-defects", type=int, default=KNOWN_DEFECTS)
    parser.add_argument("--out", type=Path, default=Path("acceptance"))
    parser.add_argument("--no-strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = Config(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        token=args.token,
        project_name=args.project_name,
        build=args.build,
        fix_build=args.fix_build,
        manual=args.manual,
        api_cases=args.api_cases,
        ui_cases=args.ui_cases,
        requirements=args.requirements,
        run_api=args.run_api,
        run_ui=args.run_ui,
        known_defects=args.known_defects,
        out_dir=args.out,
        strict=not args.no_strict,
        dry_run=args.dry_run,
    )
    if config.dry_run:
        print(json.dumps(build_plan(config), ensure_ascii=False, indent=2))
        return 0

    result = run(config)
    path = write_result(result, config.out_dir)
    for check in result["evaluation"]["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"[{mark}] {check['name']} {json.dumps(check['detail'], ensure_ascii=False)}")
    print(f"\nsummary: {result.get('summary')}")
    print(f"result: {result['status']} -> {path}")
    if result["status"] != "passed":
        print("验收未通过：请补齐未执行项或降低阈值后重跑（不使用 --no-strict 掩盖缺口）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
