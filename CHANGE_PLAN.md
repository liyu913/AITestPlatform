## 2026-11-05 — 第一版收口：交付结论与已知限制

- **类型**: docs
- **用户原话摘要**: 完成第一版最终收口
- **诊断或需求理解**:
  - 各批次记录、B7 检查清单与交付清单需要收敛成一份可交付结论
  - 真机验证后已修复 3 个缺陷、完成 4 项实测，需要写清证据链与未完成原因
- **影响范围**:
  - docs: 第一版交付清单新增「交付结论」（证据表、真机修复项、未完成原因、发布建议）
  - docs: B7 已知限制表更新为最终状态
- **步骤**:
  - [x] 1. 汇总最终验证结果与实测证据
  - [x] 2. 列出真机发现并修复的 3 个缺陷
  - [x] 3. 标注环境受限的未完成项与发布建议
  - [x] 4. 更新 B7 已知限制终态
- **风险与验证**:
  - 后端 446 passed / 6 skipped / 0 failed；前端构建通过
  - 已部署栈处于 head（f6g7h8i9j0k1）且健康
- **状态**: 第一版交付完成；量化验收与完整镜像重建待环境

## 2026-11-05 — 真实 Web 执行验证与 expect 包装修复

- **类型**: fix / test / ops
- **用户原话摘要**: 用部署栈自身页面跑一条真实 Web 用例，验证 UI 执行链路
- **诊断或需求理解**:
  - 真实 UI 执行失败：脚本里的 `expect(page.get_by_text(...))` 收到插桩后的 `_InstrumentedLocator`，
    Playwright `expect` 无法识别——默认脚本模板写法必然失败
- **影响范围**:
  - backend: `ui_runner` 注入 `script_expect`，调用真实 expect 前解包 Locator/Page
  - tests: 新增 expect 包装回归测试
  - docs: B7 实测记录增加 Web 执行章节；验收表与交付清单更新
- **步骤**:
  - [x] 1. 在部署栈上创建 UI 环境（`http://frontend`）与真实用例（登录页 + 品牌断言）
  - [x] 2. 复现 `Unsupported type: _InstrumentedLocator` 并修复
  - [x] 3. 重试执行通过，截图、尝试（error→passed）、批次项与报告全部正确
  - [x] 4. 报告显示重试后通过 1、不稳定 1、完成比例 100%
  - [x] 5. 补回归测试并更新文档
- **风险与验证**:
  - 解包只影响脚本内 `expect`，不改变插桩步骤与截图采集
  - 后端全量（见本轮记录）；真实 Web 执行 passed
- **状态**: 已落地；其余 Web 用例仍需要示例业务脚本

## 2026-11-05 — 真实部署升级演练：MySQL 迁移修复与实测验收

- **类型**: fix / test / ops
- **用户原话摘要**: 用本机运行中的部署栈做升级与验收演练
- **诊断或需求理解**:
  - 迁移只在 SQLite 验证过，MySQL 8 不允许 TEXT 默认值，实机升级直接失败
  - 数据库升级前需要可回退的完整备份；备份脚本未在真实栈验证
  - 同步执行（sync=true）不回写批次尝试，导致报告把已通过项算成未执行
- **影响范围**:
  - backend: 5 个迁移修复 TEXT 默认值；同步执行补 `sync_attempt_quietly`；验收执行器支持 `--token`
  - scripts: 备份恢复改用容器内 root 并从容器读取密码
  - tests: 新增同步执行回写测试
  - docs: B7 实测记录、B7 验收表、交付清单、索引
- **步骤**:
  - [x] 1. 升级前备份（db + uploads + 校验和）
  - [x] 2. 真实 MySQL 升级暴露并修复 TEXT 默认值问题，恢复后重新升级成功
  - [x] 3. 修复同步执行不回写尝试，补测试
  - [x] 4. 真实栈跑验收执行器（数据模式 incomplete、API 实测 passed）
  - [x] 5. 备份恢复 `--into-scratch` 8 张表行数一致
