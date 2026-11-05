from app.models.identity.user import User
from app.models.project.project import Project, ProjectMember, ProjectModule
from app.models.functional.requirement import Requirement, RequirementAttachment, RequirementVersion
from app.models.functional.testcase import (
    TestCase,
    TestCaseReview,
    TestCaseStep,
    TestCaseVersion,
)
from app.models.functional.testcase_run import TestCaseRun
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.knowledge.index_chunk import IndexChunk
from app.models.ai.system_ai_settings import SystemEmbeddingProfile, SystemLlmProfile, SystemMidsceneProfile
from app.models.ai.ai_session import AiGenerationSession
from app.models.ai.ai_call_log import AiCallLog
from app.models.ai.ai_config import (
    AiSkillVersion,
    ProjectAiActionBinding,
    ProjectAiDefault,
)
from app.models.api_automation.api_automation import (
    ApiCase,
    ApiCaseAgentSession,
    ApiCaseEditAgentSession,
    ApiDefinition,
    ApiEnvironment,
    ApiRun,
    ApiRunStep,
    ApiScenarioAgentSession,
    ApiSchedule,
    ApiSpec,
    ApiSpecEndpoint,
    ApiSuite,
)
from app.models.ui_automation.ui_automation import (
    UiAiExploreJob,
    UiCase,
    UiElement,
    UiEnvironment,
    UiRun,
    UiRunStep,
    UiSchedule,
    UiScenarioAgentSession,
    UiSuite,
)
from app.models.app_automation.app_automation import (
    AppCase,
    AppDevice,
    AppElement,
    AppEnvironment,
    AppRun,
    AppRunStep,
)
from app.models.perf.perf_automation import PerfRun, PerfScenario
from app.models.functional.scheme import TestScheme, TestSchemeVersion
from app.models.functional.test_plan import (
    ExecutionAttachment,
    TestCycle,
    TestCycleAttempt,
    TestCycleItem,
    TestCycleReport,
    TestPlan,
)
from app.models.functional.defect import Defect, DefectAttachment, DefectStatusHistory
from app.models.functional.defect_analysis import DefectAnalysis, DefectSubmission

__all__ = [
    "User",
    "Project",
    "ProjectMember",
    "ProjectModule",
    "Requirement",
    "RequirementAttachment",
    "RequirementVersion",
    "TestCase",
    "TestCaseStep",
    "TestCaseVersion",
    "TestCaseReview",
    "TestCaseRun",
    "KnowledgeDocument",
    "KnowledgeLibrary",
    "IndexChunk",
    "SystemLlmProfile",
    "SystemMidsceneProfile",
    "SystemEmbeddingProfile",
    "AiGenerationSession",
    "AiCallLog",
    "AiSkillVersion",
    "ProjectAiActionBinding",
    "ProjectAiDefault",
    "ApiEnvironment",
    "ApiCase",
    "ApiDefinition",
    "ApiSuite",
    "ApiScenarioAgentSession",
    "ApiCaseAgentSession",
    "ApiCaseEditAgentSession",
    "ApiRun",
    "ApiRunStep",
    "ApiSpec",
    "ApiSpecEndpoint",
    "ApiSchedule",
    "UiEnvironment",
    "UiCase",
    "UiElement",
    "UiSuite",
    "UiSchedule",
    "UiRun",
    "UiRunStep",
    "UiAiExploreJob",
    "UiScenarioAgentSession",
    "AppEnvironment",
    "AppDevice",
    "AppElement",
    "AppCase",
    "AppRun",
    "AppRunStep",
    "PerfScenario",
    "PerfRun",
    "TestPlan",
    "TestCycle",
    "TestCycleItem",
    "TestCycleAttempt",
    "TestCycleReport",
    "TestScheme",
    "TestSchemeVersion",
    "ExecutionAttachment",
    "Defect",
    "DefectAttachment",
    "DefectStatusHistory",
    "DefectAnalysis",
    "DefectSubmission",
]
