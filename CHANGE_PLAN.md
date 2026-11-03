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