- **风险与验证**:
  - 完整镜像构建受 Playwright CDN 网络限制，本次用无网络 overlay 镜像部署；正式发布需完整重建
  - 后端 445 passed / 6 skipped / 0 failed；前端 tsc 通过
- **状态**: 已落地；剩余：Web 自动执行、200 次执行窗口、30 条生成质量样本

## 2026-11-05 — 最终收口：交付清单与代码卫生

- **类型**: docs / chore
- **用户原话摘要**: 做最终质量收口，输出交付清单
- **诊断或需求理解**:
  - 各批次记录里的「仍待完成」已过时，需要与实际代码对齐
  - 缺一份面向交付的总览（改了什么/怎么部署/怎么验证/还差什么）
  - 新增文件里有 2 处未使用导入
- **影响范围**:
  - docs: 新增第一版交付清单；索引增加入口；B3-B4/B4/B5/B6 记录的遗留表更新为现状
  - code: 清理 `cycle_report.py`、`acceptance_first_release.py` 未使用导入
- **步骤**:
  - [x] 1. 逐份复核「仍待完成」并标注已补齐/未做
  - [x] 2. 输出交付清单（范围、代码地图、迁移链、验证矩阵、部署回退、验收状态、限制）
  - [x] 3. AST 检查新增文件的未使用导入并清理
  - [x] 4. 文档链接复查
- **风险与验证**:
  - 仅文档与导入清理，无行为变更
  - 后端全量（见本轮记录）；前端 vite build 通过
- **状态**: 已落地；剩余为真实环境验收项

## 2026-11-05 — 收尾：用例版本与评审面板

- **类型**: feature
- **用户原话摘要**: 继续补齐 B2 遗留的版本/评审页面入口
- **诊断或需求理解**:
  - 送审/评审/版本/归档接口早已就绪，但前端只能用「编辑」改生命周期，看不到版本历史与评审记录
- **影响范围**:
  - frontend: TestCaseList 新增「版本/评审」抽屉与操作按钮
  - docs: 收尾记录、索引、B1-B2 遗留表
- **步骤**:
  - [x] 1. 版本历史表（含快照展开）
  - [x] 2. 评审记录表与送审/通过/驳回（驳回必填意见）
  - [x] 3. 归档/恢复入口
  - [x] 4. 操作后刷新列表与抽屉数据
- **风险与验证**:
  - 复用既有接口与权限口径，无后端变更
  - 后端全量 444 passed / 6 skipped / 0 failed；前端 tsc + build 通过
- **状态**: 已落地；抽屉交互需浏览器确认

## 2026-11-05 — 交付整理：说明文档、索引与一致性检查

- **类型**: docs / chore
- **用户原话摘要**: 整理发布说明与使用手册，做全仓一致性检查
- **诊断或需求理解**:
  - 9 份批次记录缺少索引入口，README 未反映第一版工作流
  - 新增环境变量未写入 `.env.example`，部署时容易漏配
  - 记录里的相对链接有 2 处指向错误目录；验证脚本有一个多余的 pytest 参数
- **影响范围**:
  - docs: README 工作流章节、`change-records/00-索引.md`、B7 检查清单
  - config: `backend/.env.example` 增加 `FAST_CASE_SKILL_PROJECTS`
  - scripts: `verify-release.sh` 修正 `--e2e` 命令；`backend/.gitignore` 忽略 `acceptance/`
- **步骤**:
  - [x] 1. README 增加第一版工作流与记录入口
  - [x] 2. 新增改造记录索引（状态 + 代码入口速查）
  - [x] 3. `.env.example` 补齐新配置项并说明回退方式
  - [x] 4. 修正 2 处 Markdown 相对链接；链接检查脚本确认 0 缺失
  - [x] 5. `bash -n` 校验备份/发布脚本；CI YAML 解析通过
  - [x] 6. 全仓 Python 语法检查通过；验收输出目录加入忽略
- **风险与验证**:
  - 仅文档与配置整理，无运行时代码变更
  - 后端最近一次全量 444 passed / 6 skipped / 0 failed；前端最近一次 tsc + build 通过
