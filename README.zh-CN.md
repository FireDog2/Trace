# Governed Data Analysis Agent

[English](README.md) | **简体中文**

一个本地优先、证据优先的受控数据分析 Agent 工作台。

> **Vibe Coding 让“做出一个能演示的 Agent”变得很快；Harness 决定它能否持续、稳定、可控地运行。**

## 为什么做这个项目

今天，借助 AI Coder，开发者可以在很短时间内拼出一个能对话、会调用工具的 Agent 产品。但从“一次跑通”走向持续运行，系统仍然必须回答更困难的问题：模型能看到什么、能做什么，失败后如何恢复，如何避免循环与越权，以及如何证明最终结论来自真实证据。这些问题不能只靠 Prompt 解决，它们属于 Agent Harness 的工程责任。

我选择**数据分析**作为这个命题的落地场景：分析需求广泛存在，同时天然包含业务口径、敏感数据访问、高成本 SQL、长上下文、多角色协作、人工审批和结果追溯。它不仅需要模型“会分析”，也足以暴露一个 Harness 是否真正具备权限边界、执行预算、恢复策略、持久化状态与可观测性。

本项目约 **95% 的代码由 AI Coder 在我的指导下生成或修改**。这不是为了说明工程师不再重要，恰恰相反：AI Coder 会高效执行明确的方向，也会放大模糊的架构、错误的业务假设和缺失的验收标准。在这个项目中，我承担的是产品与业务定义、架构设计、能力边界、任务拆解、验收测试和失败闭环，并让 AI 参与具体实现。

我想通过这个项目证明：在 AI-native 开发中，代码生成可以被大规模委托，但工程责任不能被委托。比单纯增加代码产量更稀缺的，是同时理解业务、具备 Harness 思维，并能持续为 AI 提供方向、约束和可验证反馈的工程师。

## 先把 Agent 与 Harness 说清楚

模型的分析、推理和工具选择能力来自训练；外部工程代码不需要重新编写一套“智能”。但只有模型还不是一个能够在真实业务中工作的 Agent 产品——模型还需要一个领域环境，让它能够获得知识、观察状态、采取行动，并在明确的权限边界内承担任务。

```text
Agent 产品 = Model + Domain Harness

Data Analysis Harness
  = Business Knowledge       业务口径与数据语义
  + Governed Data Access     文件、Python 与受控 SQL
  + Context & State          上下文投影、任务与持久化状态
  + Permissions              工具白名单、策略与人工审批
  + Recovery                 超时、重试预算、循环检测与 Fallback
  + Evidence & Observation   Artifact、Trace、Replay 与评测
```

在这套分工中，Harness 不替模型写死分析流程，也不使用庞大的规则树模拟判断力。模型负责理解问题、制定分析路径、选择工具、改写失败查询和解释证据；Harness 负责把真实世界以清晰、受控、可观察的方式暴露给模型，并决定一次行动能否以及如何真正发生。

| 模型负责 | Harness 负责 |
| --- | --- |
| 理解业务问题并制定分析计划 | 提供版本化业务知识与有界上下文 |
| 选择下一步工具并生成类型化参数 | 校验参数、Scope、权限和执行预算 |
| 根据安全诊断改写 SQL | 执行 AST、策略、`EXPLAIN`、审批与只读查询 |
| 综合多个 Agent 的结构化 Handoff | 管理角色能力、租约、截止时间、重试和取消 |
| 基于证据形成解释与报告 | 注册 Artifact、Evidence Link，并持久化 Trace |

因此，这个项目同时实践了两层 Harness 工程：

```text
产品运行：分析模型 + 数据分析 Harness → 可控的数据分析 Agent
项目开发：AI Coder + 规格 / 架构 / 测试 / 反馈 → 可验证的软件系统
```

AI Coder 在第二层扮演模型，我为它构建开发环境、提供业务方向和验收反馈；运行中的分析模型则在第一层使用这个项目提供的领域 Harness。两层关系指向同一个结论：**不要用外部代码假装创造智能，要用工程让已有的智能可靠地作用于真实世界。**

