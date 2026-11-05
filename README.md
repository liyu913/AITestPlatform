# LY测试平台

AI 测试平台：FastAPI 后端 + React 前端，MySQL / Redis / Milvus，云端 Embedding 与 LLM。

## R 版工作流（方案 → 计划 → 用例 → 评审发布 → 执行 → 分析/缺陷 → 回归/报告）

侧栏按「概览 / 测试资产 / 执行 / 质量 / 设置」组织，进入项目默认落在工作台：

| 环节 | 入口 | 要点 |
| --- | --- | --- |
| 工作台 | 概览 → 工作台 | 我的待执行、待评审、待回归、近期执行；每项都可跳到具体记录 |
| 测试方案 | 测试资产 → 测试方案 | 范围/策略/风险/方法/准入准出；内容修改自动生成新版本，计划引用具体版本 |
| 需求与用例 | 测试资产 → 需求管理 / 功能用例 | 需求验收规则与版本；用例生命周期（草稿/待评审/已确认/已归档）与最近执行结果分离；编辑已确认用例自动回到待评审 |
| 评审与发布 | 功能用例 → 用例评审 | 待评审队列支持指派评审人与截止时间；通过后版本 `confirmed`，可发布到正式库；同一版本重复发布 409 |
| AI 用例设计 | 功能用例 → AI 生成用例 | 草稿必须带每步预期与设计依据；按「本次指定 → 动作绑定 → 项目默认 → 系统默认」解析 Skill/模型，调用记录保存配置快照 |
| AI 与 Skill | 设置 → AI 与 Skill；系统 AI 配置 → Skill 版本 | 按动作绑定 Skill/模型、设置项目默认模型；SKILL.md 可上传导入并按 key 自动递增版本 |
| 测试计划与批次 | 执行 → 测试计划与执行 | 批次冻结目标构建、环境快照、凭据引用；执行前就绪检查要求功能用例已发布；开始后 `started_at` 生效 |
| 执行中心 / 证据 | 执行 → 执行中心 / 执行证据 | 手工、API、Web 合并列表；证据可按计划/批次/用例检索，保留每次尝试与运行归属 |
| 缺陷分析 | 质量 → 缺陷分析 | 失败先分析：产品/环境/数据/脚本分类与结论；产品问题转正式缺陷，环境类可直接结束 |
| 缺陷与提交 | 质量 → 缺陷与回归 / 待提交 | 修复构建、复测、回归闸门、状态历史；平台内/外部提交记录与幂等重试 |
| 回归与报告 | 质量 → 缺陷与回归 / 测试报告 | 一键从缺陷创建回归批次；报告快照不可变，支持 AI 叙述（保存配置快照）、版本对比与导出 |
| 搜索 | 顶部搜索框 | 跨需求/用例/计划/批次/缺陷/接口/UI 资产跳转 |

AI 单次执行（agent_once）仍按预期与断言判定：没有断言证据、未覆盖必测预期、或用例没有可校验预期时记为阻塞。

改造记录按 R0–R6 归档在 `change-records/`（总览见 [R 版交付清单](change-records/R-交付清单.md)，索引见 [change-records/00-索引.md](change-records/00-索引.md)）。发布与回退步骤见 [B7 发布说明](change-records/B7-兼容验证与发布.md)，验收执行器见 `backend/tools/acceptance_first_release.py`。

## 部署方式

### 方式一：一键全栈（根目录）

```bash
docker compose up -d --build
```

访问 http://127.0.0.1:3000 。API 首次启动会自动创建默认超管 `admin` / `admin123`，无需再跑脚本。可选根目录 `.env` 配置 `LLM_API_KEY` 等。

### 方式二：仅中间件（开发调试）

编排在 `deploy/`，不要在仓库根目录执行：

```bash
cd deploy
docker compose up -d && docker compose ps
```

默认端口与 `backend/app/config.py` 一致：`3306`（MySQL）、`6379`（Redis）、`19530`（Milvus）。若端口冲突，可在运行前设置环境变量覆盖，例如：

```bash
MYSQL_PORT=13306 MILVUS_PORT=29530 docker compose up -d
```

MySQL 数据库：`raina`，用户/密码：`raina` / `raina`（与默认 `MYSQL_DSN` 匹配）。然后按下方步骤本机启动后端 / Worker / 前端。

更完整步骤见 `article/《RainaAI测试平台》-第1节：项目部署和启动.md`。

## 后端