- **状态**: 已落地；预发验收与备份演练仍待真实环境

## 2026-11-05 — 收尾：项目内全局搜索与报告版本对比

- **类型**: feature
- **用户原话摘要**: 继续补齐计划中的顶部搜索与报告历史对比
- **诊断或需求理解**:
  - 顶部导航缺计划要求的搜索入口，跨模块找记录只能逐个列表翻
  - 报告只能切换 revision 查看，无法看出两版之间指标、风险与覆盖的变化
- **影响范围**:
  - backend: `/projects/{pid}/search` 接口与 schema；`/reports/{id}/compare` 与对比 schema
  - frontend: ProjectSearch 组件 + Layout；TestCycle 无改动；TestCaseList/RequirementList/DefectList/UiCaseList 深链接；ReportDetail 对比弹窗
  - tests: test_search、test_cycle_reports 追加
  - docs: change-records/收尾-全局搜索与报告对比.md
- **步骤**:
  - [x] 1. 搜索接口：需求/用例/计划/批次/缺陷/接口用例/UI 用例，通配符转义与项目隔离
  - [x] 2. 顶部搜索框与分组结果
  - [x] 3. 四个列表页深链接（缓存优先，缺失按 ID 拉取）
  - [x] 4. 报告对比接口：指标差值、风险增减、覆盖变化
  - [x] 5. 报告详情对比弹窗
  - [x] 6. 5 项新增/追加测试
- **风险与验证**:
  - 搜索为标题/正文跳转检索，非全文索引；每组限 5 条
  - 深链接参数读取后从 URL 移除，避免刷新重复弹窗
  - 后端 444 passed / 6 skipped / 0 failed；前端 tsc + build 通过
- **状态**: 已落地；交互需浏览器手工确认

## 2026-11-05 — B4/B5/B7 收尾：批次执行、缺陷证据、验收执行器

- **类型**: feature / tooling
- **用户原话摘要**: 继续补齐批次发起执行、缺陷证据对照与第一版验收执行
- **诊断或需求理解**:
  - 批次页只能登记手工结果，自动化资产必须去各自页面执行，且不带 `cycle_item_id`
  - 缺陷看不到「原失败 → 重试 → 复测」的证据链
  - B7 的 60 项验收只是文档清单，无法重复执行
- **影响范围**:
  - backend: `/defects/{id}/evidence` 接口与 schema；`tools/acceptance_first_release.py`
  - frontend: TestCycleDetail 执行入口/尝试列/最近运行；DefectList 详情抽屉
  - tests: test_defect_evidence、test_acceptance_tool
  - docs: change-records/B7收尾-批次执行与验收执行器.md
- **步骤**:
  - [x] 1. 批次项按资产类型直接发起 API/UI 执行（携带 cycle_item_id 与批次环境）
  - [x] 2. 缺陷证据接口：批次/资产/全部尝试/复测/历史
  - [x] 3. 缺陷详情抽屉（失败来源证据 + 状态历史）
  - [x] 4. 验收执行器：60 项范围、缺陷闭环、报告判定、失败即非零退出
  - [x] 5. MockTransport 端到端测试与 dry-run
- **风险与验证**:
  - 手工结果为人工录入并在摘要标注；自动项未执行判「未完成」
  - 后端 441 passed / 6 skipped / 0 failed；前端 tsc + build 通过
- **状态**: 已落地；剩余为需真实环境的验收执行与备份演练

## 2026-11-05 — B7 兼容验证与发布准备

- **类型**: test / ci / docs
- **用户原话摘要**: 按改造计划继续推进 B7
- **诊断或需求理解**:
  - 迁移只做过单条手工校验，缺一条“旧库 → head → 回退”的可重复验证
  - 新增入口缺统一的权限与跨项目隔离测试
  - 基线 7 个失败（1 个示例数据缺陷 + 6 个外部依赖）长期红着，CI 无法作为门槛
  - 缺部署/升级/回退与备份恢复的可执行脚本