在这个工作台中，Lead Agent 可以协调具备不同权限边界的 Knowledge、SQL 和 Report Agent，分析本地文件与只读 PostgreSQL 数据，生成可追溯到分析证据的报告，并为每一次关键决策留下可审计、可离线回放的执行记录。

这个项目的重点不是在聊天界面外包一层大模型，而是实现一个完整的 **Agent Harness**：通过确定性的运行时约束模型上下文、工具权限、执行预算、人工审批、失败恢复、持久化状态与 Trace。

> **模型负责判断下一步做什么，Harness 决定它如何安全地发生；AI 可以生成代码，工程师仍对系统结果负责。**

## 30 秒看懂这个项目

| 工程问题 | 本项目的处理方式 | 系统保证 |
| --- | --- | --- |
| 模型能否绕过数据权限？ | SQL AST 校验、数据源策略快照、受限 `EXPLAIN`、一次性审批、只读连接 | 模型永远拿不到原始数据库连接，所有查询都经过统一安全网关 |
| 多 Agent 会不会无限扩张或失控？ | Lead + 三种角色化 Teammate，独立工具白名单、租约、截止时间、轮次上限和取消状态 | 协作过程有界、可观察、可恢复，而不是无约束 Agent Swarm |
| 长对话如何避免上下文失控？ | 版本化 Prompt、上下文预算、非破坏性压缩、所有者隔离的 Checkpoint | 模型上下文保持有界，完整会话仍作为审计记录持久保存 |
| 失败后会不会无限重试？ | 结构化错误分类、调用签名、独立重试预算、重复调用检测和显式 Fallback | 相同失败不会重置预算，模型和工具循环都能确定性停止 |
| 报告结论如何核验？ | 不可变分析输入、Artifact 注册、Evidence Link、Markdown/PDF 导出 | 重要结论可以追溯到真正参与分析的数据产物 |
| 一次执行能否复盘？ | 贯穿 Lead、Teammate、Tool、SQL、Approval 和 Artifact 的关联 Trace | Replay 读取持久化事件，不需要重新调用模型或执行数据库查询 |

## 一条完整的黄金路径

仓库内置了一个“区域利润下滑诊断”演示场景：

1. 用户配置一个使用最小权限只读账户的 PostgreSQL 数据源，并为表和字段设置访问策略。
2. 用户提出经营分析问题，Lead Agent 建立计划并拆分任务。
3. Knowledge Agent 检索项目级指标、术语和业务规则，返回结构化口径。
4. SQL Agent 提出查询；查询依次经过 AST、字段策略、`EXPLAIN` 风险和任务预算检查。
5. 受限字段或高风险计划会暂停执行，并产生与 SQL 哈希及策略版本绑定的一次性审批。
6. 查询在只读事务、超时、行数和字节上限内执行，结果注册为 Evidence Artifact。
7. Report Agent 读取已注册证据，生成带 Artifact 引用的管理报告。
8. 完整运行可以导出为脱敏 Replay Bundle；人类接受成功运行后，零工具 Curator 才能生成默认禁用的 Skill Candidate。

演示问题、预期发现和运行说明位于 [`examples/postgres-business-review/`](examples/postgres-business-review/README.md)，不调用模型也能审查的确定性回放样例位于 [`examples/traces/golden-run.json`](examples/traces/golden-run.json)。

## Harness 架构

系统将大模型视为一个**不可信的规划器**。模型只能生成消息和类型化 Tool Call；状态转换、权限判断、凭据、上下文投影、工具执行、重试、证据和 Trace 始终由 Harness 掌控。

![Governed Data Analysis Agent Harness 架构](docs/assets/harness-architecture.png)

<p align="center"><sub>模型提出行动；Harness 管理上下文、权限、执行、恢复、证据与持久化状态。</sub></p>

## 四个核心工程能力

### 1. 受控工具执行，而不是让模型直接操作基础设施

每个工具都有明确的 Pydantic 输入模型和 Harness 持有的 Handler：

```text
模型提出 Tool Call
  → 参数类型校验
  → Project / Task 所有权与 Scope 校验
  → 权限策略判断：允许 / 审批一次 / 拒绝
  → 通过窄接口执行
  → 结果截断、错误归一化
  → 持久化 Tool Attempt、Trace 和 Artifact
```

