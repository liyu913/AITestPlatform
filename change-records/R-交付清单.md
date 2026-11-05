# R 版交付清单（R0–R6）

记录时间：2026-11-05。本文是 R0–R6 改造的交付总览：范围、代码地图、迁移、验证证据、剩余风险。

> 历史 B 版清单见 [第一版交付清单](./第一版交付清单.md)，B 版发布说明见 [B7 发布说明](./B7-兼容验证与发布.md)。

## 1. 交付范围

| 批次 | 内容 | 记录 | 状态 |
| --- | --- | --- | --- |
| R0 | 四类关系契约：方案-计划、评审版本-正式库、执行证据-问题、缺陷-回归报告 | [R1-方案与发布规则](./R1-方案与发布规则.md) | 完成 |
| R1 | 测试方案与版本、计划引用方案版本、正式发布指针、独立评审队列 | 同上 | 完成 |
| R2 | Skill 版本目录与导入、项目动作绑定、四级解析、生成链路接入、调用记录快照 | [R2-Skill与动作绑定](./R2-Skill与动作绑定.md) | 完成 |
| R3 | 批次就绪检查与开始闸门、运行入口校验、评审指派、设计任务与关联区 | [R3-就绪检查与评审指派](./R3-就绪检查与评审指派.md) | 完成 |
| R4 | 缺陷分析记录、转缺陷、待提交视图、提交尝试与幂等 | [R4-缺陷分析与提交](./R4-缺陷分析与提交.md) | 完成 |
| R5 | 一键回归批次、报告叙述与配置快照 | [R5-回归任务与报告叙述](./R5-回归任务与报告叙述.md) | 完成 |
| R6 | 升级/备份/权限回归、200 次执行窗口、Web 验收、证据检索、发布清单 | [R6-兼容与发布验收](./R6-兼容与发布验收.md) | 完成（2 项环境阻塞） |

## 2. 代码地图（新增/主要改动）

### 后端

| 模块 | 文件 |
| --- | --- |
| 测试方案与版本 | `models/functional/scheme.py`、`services/functional/scheme_service.py`、`api/functional/schemes.py` |
| 用例发布指针/评审指派 | `models/functional/testcase.py`、`api/functional/testcases.py` |
| 批次就绪闸门 | `services/functional/cycle_readiness.py`、`api/functional/test_plans.py` |
| Skill 与动作绑定 | `models/ai/ai_config.py`、`services/ai/ai_config.py`、`api/ai/ai_config.py` |
| 生成链路接入 | `api/ai/ai_generate.py`、`services/ai/effective_ai_settings.py`、`services/ai/call_log.py` |
| 缺陷分析与提交 | `models/functional/defect_analysis.py`、`services/functional/defect_analysis.py`、`api/functional/defect_analyses.py` |
| 回归任务/报告叙述 | `services/functional/defect_workflow.py`、`api/functional/defects.py`、`api/functional/reports.py` |
| 证据检索 | `api/project/evidence.py`、`schemas/project/evidence.py` |

### 前端

| 页面 | 文件 |
| --- | --- |
| 测试方案 | `routes/SchemeList.tsx` |
| 用例评审队列 | `routes/ReviewQueue.tsx` |
| AI 与 Skill 配置 + 设计任务 | `routes/AiConfigPage.tsx` |
| Skill 版本管理 | `routes/AdminSkillsPage.tsx` |
| 缺陷分析 / 待提交 | `routes/DefectAnalysisPage.tsx` |
| 执行证据 | `routes/EvidencePage.tsx` |
| 轮次就绪卡片、发布入口、评审弹窗 | `routes/TestCycleDetail.tsx`、`routes/TestCaseList.tsx` |
| 报告叙述 | `routes/ReportDetail.tsx` |
| 导航/路由 | `routes/domainNav.ts`、`App.tsx` |

## 3. 迁移链

```
91de45b6c284（B 基线）
→ b2c3d4e5f6g7 → c3d4e5f6g7h8 → d4e5f6g7h8i9 → e5f6g7h8i9j0 → f6g7h8i9j0k1（B0–B7）
→ g7h8i9j0k1l2（R1 方案/发布指针）
→ h8i9j0k1l2m3（R2 Skill/绑定/调用配置）
→ i9j0k1l2m3n4（R3 就绪快照/评审指派）
→ j0k1l2m3n4o5（R4 分析/提交）
→ k1l2m3n4o5p6（R5 报告叙述）
```

升级：`docker compose exec backend alembic upgrade head`；回退：逐个 `alembic downgrade -1` 或回退到 `91de45b6c284` 后回滚镜像（见 B7 发布说明）。

## 4. 验证证据

| 项 | 结果 |
| --- | --- |
| 后端全量测试 | **462 passed / 6 skipped / 0 failed** |
| 前端构建 | `tsc -b` + `vite build` 通过 |
| 迁移链（SQLite 升级/回退） | 通过 |
| 真实 MySQL 升级 | 通过（head `k1l2m3n4o5p6`） |
| 备份 + scratch 恢复 | 15 张表行数一致 |
| 权限/跨项目隔离 | 通过（测试覆盖） |
| 200 次 API 执行 | 200/200 通过，一致率 100%，0 不稳定 |
| Web 执行 | 5/5 登录页 UI 用例通过 |
| 验收执行器（真实 API） | passed（11 项、缺陷闭环、报告 100%） |
| 数据模式执行器 | incomplete（未执行自动项被正确拦截） |

截图与产物：`logs/acceptance/r6-200-runs.json`、`logs/r1-*`、`logs/r2-*`、`logs/r3-*`、`logs/r4-*`、`logs/r5-*`、`logs/r6-*`。

## 5. 剩余风险（环境依赖）

| 项 | 影响 | 解除条件 |
| --- | --- | --- |
| 30 条标注需求生成质量 | 无法给出 ≥90% 的生成质量结论 | 可用模型网关 + 标注样本 |
| 完整镜像重建 | 当前部署使用 overlay 镜像，非正式构建产物 | Docker 代理/镜像源可用 |
| 浏览器人工确认 | 主题、弹窗、抽屉交互未人工验收 | 人工过一遍关键路径 |
| 业务 UI 用例 | Web 验收仅覆盖平台登录页 | 提供示例业务 UI 脚本 |

## 6. 发布建议

1. 将当前工作区作为一次发布提交（`app/` 仓库），建议 tag `v1.1.0`（R0–R6 + 主题/Logo/按钮）；
2. 在可访问镜像源的环境执行 `scripts/verify-release.sh` 与 `manage.sh rebuild`，确认镜像构建；
3. 预发库执行备份 → `alembic upgrade head` → 冒烟 → 验收执行器；
4. 用 `backup-restore-drill.sh restore <dir> --into-scratch` 再做一次演练；
5. 按 R6 检查清单完成剩余环境项后放行。