- **影响范围**:
  - backend/tests: test_migration_chain、test_permissions_isolation、conftest 集成门控
  - examples/demo-openapi.yaml: 补齐字段描述
  - ci: .github/workflows/ci.yml
  - scripts: backup-restore-drill.sh、verify-release.sh
  - docs: change-records/B7-兼容验证与发布.md
- **步骤**:
  - [x] 1. 迁移链测试：遗留库升级、回填校验、整批回退
  - [x] 2. 权限矩阵 + 跨项目隔离测试（工作台/执行中心/报告/缺陷）
  - [x] 3. 外部依赖测试以 LY_INTEGRATION 门控；修复 openapi 示例数据
  - [x] 4. CI 示例（后端 + 迁移链 + 前端构建）
  - [x] 5. 备份/恢复演练脚本（dump/校验/scratch 恢复比对）
  - [x] 6. 发布校验脚本与升级/回退说明、第一版验收表
- **风险与验证**:
  - 全量 436 passed / 6 skipped / 0 failed；迁移链与权限隔离为门槛
  - 备份恢复脚本未在预发实机执行（本机无容器），已在文档标记为发布阻塞项
  - 量化验收（60 项/6 缺陷/200 次执行/30 样本）需示例业务与模型，未执行
- **状态**: 已落地（B7 主体），发布前仍需预发演练与量化验收

## 2026-11-05 — B6 报告与质量工作台

- **类型**: feature / migration
- **用户原话摘要**: 按改造计划继续推进 B6
- **诊断或需求理解**:
  - 现有轮次统计只有计数，没形成可追溯的报告快照
  - 缺统一统计口径（通过率/完成比例/零分母）与遗留风险清单
  - 重试通过会掩盖首次失败；报告无法导出
- **影响范围**:
  - backend: TestCycleReport 模型+迁移、cycle_report 服务（统计/风险/导出）、reports 接口
  - frontend: Reports 列表、ReportDetail、批次页生成入口、工作台跳转、导航
  - docs: change-records/B6-报告与质量工作台.md
- **步骤**:
  - [x] 1. 报告快照模型与迁移（revision 不可变）
  - [x] 2. 统计口径：通过率/完成比例/未执行/运行中/跳过 互斥
  - [x] 3. 重试与不稳定记录（保留首次失败）、版本漂移
  - [x] 4. 需求覆盖标注用例关联/规则/无覆盖
  - [x] 5. 遗留风险清单与关联缺陷汇总
  - [x] 6. Markdown 与 Excel 导出
  - [x] 7. 前端报告列表/详情/生成入口；4 项测试 + 迁移验证
- **风险与验证**:
  - 报告只读历史，不随结果变化；零分母统一显示「不适用」
  - 导出为同源直连下载，跨域部署需改为令牌下载（已记录）
  - 前端 tsc + vite build 通过；后端全量见本轮记录
- **状态**: 已落地（B6 主体）；剩余：历史对比视图、定时报告、下载令牌

## 2026-11-05 — B5 缺陷与修复回归

- **类型**: feature / migration
- **用户原话摘要**: 按改造计划继续推进 B5
- **诊断或需求理解**:
  - 缺陷只有自由文本，缺失败分类、预期/实际、环境与发现/修复构建，无法判断何时可以关闭
  - 没有复测概念：修复后无法记录"原问题已复测通过"，关闭没有依据
  - 同一失败可重复建缺陷；状态变化无历史
- **影响范围**:
  - backend: defect 模型+迁移+schemas、defect_workflow 服务、defects 接口、消息
  - frontend: workflow 常量、DefectList（分类/构建/复测/历史/关闭）、TestCycleDetail（建缺陷前建议确认）
  - docs: change-records/B5-缺陷与修复回归.md
