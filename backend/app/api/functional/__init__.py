from app.api.functional.coverage import router as coverage_router
from app.api.functional.defect_analyses import router as defect_analyses_router
from app.api.functional.defects import router as defects_router
from app.api.functional.reports import router as reports_router
from app.api.functional.schemes import router as schemes_router
from app.api.functional.requirements import router as requirements_router
from app.api.functional.test_plans import router as test_plans_router
from app.api.functional.testcase_runs import router as testcase_runs_router
from app.api.functional.testcases import router as testcases_router

routers = [
    requirements_router,
    testcases_router,
    testcase_runs_router,
    test_plans_router,
    defect_analyses_router,
    defects_router,
    reports_router,
    schemes_router,
    coverage_router,
]