未知工具默认拒绝。绝对路径、覆盖写、网络访问、依赖安装等越界动作不会因为模型提出请求就自动获得权限。数据库凭据只存在于受保护的服务层，不会进入模型上下文、前端响应或普通 Trace。

### 2. 纵深防御的 PostgreSQL Safety Gateway

`query_database` 不是一个薄封装的数据库工具，而是一条完整的策略执行链：

```text
类型化调用
  → 单条 SELECT AST Allowlist
  → Schema 解析与数据源策略快照
  → 每任务查询预算
  → 有界 EXPLAIN 风险分类
  → 必要时请求一次性人工审批
  → 只读事务 + Statement / Lock Timeout
  → 返回行数与字节限制
  → 注册结果 Artifact + 写入脱敏 Trace
```

系统拒绝写操作、DDL、多语句、不可安全解析的 `SELECT *`、危险函数和系统目录访问。字段策略分为自动允许、需要审批和禁止访问；审批绑定规范化 SQL 哈希、数据源和策略版本，并且只能消费一次。

这是纵深防御而不是单点校验：即使应用层 AST 策略存在缺陷，执行仍受专用只读账户、只读事务、查询超时和结果预算约束。`EXPLAIN` 只被视为风险启发式，而不是对真实执行时间的保证。

### 3. 持久化、有边界的多 Agent 协作

Lead 是唯一直接面向用户的 Agent。它维护总体目标和计划，但不能直接执行数据库查询或生成最终报告，必须把不同能力委派给具备独立边界的 Teammate：

| 角色 | 主要职责 | 能力边界 |
| --- | --- | --- |
| Knowledge Agent | 查询项目级指标、术语和业务规则 | 不能执行 SQL |
| SQL Agent | 查看策略过滤后的 Schema，提出并执行受控查询 | 拿不到数据库凭据，不能发布最终报告 |
| Report Agent | 读取已注册 Artifact，生成图表和证据化报告 | 不能直接访问数据库 |
| Curator | 从成功 Trace 的结构化脱敏证据中提炼候选工作流 | 工具白名单为空，候选 Skill 默认禁用 |

每个 Teammate 都有独立的持久化运行记录、消息、状态、Tool Allowlist、最大轮次、墙钟截止时间、重试预算和取消信号。结果通过结构化 Handoff 返回，而不是把不同 Agent 的完整上下文拼接到同一个对话数组中。

启动时，系统会将遗留的活跃工作标记为中断，避免进程重启后仍把已经失去 Worker 的任务显示为运行中。

### 4. 可恢复、可审计、可离线回放

Provider 错误和 Tool 错误使用两套独立的恢复策略。网络、限流、超时、语法、权限拒绝、查询超时、结果溢出和 Teammate 超时都有结构化类别与明确的 `retryable` 决策。

重试状态按归一化 Tool Call 签名记录。模型会收到剩余次数和 Fallback 指令；重复相同失败调用不会获得新的预算。总 Tool Action、单一失败调用、Provider Retry、Teammate 数量与轮次、SQL 查询数、结果行数、结果字节和执行时间都有显式上限。

同一个 `TraceContext` 关联以下事件：

- Lead 与 Teammate 生命周期；
- Prompt 版本、模型调用与上下文预算；
- Tool Call、权限判断、重试和 Fallback；
- SQL 校验、风险分类与审批；
- Agent 间消息、Artifact 与 Evidence Link；
- 人工接受、Curator 和 Skill Candidate。

Replay 从持久化且经过递归脱敏、长度限制的事件中构建，不会重新执行模型或数据库查询，因此可以作为稳定的调试、审计和作品集证据。

## Context、Memory 与 Evidence 的边界

项目刻意拆分了四种经常被统称为“Agent Memory”的状态：

| 状态 | 用途 | 变更规则 |
| --- | --- | --- |
| Transcript | 完整的用户、Lead 和 Teammate 协议历史 | 从运行时视角只追加，不被压缩覆盖 |
| Context Checkpoint | 压缩后的短期延续上下文 | 创建新版本，不替代源消息 |
| Business Knowledge | 项目级指标、术语和业务规则 | 显式创建、版本化并记录来源 |
| Artifact | 数据结果、图表和最终报告 | 注册到项目并携带 Provenance / Evidence Link |