- **步骤**:
  - [x] 1. 结构化字段 + 状态历史 + 失败来源唯一约束
  - [x] 2. 规则分类建议与草稿预填；从执行项建缺陷去重（409）
  - [x] 3. 复测接口：通过→resolved、失败→reopened，均写历史
  - [x] 4. 关闭闸门：修复构建 + 通过复测 + 关联回归批次全绿
  - [x] 5. 关联回归批次接口与跨项目校验
  - [x] 6. 前端分类/构建列、复测弹窗、历史弹窗、快捷关闭/重开
  - [x] 7. 迁移与 4 项测试；SQLite upgrade/backfill/downgrade 验证
- **风险与验证**:
  - 旧缺陷回填时同一执行项只保留一条失败来源，避免唯一约束失败
  - 关闭失败会一次列出全部阻断原因，不静默
  - 前端 tsc + vite build 通过；后端全量见本轮记录
- **状态**: 已落地；剩余：缺陷详情页前后证据对照、AI 分类建议、批次选择器

## 2026-11-05 — B4 阶段二：执行器回写与统一执行中心

- **类型**: feature / migration
- **用户原话摘要**: 按改造计划继续推进 B4
- **诊断或需求理解**:
  - 批次项只有手工结果，API/UI/agent 运行结束后不会回到批次，无法汇总
  - 缺少跨手工/API/Web 的统一执行列表，用户要在三个页面之间来回找
  - 重试与重复回调没有幂等约束，证据可能被覆盖或重复
- **影响范围**:
  - backend: 三张运行表加 `cycle_item_id`、迁移 `d4e5f6g7h8i9`、cycle_attempt_sync、run 任务、agent_once 收尾、取消接口、运行请求 schema、`/executions` 接口
  - frontend: ExecutionCenter 页面、路由与导航
  - docs: change-records/B4阶段二-执行回写与执行中心.md
- **步骤**:
  - [x] 1. 运行表关联批次项 + 唯一约束（批次项, 运行类型, 运行 ID）
  - [x] 2. 幂等同步服务：新建/更新尝试、终态保护、结果投影
  - [x] 3. 接入 API/UI 任务、agent_once 收尾、取消流程
  - [x] 4. 三处运行入口接受并校验 `cycle_item_id`
  - [x] 5. 统一执行列表接口 + 执行中心页面（筛选/分页/URL 可分享）
  - [x] 6. 迁移与 4 项测试；SQLite upgrade/downgrade 验证
- **风险与验证**:
  - 同步失败只记日志，不影响运行结论；迟到 running 回调不改终态
  - 跨项目执行项直接 400；独立运行不受影响
  - 后端全量（见本轮记录）；前端 tsc + vite build 通过
- **状态**: 已落地（B4 阶段二主体）；剩余：批次页发起执行、超时/Worker 重启恢复、步骤级尝试关联

## 2026-11-05 — B3 AI 用例设计、B4 批次与执行尝试（阶段一）

- **类型**: feature / refactor / migration
- **用户原话摘要**: 按改造计划继续推进 B3、B4
- **诊断或需求理解**:
  - 分步（agent）生成没有走 test-case-generator 校验，草稿可能没有每步预期与设计依据；设计依据未记录生成模型
  - 确认入库没有创建版本记录，AI 用例缺少版本起点
  - 批次只支持手工用例，缺少目标构建/环境快照/凭据引用/数据说明，也没有重试尝试记录
- **影响范围**:
  - backend: ai_gen_pipeline / fast_case_skill / design_trace / ai_generate / config / conftest；test_plan 模型+迁移+schemas+接口；tests 两组新增
  - frontend: CaseDesignEvidence 展示生成模型
  - docs: change-records/B3-B4阶段一-变更与验收.md
- **步骤**:
  - [x] 1. 分步流程注入规则并复用同一套入库校验（含测试点阶段）
  - [x] 2. design_trace 记录 llm_model；确认入库写生命周期与基准版本
  - [x] 3. 技能默认开启（`FAST_CASE_SKILL_PROJECTS=*`），单测默认关闭
  - [x] 4. 批次快照字段 + 资产类型/ID + 用例版本固定
  - [x] 5. 执行尝试表与接口，重试追加、终态回写
  - [x] 6. 迁移 + 4 组测试；SQLite 上 upgrade/downgrade 验证
