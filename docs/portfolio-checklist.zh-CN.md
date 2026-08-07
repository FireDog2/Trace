# Agent 开发工程师作品集验收清单

## 核心需求

- [x] PostgreSQL 数据源可在界面配置，凭据加密保存且不会返回前端。
- [x] 表级、字段级策略支持自动允许、需要审批、禁止访问；审批只消费一次。
- [x] 数据库使用独立最小权限只读账户，应用层与数据库层双重只读。
- [x] SQL 经 `sqlglot` AST 单语句/只读/函数/系统目录校验，再执行策略校验。
- [x] 查询前执行有界 `EXPLAIN`；高成本、受限字段和未知风险进入人工审批。
- [x] 查询具有 statement timeout、结果行数/字节上限和安全错误分类。
- [x] 模型、Lead 工具和 teammate 工具均有有界重试；耗尽后返回显式 fallback，禁止无限重复同一调用。
- [x] Lead 不直接持有数据库查询和报告生成工具，必须委派给角色化 teammate。
- [x] Knowledge / SQL / Report Agent 拥有独立工具白名单、持久化生命周期、超时、取消与最多三路并发限制。
- [x] Agent 工作状态、结果摘要和失败类别可在 `Agent` 面板查看。
- [x] 项目级业务知识源支持指标、术语、规则的 CRUD、版本、公式、维度、同义词和来源。
- [x] Knowledge Agent 可检索业务知识，Lead 只能通过 Knowledge Agent 获取口径。
- [x] 审批卡片可在对话区批准一次或拒绝，决定后任务安全恢复。
- [x] Trace 关联 Lead、teammate、工具、SQL、审批、重试、fallback 和工件；敏感字段递归脱敏并限长。
- [x] `GET /api/runs/{run_id}/replay` 可导出完整只读回放包，包含 Agent、审批、工件、Skill 与序列完整性检查。
- [x] 人工接受成功 run 后只创建一个持久化 Curator Agent；它没有任何工具，只读取结构化脱敏证据。
- [x] Skill Candidate 经静态安全校验，人工批准后仍保持禁用，避免自动自我修改。

## 演示与工程质量

- [x] FastAPI 与 React 在本机运行；Docker 只管理 PostgreSQL，便于频繁修改。
- [x] SQLite migration、确定性单元/验收测试、真实 PostgreSQL 集成测试和前端生产构建已覆盖。
- [x] 仓库包含架构、SQL 安全、多 Agent、Trace/Skill、安全边界和本地启动文档。
- [x] 仓库包含脱敏 golden trace，面试官无需模型 Key 即可检查核心设计。

## 秋招前建议继续做的展示层工作

- [ ] 录制 60–90 秒 GIF/视频：配置口径 → 提问 → SQL 审批 → 三 Agent 协作 → 报告 → Trace/Skill。
- [ ] 在 README 放一张主界面截图和一张审批/Trace 截图。
- [ ] 准备 3 分钟讲解稿，重点讲安全边界、失败恢复和为什么 Lead 不直接拿 SQL 工具。
- [ ] 可选：增加 PDF/Markdown 文档批量导入与向量检索；当前业务知识源是结构化、可解释的词法检索。
- [ ] 可选：增加登录、租户隔离和远程部署；当前定位是单用户本地作品集，不是生产 SaaS。

