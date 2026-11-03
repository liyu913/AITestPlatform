"""Preset multi-dimension catalog for AI test-case review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.ai.ai_gen import ReviewDimensionConfig

DEFAULT_WEIGHT = 3
MIN_WEIGHT = 1
MAX_WEIGHT = 5


@dataclass(frozen=True)
class ReviewDimensionDef:
    key: str
    title: str
    guideline: str


REVIEW_DIMENSIONS: tuple[ReviewDimensionDef, ...] = (
    ReviewDimensionDef(
        "coverage",
        "覆盖完整性",
        "相对测试点/需求是否遗漏关键场景，覆盖是否完整",
    ),
    ReviewDimensionDef(
        "correctness",
        "正确性",
        "步骤与总体期望是否合理、可执行、与业务一致",
    ),
    ReviewDimensionDef(
        "redundancy",
        "冗余去重",
        "是否存在可合并的重复或高度相似用例",
    ),
    ReviewDimensionDef(
        "boundary",
        "边界与异常",
        "边界值、异常与负面路径是否充分",
    ),
    ReviewDimensionDef(
        "clarity",
        "步骤清晰度",
        "标题与步骤是否清晰、无歧义、可独立执行",
    ),
    ReviewDimensionDef(
        "priority",
        "优先级合理性",
        "P0–P4 是否与风险/影响匹配",
    ),
    ReviewDimensionDef(
        "precondition",
        "前置/数据完整性",
        "前置条件与测试数据是否齐全、可复现",
    ),
    ReviewDimensionDef(
        "traceability",
        "需求追溯",
        "标题/模块是否与需求或测试点可追溯对齐",
    ),
)

REVIEW_DIM_BY_KEY: dict[str, ReviewDimensionDef] = {d.key: d for d in REVIEW_DIMENSIONS}
REVIEW_DIM_KEYS: frozenset[str] = frozenset(REVIEW_DIM_BY_KEY)


def clamp_weight(weight: int | None) -> int:
    if weight is None:
        return DEFAULT_WEIGHT
    return max(MIN_WEIGHT, min(MAX_WEIGHT, int(weight)))


def normalize_review_dims(
    configs: list[ReviewDimensionConfig] | None,
    *,
    enable: bool,
) -> list[ReviewDimensionConfig]:
    """Return selected dims; when enabled and empty, use all with default weight."""
    from app.schemas.ai.ai_gen import ReviewDimensionConfig as DimCfg

    if not enable:
        return []
    if not configs:
        return [DimCfg(key=d.key, weight=DEFAULT_WEIGHT) for d in REVIEW_DIMENSIONS]
    seen: set[str] = set()
    out: list[DimCfg] = []
    for item in configs:
        if item.key in seen:
            continue
        seen.add(item.key)
        out.append(DimCfg(key=item.key, weight=clamp_weight(item.weight)))
    return out or [DimCfg(key=d.key, weight=DEFAULT_WEIGHT) for d in REVIEW_DIMENSIONS]


def build_review_checklist_text(dims: list[ReviewDimensionConfig]) -> str:
    lines: list[str] = []
    for cfg in dims:
        meta = REVIEW_DIM_BY_KEY[cfg.key]
        lines.append(
            f"- {meta.key}（{meta.title}，权重 {cfg.weight}/5）：{meta.guideline}"
        )
    return "\n".join(lines)


def issue_type_keys_csv(dims: list[ReviewDimensionConfig]) -> str:
    return "、".join(cfg.key for cfg in dims)