上下文管理器只生成当前模型调用可见的有界投影。完整消息继续保留用于审计；Checkpoint 是短期运行状态，Business Knowledge 是显式治理的长期语义来源，两者不会混为隐藏的全局记忆。

## 工程主张与验证证据

| 工程主张 | 主要实现 | 验证入口 |
| --- | --- | --- |
| 模型不能绕过 SQL 策略 | [`sql_safety.py`](src/data_analysis_agent/sql_safety.py)、[`postgres.py`](src/data_analysis_agent/postgres.py) | [`test_sql_safety.py`](tests/test_sql_safety.py)、[`test_postgres_gateway.py`](tests/test_postgres_gateway.py) |
| 工具权限由 Harness 而不是 Prompt 决定 | [`tools.py`](src/data_analysis_agent/tools.py)、[`permissions.py`](src/data_analysis_agent/permissions.py) | [`test_tools.py`](tests/test_tools.py)、[`test_permissions.py`](tests/test_permissions.py) |
| 多 Agent 协作有角色与生命周期边界 | [`team.py`](src/data_analysis_agent/team.py)、[`team_types.py`](src/data_analysis_agent/team_types.py) | [`test_teammate_runner.py`](tests/test_teammate_runner.py)、[`test_team_manager.py`](tests/test_team_manager.py) |
| 相同失败不能形成无限循环 | [`recovery.py`](src/data_analysis_agent/recovery.py)、[`providers.py`](src/data_analysis_agent/providers.py) | [`test_recovery.py`](tests/test_recovery.py)、[`test_providers.py`](tests/test_providers.py) |
| Context 压缩不会删除完整历史 | [`context.py`](src/data_analysis_agent/context.py) | [`test_context.py`](tests/test_context.py) |
| 报告结论可以追溯到分析输入 | [`reporting.py`](src/data_analysis_agent/reporting.py)、[`workspace.py`](src/data_analysis_agent/workspace.py) | [`test_portfolio_acceptance.py`](tests/test_portfolio_acceptance.py) |
| 完整运行可以安全离线回放 | [`tracing.py`](src/data_analysis_agent/tracing.py) | [`test_trace_instrumentation.py`](tests/test_trace_instrumentation.py)、[`golden-run.json`](examples/traces/golden-run.json) |
| 运行经验不会自动修改系统能力 | [`skills.py`](src/data_analysis_agent/skills.py) | [`test_skill_curator.py`](tests/test_skill_curator.py) |

## 技术栈

- Backend：Python 3.12、FastAPI、Pydantic、SQLite
- Frontend：React、TypeScript、Vite
- Local Analytics：DuckDB、受控 Python 执行
- Database Gateway：PostgreSQL、`sqlglot`、只读事务
- Reporting：Markdown、PDF、Evidence Artifact
- Testing：Pytest、Vitest、确定性 Fake Model、可选真实 PostgreSQL Integration Test

## 快速开始

环境要求：Python 3.12、Node.js 20、Docker Desktop。

只有一次性演示用 PostgreSQL 运行在 Docker 中；API 和前端保留在宿主机上，便于快速开发与调试。

```powershell
# 启动演示数据库；Fixture 会在首次创建 Volume 时载入
docker compose up -d postgres
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"

# 安装后端与前端依赖
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix web ci

# Terminal 1：启动 API
data-analysis-agent serve --host 127.0.0.1 --port 8765

# Terminal 2：启动 Web Workbench
npm --prefix web run dev
```

在 Workbench 中使用上述只读 DSN 创建 PostgreSQL 数据源。仓库内密码仅用于本地一次性 Fixture，不要提交真实 DSN 或 API Key。

代码修改不需要重新构建数据库容器：

```powershell
# 暂停数据库
docker compose stop postgres

# 丢弃演示数据并重新加载 Fixture
docker compose down -v
docker compose up -d postgres
```

## 验证

确定性测试不需要模型 API Key，也不需要运行 PostgreSQL：