- **风险与验证**:
  - 默认开启技能会拒绝没有 design_trace 的模型输出（422 并重试一次）；可用环境变量回退
  - `case_id` 改可空、新增 `(cycle_id, asset_type, asset_id)` 唯一约束；回退时清理仅属自动化资产的计划项
  - 后端 421 passed / 7 既有环境失败；前端 tsc 通过
- **状态**: 已落地（B3、B4 阶段一）；B4 阶段二（执行器回写、统一执行中心）待续

## 2026-11-05 — B0/B1/B2：开发基线、工作台、用例生命周期与版本

- **类型**: feature / refactor / migration
- **用户原话摘要**: 按《LY 测试平台第一版改造计划》改造项目，先做 B0 基线、B1 工作台与入口、B2 需求与用例资产
- **诊断或需求理解**:
  - `TestCase.status` 同时承担生命周期与执行结果，执行失败会把已确认用例改回执行态（计划第 40 行的优先项）
  - `judge_agent_once_result` 以“最后动作 done / status ok”判通过，没有断言证据也能通过（同一优先项）
  - 缺工作台与旧链接兼容入口；用例无版本、评审、归档保护
- **影响范围**:
  - backend: models/testcase+requirement、alembic 新迁移、testcase_lifecycle、asset_versions、case_expectation、testcases/requirements/test_plans 接口、testcase_run_finalize、ui_scenario_agent、project/workbench
  - frontend: App/domainNav/Layout/ProjectShell/TestCaseList/workflow/Workbench
  - docs: B0 契约、B1-B2 变更与验收
- **步骤**:
  - [x] 1. B0 基线：依赖安装、测试基线、差异清单、数据/接口契约、迁移规则
  - [x] 2. 用例生命周期与执行结果拆两轴（模型/迁移/接口/写回路径/前端）
  - [x] 3. 用例与需求版本快照、送审/评审、归档与引用删除保护
  - [x] 4. agent_once 改为按用例预期与断言覆盖判定，缺断言记阻塞
  - [x] 5. 工作台接口与页面、导航重组、旧链接兼容、项目切换重置
  - [x] 6. 新增 4 组测试；迁移在 SQLite 上验证 upgrade/backfill/downgrade
- **风险与验证**:
  - `status` 保留为生命周期镜像，旧读取方仍可用；执行结果词走 `last_result`
  - 迁移先扩列再回填，回退 `alembic downgrade 91de45b6c284` 可恢复列结构
  - 后端 418 passed / 7 既有环境失败；前端 `tsc -b` 与 `vite build` 通过
- **状态**: 已落地（B0/B1/B2 主体），后续 B2 收尾、B3–B7 见 change-records/B1-B2-变更与验收.md 第 4 节

## 2026-10-28 — UI 环境 KV 注入，去掉登录方式/测试数据


- **类型**: feature / refactor
- **用户原话摘要**: Token 注入有 access_token 仍要登录；改为像变量一样自配 localStorage/请求头；去掉登录方式与测试数据；AI 生成也不写用例测试数据
- **诊断或需求理解**: 旧 token_inject 默认写 localStorage.token，本站读 access_token；应用层应显式 KV 配置
- **影响范围**:
  - backend: model/alembic/schema/ui_envs/ui_env_auth/ui_run_executor/ui_scenario_agent
  - frontend: UiEnvList / UiCaseList / UiSuiteList
- **步骤**:
  - [x] 1. inject_headers_json / inject_local_storage_json + 兼容旧 token_inject
  - [x] 2. resolve 渲染 {{var}} 并自动注入
  - [x] 3. 环境表单 KV；用例/套件去掉登录方式与测试数据
  - [x] 4. Agent 确认不写 variables_json
  - [x] 5. 测试 + alembic upgrade
- **风险与验证**: 旧环境仍可读；保存环境会把 token_inject 清为 none 并写入 KV
- **状态**: 已落地