```bash
cd backend
cp .env.example .env
# 官方 PyPI 慢时用清华镜像；直连官方源则执行 uv sync
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

另开终端启动 Celery Worker。UI 任务入队到环境上的队列名（默认 `ui`），Worker 必须同时监听 `ui` 和默认的 `celery`，否则任务会停在 Redis 里，页面一直显示「探索中」：

```bash
cd backend
uv run celery -A app.tasks.celery_app worker -l info -Q ui,celery
```

Windows 上再加 `--pool=solo`（默认 prefork 池在 Windows 不可用）：

```bash
uv run celery -A app.tasks.celery_app worker -l info --pool=solo -Q ui,celery
```

接口自动化（项目内「接口环境 / 接口用例」）、UI 自动化（「UI 环境 / 元素仓库 / UI 用例 / 套件 / 定时 / 执行记录」）与**性能测试**（「压测场景 / 压测记录」，Locust）均依赖该 Worker 异步执行；未启动时执行记录会停在 `pending`。用例页「发送调试」为同步请求，可不依赖 Worker。

UI 企业能力：套件编排与跨浏览器矩阵、队列并发隔离、账号变量隔离、稳定等待（含 networkidle）、元素仓库（Page Object）、运行趋势、JUnit/HTML 导出、CI 触发（`POST .../ui-runs/ci-trigger`）。

**UI AI 辅助**（侧栏「AI 辅助」）：选择功能用例 + UI 环境后，Worker 用 Playwright 真浏览器探索页面，生成可入库的 UI 步骤 DSL 与可复制的 Playwright 代码；自动 dry-run 后一键写入 UI 用例。依赖 Worker、LLM（系统 AI 配置）与 Playwright 浏览器。

性能测试 MVP：从接口用例勾选生成压测场景，配置并发/时长/SLA，Celery 内嵌 Locust 执行，报告含 RPS、延迟分位数、错误率、按接口拆分、基线对比、近14天趋势与 HTML 导出。
若使用**定时回归**（接口或 UI），还需启动 Celery Beat（可与 Worker 分进程）：

```bash
cd backend
uv run celery -A app.tasks.celery_app beat -l info
```

定时 Cron 按 **UTC** 解释（与 Celery `timezone=UTC` 一致）。例如每天 UTC 01:00 为 `0 1 * * *`。Beat 负责发现到期任务并入队，实际执行仍由 Worker 完成。

UI 自动化还要在 Worker 所在机器安装浏览器。`uv sync` 只安装 Python 包 `playwright`；API 和 Worker 启动时都不会自动下载浏览器。未安装时探索会在 `BrowserType.launch` 失败，提示 `Executable doesn't exist`（例如 `ms-playwright\chromium_headless_shell-…\chrome-headless-shell.exe`）。在 `backend` 目录执行一次即可，装到当前用户的 `%LOCALAPPDATA%\ms-playwright`（Windows）或对应缓存目录：

```bash
cd backend
uv run playwright install chromium
# 跨浏览器矩阵再装：uv run playwright install firefox webkit
```

Playwright 升级后浏览器版本号会变，出现上述路径不存在时，重新执行同一条命令。

**UI 场景 Agent** 默认用 **LangGraph** 编排 + **Playwright CLI** 工具逐步探索（`snapshot` → LLM 决策 → `click`/`fill` → 再生成 `run(page, agent)` 脚本）；复杂步（canvas / 无 ref / 验证码读图）才在脚本里用 Midscene。另依赖仓库根目录的 Node 运行器 `ui-runner/`，与上面的 Python Playwright **分开安装**：

```bash
cd ui-runner
npm install
# 国内可加速：npm install --registry=https://registry.npmmirror.com
npx playwright install chromium
```

装完后确认存在：`ui-runner/node_modules/@playwright/cli` 与 `@midscene/web`。然后**重启 Celery Worker** 再发起探索。

UI 用例可在页面点「录制」：后端会打开有头 Playwright 浏览器，操作结束后编译成 Python 脚本，可在平台编辑后执行。需要先配置 UI 环境（被测站点 Base URL）。

在 `.env` 中配置 `EMBEDDING_API_KEY`、`LLM_API_KEY`（云端 OpenAI 兼容接口）后，索引与 AI 功能可用。环境密钥变量使用 Fernet：`SETTINGS_ENCRYPTION_KEY` 本地已有默认值，生产环境请重新生成并覆盖。

## 前端

```bash
cd frontend
npm install
npm run dev
```

开发环境使用 Vite 代理：浏览器访问 `http://127.0.0.1:5173`，`/api` 会转发到 `http://127.0.0.1:8000`。可通过 `frontend/.env` 设置 `VITE_API_BASE`（默认留空即可配合代理）。

首次使用：后端启动后即可用默认超管 `admin` / `admin123` 登录（可用 `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` 覆盖）。也可在前端 **注册** 页自助创建账号（环境变量 `ALLOW_OPEN_REGISTRATION=false` 可关闭，需超管审批），或由超管通过管理接口创建用户。忘记超管密码时再执行 `python -m app.bootstrap` 强制重置。

**知识库**：每个项目可有多个知识库（新建项目会带「默认知识库」），文档按库上传与索引。AI 生成用例时可选择关联某一知识库，此时 RAG 仅检索该库内已上传文档的向量片段；不选则不进行知识库向量检索（仅依赖补充说明、关联需求等输入）。
