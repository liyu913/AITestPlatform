# R2：Skill 目录、动作绑定与配置解析

记录时间：2026-11-05。新版计划 R2 的第一阶段：把「按动作选择 Skill/模型」的数据、解析与配置页面落地。

## 1. 数据

迁移 `h8i9j0k1l2m3`（revises `g7h8i9j0k1l2`）：

| 表/列 | 说明 |
| --- | --- |
| `ai_skill_versions` | Skill 版本目录：`skill_key`、名称、适用动作、版本、描述、输入/输出契约、资源正文、工具、启用状态；`(skill_key, version)` 唯一 |
| `project_ai_action_bindings` | 项目内动作绑定：`(project_id, action)` 唯一，保存调用方式、Skill 版本、模型档案、扩展参数 |
| `project_ai_defaults` | 项目默认模型档案（每项目一条） |
| `ai_call_logs` 新列 | `action`、`skill_version_id`、`binding_id`、`config_source`、`source_type/source_id`、`resolved_config` |

迁移内置登记 `test-case-generator`（动作 `case_design`，v1，复用现有用例技能的强制 `design_trace`/每步预期约束）。
若历史库没有 `ai_call_logs` 表，迁移会跳过加列（兼容测试用的精简库）。

## 2. 解析规则

`services/ai/ai_config.resolve` 按计划实现四级覆盖，逐字段合并：

```
本次调用覆盖 → 项目动作绑定 → 项目默认模型 → 系统默认（is_active）
```

- 返回 `mode`、`source`、Skill 名称/版本、模型档案与模型名、参数；
- `source` 标记实际来源：`call / action / project / system`，供界面展示；
- Skill 模式必须选 Skill 版本；Skill 必须启用且属于全局或当前项目，否则 404/409；
- 找不到任何模型时返回 400，并提示先在 AI 设置中配置模型。

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/projects/{id}/ai-config/` | 动作、绑定、默认、可用 Skill 与模型一屏返回 |
| PUT/DELETE | `/projects/{id}/ai-config/bindings/{action}` | 保存或恢复继承 |
| PUT | `/projects/{id}/ai-config/defaults` | 项目默认模型 |
| GET | `/projects/{id}/ai-config/resolve?action=...` | 解析实际生效配置（可带 `mode/skill_version_id/model_profile_id` 覆盖） |
| GET/POST | `/admin/ai-skills/` | 管理员查看/登记 Skill 版本 |

## 4. 前端

新增「设置 → AI 与 Skill」页面：

- 顶部设置项目默认模型；
- 每个动作一行：调用方式（直接模型 / Skill）、Skill 版本（按动作过滤）、模型档案（可留空继承）、保存 / 恢复继承；
- 行内展示实际生效来源与 Skill/模型，数据来自 `/resolve`。

## 5. 验证

- `tests/test_ai_config.py`：系统默认 → 项目默认 → 动作绑定 → 本次覆盖的优先级；Skill 模式缺少 Skill、跨项目 Skill、停用 Skill 的拒绝；未知动作 400。
- 迁移链测试与真实 MySQL 升级 `g7h8i9j0k1l2 → h8i9j0k1l2m3` 通过；内置 Skill 已登记。
- 真机：项目 7 把 `case_design` 绑定到「用例设计（内置）v1」并设置项目默认模型后，
  `resolve` 返回 `source=action, mode=skill`，其他动作返回 `source=project`。
- 前端 `tsc -b` + `vite build` 通过；截图 `logs/r2-ai-config2.png`。

## 6. 遗留（R2 第二阶段 / R3）

| 项 | 说明 |
| --- | --- |
| 生成链路尚未消费解析结果 | 用例生成仍走全局激活模型与固定 Skill；需把 `resolve` 结果传入 LLM 调用与生成记录 |
| 调用记录未写 `resolved_config` 快照 | 列已就绪，待生成链路接入后写入 |
| Skill 导入（SKILL.md 上传）与版本校验 | 目前仅管理员 API 登记 + 内置版本 |
| 页面未展示历史任务使用的配置版本 | 需要设计任务记录关联（R3 设计页） |

## 7. 第二阶段：生成链路接入

- 用例生成请求新增 `ai_mode / skill_version_id / model_profile_id` 覆盖字段；生成前统一走 `resolve`，
  语言模型调用改为使用解析出的模型档案（`runtime_for_profile`），不再固定用全局激活档案。
- 动作绑定为 Skill 模式时，即使项目不在 `FAST_CASE_SKILL_PROJECTS` 中也会强制启用内置用例技能，
  `llm-options` 同步返回 `mode / source / skill_name / skill_version / model_profile_id`。
- 调用日志写入 `action`、`skill_version_id`、`binding_id`、`config_source` 与 `resolved_config` 快照。
- AI 生成页展示「当前生效：动作绑定 · 用例设计（内置）v1 · 模型」。
- 验证：`tests/test_ai_config.py` 新增「options 反映绑定」与「调用日志携带解析配置」用例；
  真机 `llm-options` 返回 `source=action, mode=skill, skill=用例设计（内置）v1`，页面可见。
- 截图：`logs/r2-generate2.png`。

仍待：设计任务（会话）保存配置版本快照、SKILL.md 导入与样例校验、逐动作的提示词契约。

## 8. SKILL.md 导入与版本管理

- `POST /admin/ai-skills/import`（multipart）：
  - 上传 SKILL.md，解析 YAML front matter（name/description），Skill key 缺省取 front matter 或文件名；
  - `skill_key` 校验小写字母/数字/`-`/`_`；版本缺省按 key 自动递增，重复版本 409；
  - 适用动作校验、资源正文入库、工具列表逗号分隔；
  - 非超级管理员 403，文件超过 1MB 400。
- `PATCH /admin/ai-skills/{id}`：启停、改名、改描述。
- 新增「Skill 版本」管理页 `/admin/skills`（从系统 AI 配置进入）：列表、启停、导入弹窗。
- 真机：导入 `review-checker`（review 动作）自动生成 v1，再次导入生成 v2；页面可见 3 个版本。
- 测试：`tests/test_skill_import.py` 覆盖版本递增、重复 409、非法动作/Key 400、非管理员 403、停用 Skill 绑定 409。
- 截图：`logs/r2-skills.png`。

至此 R2 的 Skill 目录、导入、动作绑定、解析、生成链路接入与调用记录闭环完成。
