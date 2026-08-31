# TRACE · Governed Data Analysis Agent

一个本地优先、证据优先的受控数据分析 Agent 工作台。

模型负责理解问题、规划分析和解释结果。Harness 负责上下文、数据权限、工具执行、失败恢复、持久化状态与结果追溯。

![TRACE 工作台演示](docs/assets/workbench-tour.gif)

[快速开始](#快速开始) · [完整分析链路](#完整分析链路) · [系统架构](#系统架构) · [工程验证](#工程验证) · [设计文档](#设计文档)

## 这是什么

TRACE 是一个面向真实数据工作的 Agent Harness。它支持 CSV、Excel、本地 DuckDB、受控 Python 和只读 PostgreSQL，将一次分析中的计划、工具调用、审批、Agent 协作、数据产物和报告统一放进可观察的工作台。

这个项目关注的是模型真正开始行动之后的工程问题：

- 模型可以看到哪些数据，调用哪些工具；
- SQL 如何经过字段策略、风险检查和人工审批；
- 多个 Agent 如何协作，同时保持角色、数量和执行时间有界；
- 长对话如何压缩，但不覆盖完整审计记录；
- 失败如何停止、降级和恢复；
- 报告里的结论如何追溯到真正参与分析的数据产物。

系统把大模型视为不可信的规划器。模型只能提出消息和类型化 Tool Call，所有真实操作都由 Harness 校验和执行。

```text
Agent product = Model + Domain Harness

Data Analysis Harness
  = Business Knowledge
  + Governed Data Access
  + Context and State
  + Permissions and Approval
  + Recovery and Budgets
  + Artifact, Trace and Replay
```

## 60 秒看懂核心差异

| 工程问题             | TRACE 的处理方式                                     | 可验证结果                                 |
| -------------------- | ---------------------------------------------------- | ------------------------------------------ |
| 模型绕过数据权限     | SQL AST、字段策略、只读事务、一次性审批              | 模型拿不到数据库凭据，所有查询经过统一网关 |
| 多 Agent 无限扩张    | 固定角色、工具白名单、租约、轮次与数量上限           | 协作过程有界，可取消，可在重启后恢复状态   |
| 失败调用反复循环     | 错误分类、调用签名、独立重试预算、Fallback           | 相同失败不会重置预算，循环会确定性停止     |
| 长对话挤爆上下文     | 有界投影、Checkpoint、不可变 Transcript              | 模型请求保持有界，完整消息仍可审计         |
| 报告结论无法核验     | Artifact、Evidence Link、Trace、Replay               | 重要结论可以回到真实分析输入和执行事件     |
| 成功经验直接污染能力 | 人工接受、零工具 Curator、默认禁用的 Skill Candidate | 新能力必须经过显式审核，不会自动上线       |

## 完整分析链路

仓库内置了两套演示数据：一套用于 CSV / Excel 区域经营复盘，一套用于 PostgreSQL 经营分析。一次完整运行包含下面这些步骤：

1. 用户创建项目，上传数据集或配置只读 PostgreSQL 数据源。
2. 用户用自然语言描述分析目标，Lead Agent 创建持久化计划。
3. Lead 按需委派 Knowledge、SQL 和 Report Agent。
4. Knowledge Agent 检索项目级指标、术语和业务规则。
5. SQL Agent 查看策略过滤后的 Schema，并通过安全网关执行查询。
6. 受限字段或高风险查询会暂停任务，等待一次性人工审批。
7. 查询结果、图表和报告被注册为 Artifact，并记录 Evidence Link。
8. 工作台展示 Agent、工具、审批和 Artifact 的关联 Trace。
9. 完成的运行可以导出 Replay；人工接受后，Curator 才能创建待审核的 Skill Candidate。

```text
User question
  -> Lead plan
  -> Knowledge / SQL / Report teammates
  -> Governed tools and approvals
  -> Evidence artifacts
  -> Report
  -> Trace and offline replay
```

演示材料：

- [区域经营复盘](examples/region-business-review/README.md)：144 行合成经营数据，提供 CSV、Excel、问题清单和预期发现；
- [PostgreSQL 经营分析](examples/postgres-business-review/README.md)：可丢弃的 PostgreSQL Fixture 和完整演示问题；
- [Golden Trace](examples/traces/golden-run.json)：无需模型和数据库即可检查的确定性 Replay 样例。

## 系统架构

![TRACE Harness 架构](docs/assets/harness-architecture.png)

React Workbench 调用 FastAPI。SQLite 保存项目、任务、消息、审批、Agent Run、Context Checkpoint、Trace 和 Skill Candidate。分析产物写入项目工作区，报告通过 Artifact ID 引用证据。

```text
React Workbench
      |
      v
FastAPI and application services
      |
      +-- Lead runtime and bounded teammate manager
      +-- Context projection and checkpoints
      +-- Typed tool registry and permission policy
      +-- DuckDB / controlled Python execution
      +-- PostgreSQL safety gateway
      +-- Artifact, report and PDF services
      +-- Trace, replay and skill curation
      |
      +-- SQLite metadata
      +-- Project-local artifact workspace
```

### 受控工具执行

工具使用 Pydantic 输入模型和 Harness 持有的 Handler。未知工具默认拒绝，绝对路径、覆盖写、网络访问和依赖安装等动作不能因为模型提出请求就自动获得权限。

```text
Typed Tool Call
  -> argument validation
  -> project / task scope validation
  -> allow / approve once / deny
  -> bounded execution
  -> normalized result or safe error
  -> artifact and trace persistence
```

当前工具覆盖工作区检查、数据集检查、DuckDB 查询、受控 Python、PostgreSQL 查询、知识检索、Artifact 保存、报告发布、计划更新、用户确认和 Teammate 管理。

### PostgreSQL 纵深防御

`query_database` 不是数据库驱动的薄封装。查询依次经过：

```text
single SELECT AST allowlist
  -> schema and field policy snapshot
  -> per-task query budget
  -> bounded EXPLAIN risk classification
  -> optional one-time approval
  -> read-only transaction and timeouts
  -> row and byte limits
  -> evidence artifact and redacted trace
```

系统拒绝写操作、DDL、多语句、危险函数、系统目录访问，以及无法安全解析的 `SELECT *`。字段策略分为自动允许、需要审批和禁止访问。审批与任务、数据源、策略版本、受限字段或规范化 SQL 哈希绑定。

模型不会收到原始数据库连接。即使应用层校验存在缺陷，专用只读账户、只读事务、Statement Timeout、Lock Timeout 和结果预算仍然限制真实执行。

### 有界多 Agent 协作

Lead 是唯一直接面向用户的 Agent。它维护总体目标和计划，将不同工作委派给具备独立权限边界的 Teammate。

| 角色            | 职责                                    | 边界                                |
| --------------- | --------------------------------------- | ----------------------------------- |
| Knowledge Agent | 检索指标、术语、公式和业务规则          | 不能执行 SQL                        |
| SQL Agent       | 检查可见 Schema，提出并执行受控查询     | 没有数据库凭据，不能发布最终报告    |
| Report Agent    | 读取已注册证据，生成图表和报告          | 不能直接访问数据库                  |
| Curator         | 从人工接受的成功 Trace 中提炼候选工作流 | 工具白名单为空，候选 Skill 默认禁用 |

每个 Agent Run 都有独立消息、状态、工具白名单、最大轮次、墙钟截止时间、重试预算、租约和取消信号。Lead 最多同时协调三个 Teammate。进程启动时会将遗留的活跃工作标记为中断，避免已经失去 Worker 的任务继续显示为运行中。

### Context、Memory 与 Evidence

项目没有把所有状态笼统地称为 Memory：

| 状态               | 用途                                  | 变更方式                                     |
| ------------------ | ------------------------------------- | -------------------------------------------- |
| Transcript         | 完整的用户、Lead 和 Teammate 协议历史 | 只追加，不被压缩覆盖                         |
| Context Checkpoint | 压缩后的短期延续上下文                | 创建新版本，保留源消息                       |
| Business Knowledge | 项目级指标、术语和规则                | 显式创建，记录版本和来源                     |
| Artifact           | 数据结果、图表和报告                  | 注册到项目，携带 Provenance 与 Evidence Link |
| Trace              | 解释一次行动为何以及如何发生          | 按运行顺序持久化并脱敏                       |

模型调用前，Context Manager 会构建有界投影，压缩较早的消息，并替换过大的模型可见工具结果。完整 Transcript 不会因此被删除。如果 Provider 返回 `context_length_exceeded`，运行时会进行一次强制压缩并重试。

### Trace、Replay 与恢复

一个 `TraceContext` 贯穿 Lead、Teammate、Tool、SQL、Approval、Artifact 和 Curator。事件保存类型、状态、父子关系、尝试次数、耗时、安全错误分类和 Artifact 引用。敏感字段会递归脱敏，长 Payload 会在入库前截断。

Replay 读取已经持久化的事件，不会重新调用模型，也不会重新执行数据库查询。Provider 和 Tool 使用独立的重试策略；重复失败按规范化调用签名计数，达到预算后向模型返回明确的 Fallback 指令。

## 快速开始

环境要求：Python 3.12、Node.js 20。只有 PostgreSQL 演示需要 Docker Desktop。

### 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix web ci
```

### 零模型 Key 演示

确定性演示会创建一个独立工作区，真实执行 Lead、Report Agent、Python 分析、Artifact、报告和 Trace。它不需要模型 API Key，也不会污染日常工作区。

```powershell
data-analysis-agent demo --reset
```

启动 API：

```powershell
data-analysis-agent serve --data-dir .data-analysis-agent-demo
```

在另一个终端启动前端：

```powershell
npm --prefix web run dev
```

打开 `http://127.0.0.1:5173`。

### PostgreSQL 完整演示

```powershell
docker compose up -d postgres
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"
data-analysis-agent serve --host 127.0.0.1 --port 8765
```

Fixture 会在 Volume 首次创建时载入。进入 Workbench 后，使用上面的只读 DSN 创建 PostgreSQL 数据源，再复制 [演示问题](examples/postgres-business-review/question.md)。

仓库内密码只用于本地一次性 Fixture。不要提交真实 DSN 或 API Key。

重建演示数据库：

```powershell
docker compose down -v
docker compose up -d postgres
```

### 配置模型

Workbench 支持 DeepSeek、OpenAI，以及自定义 OpenAI-compatible Gateway。模型配置由后端保存，API Key 写入后不会通过普通配置接口返回前端。

不配置模型时，仍然可以运行测试、生成确定性 Demo，并检查 Golden Trace。

## 工程验证

以下结果来自当前工作区的本地验证：

| 检查                     | 结果                                                    |
| ------------------------ | ------------------------------------------------------- |
| 后端确定性测试           | 346 passed，1 个真实 PostgreSQL 测试因未提供 DSN 而跳过 |
| 前端测试                 | 49 passed                                               |
| 后端语句与分支综合覆盖率 | 85.09%，门槛为 85%                                      |
| Python 静态检查          | Ruff、mypy 通过                                         |
| 前端生产构建             | TypeScript 检查与 Vite build 通过                       |

确定性测试不需要模型 API Key，也不需要运行 PostgreSQL：

```powershell
python -m pytest --cov=data_analysis_agent --cov-report=term-missing -v
ruff check src tests scripts
ruff format --check src tests scripts
mypy src

npm --prefix web run format:check
npm --prefix web test -- --run
npm --prefix web run build
```

真实 PostgreSQL 集成测试是显式 Opt-in：

```powershell
$env:TEST_POSTGRES_ADMIN_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
$env:TEST_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"
python -m pytest tests/test_postgres_integration.py -v
```

重新生成并检查确定性 Golden Trace：

```powershell
python scripts/export_golden_trace.py
```

## 技术栈

- Backend：Python 3.12、FastAPI、Pydantic、SQLite
- Frontend：React 19、TypeScript、Vite
- Local analytics：DuckDB、Polars、受控 Python
- Database gateway：PostgreSQL、sqlglot、只读事务
- Reporting：Markdown、Plotly、PDF、Evidence Artifact
- Testing：Pytest、Vitest、确定性 Scripted Model、可选真实 PostgreSQL 集成测试

## 代码导航

| 模块                          | 入口                                                                                                                                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| API 组合与生命周期            | [`api.py`](src/data_analysis_agent/api.py)                                                                                                                               |
| Lead Model / Tool Loop        | [`agent.py`](src/data_analysis_agent/agent.py)                                                                                                                           |
| Prompt 组装                   | [`prompts.py`](src/data_analysis_agent/prompts.py)                                                                                                                       |
| Context 预算与 Checkpoint     | [`context.py`](src/data_analysis_agent/context.py)                                                                                                                       |
| 类型化工具与 Scope 执行       | [`tools.py`](src/data_analysis_agent/tools.py)                                                                                                                           |
| 权限与一次性审批              | [`permissions.py`](src/data_analysis_agent/permissions.py)                                                                                                               |
| Teammate、租约与消息          | [`team.py`](src/data_analysis_agent/team.py)                                                                                                                             |
| SQL AST 与 PostgreSQL Gateway | [`sql_safety.py`](src/data_analysis_agent/sql_safety.py)、[`postgres.py`](src/data_analysis_agent/postgres.py)                                                           |
| Provider Adapter 与错误分类   | [`providers.py`](src/data_analysis_agent/providers.py)、[`recovery.py`](src/data_analysis_agent/recovery.py)                                                             |
| Trace、Replay 与 Curator      | [`tracing.py`](src/data_analysis_agent/tracing.py)、[`skills.py`](src/data_analysis_agent/skills.py)                                                                     |
| Artifact、报告与 PDF          | [`workspace.py`](src/data_analysis_agent/workspace.py)、[`reporting.py`](src/data_analysis_agent/reporting.py)、[`pdf_export.py`](src/data_analysis_agent/pdf_export.py) |
| 元数据 Schema 与 Migration    | [`db.py`](src/data_analysis_agent/db.py)                                                                                                                                 |
| React Workbench               | [`App.tsx`](web/src/App.tsx)、[`components/`](web/src/components/)                                                                                                       |

## 信任边界

TRACE 是单用户、本地优先的 Agent Engineer 作品集，不是已经完成生产加固的互联网 SaaS。

- Python 执行面向可信本地代码，不是针对恶意代码的安全沙箱。
- 当前没有互联网多租户服务需要的身份系统、租户隔离和集中式密钥管理。
- 受控数据库路径目前只支持 PostgreSQL，并假设使用专用的最小权限只读账户。
- `EXPLAIN` 是查询风险信号，不能保证真实执行时间或数据库性能。
- Context Token 使用确定性近似值做预算，不等于供应商计费 Token。
- Trace 是 Best-effort 可观测性记录，不会覆盖原始运行错误。
- Teammate 是有界 Worker，不是自主运行的长期服务。
- Skill Candidate 即使通过人工审核也保持禁用，不会自动执行或修改系统能力。

这些边界由代码和部署方式明确表达，不依赖 Prompt 假设模型会主动遵守。

## 开发方式

本项目约 95% 的代码由 AI Coder 在我的指导下生成或修改。我的工作集中在产品定义、架构、能力边界、任务拆解、验收测试和失败闭环。

这个比例本身不能证明质量。仓库里的状态不变量、安全策略、确定性演示、静态检查和回归测试才是可以复核的结果。

## 设计文档

- [组件架构和状态存储](docs/architecture.md)
- [Prompt、Context Projection、Checkpoint 与 Memory](docs/context-lifecycle.md)
- [SQL 校验、风险分类与审批边界](docs/sql-safety.md)
- [持久化、有边界的 Multi-agent Runtime](docs/multi-agent-runtime.md)
- [Trace、Replay、Acceptance 与 Curator](docs/trace-and-skill.md)
- [信任边界与安全模型](SECURITY.md)
- [本地开发和验证流程](CONTRIBUTING.md)
- [实现和测试对应的中文验收清单](docs/portfolio-checklist.zh-CN.md)