```powershell
# 作品集核心验收路径
python -m pytest tests/test_portfolio_acceptance.py -v

# 完整确定性后端测试
python -m pytest -v

# 前端单元测试与生产构建
npm --prefix web test -- --run
npm --prefix web run build
```

真实 PostgreSQL 集成测试是显式 Opt-in：

```powershell
$env:TEST_POSTGRES_ADMIN_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
$env:TEST_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"
python -m pytest tests/test_postgres_integration.py -v
```

重新生成确定性 Golden Trace：

```powershell
python scripts/export_golden_trace.py
```

## 代码导航

| 模块 | 主要文件 |
| --- | --- |
| API 组合与生命周期 | [`api.py`](src/data_analysis_agent/api.py) |
| Lead Model / Tool Loop | [`agent.py`](src/data_analysis_agent/agent.py) |
| 版本化 Prompt | [`prompts.py`](src/data_analysis_agent/prompts.py) |
| Context 预算与 Checkpoint | [`context.py`](src/data_analysis_agent/context.py) |
| 类型化工具与 Scope 执行 | [`tools.py`](src/data_analysis_agent/tools.py) |
| 权限与审批策略 | [`permissions.py`](src/data_analysis_agent/permissions.py) |
| Teammate、租约与消息 | [`team.py`](src/data_analysis_agent/team.py) |
| SQL AST 与 PostgreSQL Gateway | [`sql_safety.py`](src/data_analysis_agent/sql_safety.py)、[`postgres.py`](src/data_analysis_agent/postgres.py) |
| Provider Adapter 与错误分类 | [`providers.py`](src/data_analysis_agent/providers.py) |
| Trace、Replay 与 Curator | [`tracing.py`](src/data_analysis_agent/tracing.py)、[`skills.py`](src/data_analysis_agent/skills.py) |
| Artifact、报告与 PDF | [`reporting.py`](src/data_analysis_agent/reporting.py)、[`pdf_export.py`](src/data_analysis_agent/pdf_export.py) |
| 元数据 Schema 与 Migration | [`db.py`](src/data_analysis_agent/db.py) |
| React Workbench | [`App.tsx`](web/src/App.tsx)、[`components/`](web/src/components/) |

## 信任边界与刻意取舍

这是一个高质量的 Agent Engineer 作品集垂直切片，不宣称是已经完成生产加固的企业 SaaS：

- Python 执行属于可信本地执行，不是针对恶意代码的安全沙箱。
- 当前是单用户、本地优先的工作台，不具备互联网多租户服务所需的身份、租户隔离和集中式密钥管理。
- 受控数据库路径目前只支持 PostgreSQL，并假设使用专用最小权限只读账户。
- `EXPLAIN` 风险分类可以降低查询风险，但不能保证数据库性能或真实运行时间。
- Token 估算是 Provider-neutral 的确定性预算信号，不等同于供应商计费 Token。
- Trace 写入是 Best-effort 的可观测性机制，不能替代原始运行错误。
- Teammate 是有界 Worker，不是自主运行的长期服务。
- Skill Candidate 即使经过人工批准也保持禁用，不会自动执行或修改系统能力。

这些限制是显式的系统边界，而不是通过 Prompt 假装已经解决的安全问题。

## 延伸文档

- [`docs/architecture.md`](docs/architecture.md)：组件架构
- [`docs/context-lifecycle.md`](docs/context-lifecycle.md)：Prompt、Context Projection、Checkpoint 与 Memory
- [`docs/sql-safety.md`](docs/sql-safety.md)：SQL 校验、风险与审批边界
- [`docs/multi-agent-runtime.md`](docs/multi-agent-runtime.md)：持久化、有边界的多 Agent Runtime
- [`docs/trace-and-skill.md`](docs/trace-and-skill.md)：Trace、Replay、Acceptance 与 Curator
- [`SECURITY.md`](SECURITY.md)：支持的信任边界与安全模型
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：本地开发和验证流程
- [`docs/portfolio-checklist.zh-CN.md`](docs/portfolio-checklist.zh-CN.md)：实现和测试对应的中文验收清单

---

如果只记住这个项目的一点：**大模型在系统中负责提出计划，而不是拥有权限；真正决定系统可靠性的，是模型之外的 Harness。**
