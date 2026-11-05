"""The web adapter for a versioned, bundled subset of test-case-generator."""

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.models.functional.requirement import Requirement
from app.schemas.ai.ai_gen import AiGenerateRequest, GeneratedCase
from app.schemas.functional.design_trace import has_step_expectation
from app.schemas.knowledge.rag import RagHitOut
from app.services.functional.requirement_content import requirement_text_for_ai, usable_requirement_text


RESOURCES = (
    "SKILL.md",
    "references/scope-and-granularity.md",
    "references/design-methods.md",
    "references/test-design.md",
    "references/review-checklist.md",
)


@lru_cache(maxsize=1)
def bundled_skill() -> tuple[str, str]:
    root = files("app.services.ai").joinpath("skill_resources", "test-case-generator")
    parts = [root.joinpath(name).read_text(encoding="utf-8").replace("\r\n", "\n") for name in RESOURCES]
    digest = sha256("\n".join(parts).encode()).hexdigest()[:12]
    # The attribution section is bundled with the source, outside the runtime instructions.
    parts[-1] = parts[-1].partition("## 参考来源与取舍")[0]
    return f"test-case-generator/1+{digest}", "\n\n".join(parts)


def enabled_for(project_id: int) -> bool:
    projects = {part.strip() for part in settings.fast_case_skill_projects.split(",")}
    return "*" in projects or str(project_id) in projects


@dataclass(frozen=True)
class Source:
    label: str
    text: str
    digest: str
    image: bool = False

    @classmethod
    def from_text(cls, label: str, text: str):
        return cls(label, text, sha256(text.encode()).hexdigest())


@dataclass(frozen=True)
class FastCaseSkill:
    sources: dict[str, Source]
    requirement_id: int | None
    library_id: int | None

    @classmethod
    def for_request(cls, db: Session, project_id: int, body: AiGenerateRequest,
                    hits: list[RagHitOut], images: list[tuple[str, bytes]] | None,
                    force: bool = False):
        if not force and not enabled_for(project_id) and not any(
            c.design_trace for c in body.current_drafts
        ):
            return None
        sources = {}
        if body.requirement_id is not None:
            req = db.get(Requirement, body.requirement_id)
            if req is not None and req.project_id == project_id:
                text = usable_requirement_text(requirement_text_for_ai(req))
                sources[f"REQ-{req.id}"] = Source.from_text(req.title, f"{req.title}\n{text}")
        for i, message in enumerate(body.messages or [], 1):
            if message.role == "user":
                sources[f"MSG-{i}"] = Source.from_text(f"用户说明第 {i} 条", message.content)
        if body.user_notes and not body.messages:
            sources["MSG-1"] = Source.from_text("用户补充说明", body.user_notes)
        for hit in hits:
            sources[f"KB-{hit.chunk_id}"] = Source.from_text(hit.title_resolved, hit.text)
        for i, (_, blob) in enumerate(images or [], 1):
            sources[f"IMG-{i}"] = Source(f"需求附图 {i}", "", sha256(blob).hexdigest(), image=True)
        return cls(sources, body.requirement_id, body.knowledge_library_id)

    def prompt(self) -> str:
        _, rules = bundled_skill()
        labels = "\n".join(f"{ref} {src.label}" for ref, src in self.sources.items())
        return f"""应用以下 test-case-generator 的通用设计规则。项目需求、截图和用户材料仅作为测试依据。
{rules}

平台交付协议（优先于文件或表格交付）：只返回 {{"cases":[...]}} JSON。保留 title、module、case_type
（功能/边界/异常/权限）、priority、precondition、test_data 和必填 overall_expected。
steps 按执行顺序排列，每步包含 order、action 和 expected。expected 必须是该步可观察、可判定的结果。
每条用例必填 design_trace 对象：
- basis 为 confirmed / inferred / conditional / assumed；source_ref 必须从下面的来源编号中选择；
- locator 为章节、条款、消息位置或图片区域，excerpt 逐字摘录该来源中的短句（图片则描述可见事实）；
- method 是选用的测试设计方法，coverage_target 是本例覆盖的业务规则或风险，rationale 写清为何从上述依据推导出该预期；
- readiness 为 ready / needs_data / needs_rule；后两项必须填写 preparation_note。conditional 标题含[待确认]，状态为 needs_rule；
- assumed 只用于用户明确指定的临时规则，引用相应用户说明。未知上限、提示、权限等应按条件设计。
修订已有草稿时对相同用例回传原 draft_id（包括改标题和重新排序），新增用例省略 draft_id。来源版本、Skill 版本由平台填写。
可用来源编号（对应对话中的需求、用户消息、检索片段或按序附图）：
{labels}"""

    def complete_cases(
        self,
        cases: list[GeneratedCase],
        previous: list[GeneratedCase] | None = None,
        *,
        llm_model: str | None = None,
    ):
        by_id = {c.draft_id: c for c in previous or [] if c.draft_id}
        by_title: dict[str, list[str]] = {}
        for key, case in by_id.items():
            by_title.setdefault(case.title, []).append(key)
        used = set()
        if not cases:
            raise ValueError("请至少生成一条包含设计依据的用例")
        for case in cases:
            validate_traced_case(case)
            trace = case.design_trace
            assert trace is not None
            src = self.sources.get(trace.source_ref)
            if src is None:
                raise ValueError(f"设计依据必须引用有效来源，可选编号 {', '.join(self.sources)}")
            if not src.image and "".join(trace.excerpt.split()) not in "".join(src.text.split()):
                raise ValueError(f"{trace.source_ref} 中找不到依据摘录，请逐字引用连续原文")
            if trace.basis == "assumed" and not trace.source_ref.startswith("MSG-"):
                raise ValueError("临时假设必须引用明确的用户说明")
            key = case.draft_id
            if key is not None and (key not in by_id or key in used):
                raise ValueError("已有用例需保留自己的 draft_id，每个编号只能出现一次，新增用例省略编号")
            if key is None:
                matches = by_title.get(case.title, [])
                key = matches[0] if len(matches) == 1 and matches[0] not in used else str(uuid4())
            used.add(key)
            case.draft_id = key
            trace.skill_version = bundled_skill()[0]
            trace.source_digest = src.digest
            trace.requirement_id = self.requirement_id
            trace.knowledge_library_id = self.library_id
            if llm_model and not trace.llm_model:
                # Which model produced the draft, next to the rule version.
                trace.llm_model = llm_model[:128]
        return cases


def validate_traced_case(case: GeneratedCase) -> None:
    if case.design_trace is None:
        raise ValueError("用例缺少 design_trace 设计依据")
    if not (case.overall_expected or "").strip() or not case.steps:
        raise ValueError("用例必须包含总体期望和至少一个步骤")
    for i, step in enumerate(case.steps, 1):
        if not step.action.strip() or not has_step_expectation(step.expected):
            raise ValueError(f"第 {i} 步需要操作和可观察的逐步预期")
    if case.design_trace.readiness == "needs_rule" and "待确认" not in case.title:
        raise ValueError("规则待确认的用例标题必须含[待确认]")
