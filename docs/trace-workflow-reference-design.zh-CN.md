# Trace 工作流程优化：开源实现调研与分阶段方案

调研日期：2026-10-28。状态：供决策的设计稿，尚未实施业务代码修改。

本文接续 `trace-harness-optimization-review.zh-CN.md`，采用用户修正后的顺序：**结构化交接 → 结束前验收 → 完整任务约定**。任务复杂度分级和事件唤醒暂缓。外部项目用于验证设计机制，不以 star 数推断正确性，也不建议直接更换 Trace 的运行框架。

## 1. 调研对象、源码版本与结论

star 数来自调研时 GitHub API 返回值，只是规模快照。源码链接固定到本次读取的 commit；官方在线文档可能继续更新。

| 项目 | 本次读取的规模 / 版本 | 确认的实际机制 | Trace 值得借鉴的部分与边界 |
| --- | --- | --- | --- |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | 20,918 stars；`bc2213364ca0` | 用输出类型生成 JSON Schema，并校验返回数据；可另挂输出 validator，失败反馈可触发受预算限制的输出修正 | 同一份 Pydantic 定义用于提示与解析；业务校验单独执行。类型正确不代表 Artifact 真实或数字正确 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 44,025 stars；`eb8ec111b3e9` | 显式状态和条件路由；有 recursion limit、剩余步数；触顶有错误路径，也支持提前进入收尾路径 | 剩余额度由 Harness 持有，耗尽前保留收尾机会。它没有替 Trace 定义分析验收规则，也不会自动提供业务上的“部分完成”模型 |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | 14,348 stars；`4a272d2b8e96` | executor 声明输入 / 输出类型，构建时检查边类型兼容；循环由 continuation predicate 和最大次数共同控制；待审批响应会先退出自主循环 | 类型化交接、继续条件与硬上限分离、审批优先返回。其 agent looping 文档标为 experimental；这里只借鉴经过源码和测试确认的机制 |
| [OpenHands](https://github.com/OpenHands/OpenHands) 与 [Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | 主仓库 92,052 stars；SDK 1,312 stars；所读 SDK commit 为 `46b1fa5cb91a` | `FinishAction` / `FinishTool` 明确发出结束动作；执行状态区分 finished、error、stuck 等 | 借鉴“结束是显式动作”及终止原因。FinishTool 的说明也允许因限制而结束，源码执行器没有业务正确性检查，不能把它宣传成结果验收器 |
| [AutoGen](https://github.com/microsoft/autogen)（历史参考） | 61,599 stars；`004749e6b710` | 可组合消息数、token、超时等终止条件；结构化消息与停止原因分开 | 参考终止条件组合，不新增依赖。所读 README 已声明转向 Microsoft Agent Framework；API 的 archived 字段当时仍是 false，故不据此断言平台归档状态 |

关键证据：

- Pydantic AI：[输出文档](https://pydantic.dev/docs/ai/core-concepts/output/)，[固定版本的输出验证实现](https://github.com/pydantic/pydantic-ai/blob/bc2213364ca0318f4077110bbc9c240c0ee2f7cd/pydantic_ai_slim/pydantic_ai/_output.py)，[输出 Schema 测试](https://github.com/pydantic/pydantic-ai/blob/bc2213364ca0318f4077110bbc9c240c0ee2f7cd/tests/test_agent_output_schemas.py)。
- LangGraph：[剩余步数与提前收尾](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit)，[触顶抛错实现](https://github.com/langchain-ai/langgraph/blob/eb8ec111b3e9d3108af5d7bdcac51e3e913637a0/libs/langgraph/langgraph/pregel/main.py#L3093)，[中断恢复的幂等要求](https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent)。
- Microsoft Agent Framework：[executor 类型兼容检查](https://github.com/microsoft/agent-framework/blob/4a272d2b8e96242a1e00783e833d42922a618d23/python/packages/core/agent_framework/_workflows/_validation.py#L198)，[先检查循环硬上限](https://github.com/microsoft/agent-framework/blob/4a272d2b8e96242a1e00783e833d42922a618d23/python/packages/core/agent_framework/_harness/_loop.py#L775)，[循环与审批文档](https://learn.microsoft.com/en-us/agent-framework/agents/looping)，[上限和待审批退出测试](https://github.com/microsoft/agent-framework/blob/4a272d2b8e96242a1e00783e833d42922a618d23/python/packages/core/tests/core/test_harness_loop.py#L1371)。注意：边类型兼容检查不等于运行时逐字段验证，更不等于业务验证。
- OpenHands SDK：[FinishAction 与执行器](https://github.com/OpenHands/software-agent-sdk/blob/46b1fa5cb91ada17e6ec938c7e8624a4fa19fa98/openhands-sdk/openhands/sdk/tool/builtins/finish.py)，[执行状态](https://github.com/OpenHands/software-agent-sdk/blob/46b1fa5cb91ada17e6ec938c7e8624a4fa19fa98/openhands-sdk/openhands/sdk/conversation/state.py#L48)。SDK 的 star 数与主仓库分开统计。
- AutoGen：[README 当前说明](https://github.com/microsoft/autogen/blob/004749e6b7102c8fd317b9b1499ce5807223c278/README.md)，[终止条件实现](https://github.com/microsoft/autogen/blob/004749e6b7102c8fd317b9b1499ce5807223c278/python/packages/autogen-agentchat/src/autogen_agentchat/conditions/_terminations.py)，[TaskResult 的停止原因](https://github.com/microsoft/autogen/blob/004749e6b7102c8fd317b9b1499ce5807223c278/python/packages/autogen-agentchat/src/autogen_agentchat/base/_task.py)。

**综合判断：可以复用的共同机制是类型化输入输出、应用校验、有限修正和明确终态。Artifact 归属、数据口径、数字复算和部分完成资格，必须由 Trace 自己定义。** 后文均为针对 Trace 的设计推导，不声称这些项目已经原样实现。

## 2. 对当前 Trace 的补充核对

用户指出的交接 Schema 缺失、无交付验收、计算与报告混杂均成立。本次又发现两项需要纳入工作量的前置条件。

### 2.1 DuckDB 查询还没有稳定的证据产物

`execution.py` 的 `DuckDBExecutor.query`：结果不超过 200 行时只返回 preview，`artifact` 为 None；超过 200 行才写 Parquet，而且注册时 `task_id=None`。`tools.py` 的 `query_data` 原样返回这一结果。

因此，直接要求“SQL 交接必须给出当前任务的 Artifact”会拒绝不少正常本地分析。必须先保证需要用于交付和验证的查询结果有持久证据，并记录当前任务 / 工作轮次 / 工具执行的关联。

输入和输出还要区别处理：项目上传的源数据本来可能 `task_id=None`；显式允许复用的历史产物也不应被一概拒绝。严格的当前任务约束主要针对**本次声称新生成的输出**。

### 2.2 当前完成事务还会自动把未完成计划改为完成

`TaskRepository.transition_in_transaction` 在任务进入 COMPLETED 时调用 `_complete_latest_plan_in_transaction`，将最新计划的 PENDING / RUNNING 步骤批量改为 COMPLETED。`tests/test_tasks.py` 也验证了这一旧行为。

因此结束验收不能只在 `AgentRunner._complete_task` 前加一个 if。否则“部分完成”的任务一旦沿用完成事务，尚未做完的步骤仍会被标绿。需要改成按真实步骤收据更新；其余步骤保留原状态，或在执行终止时记为 SKIPPED 并附原因，不能标记为已完成。

## 3. 建议的新流程

```text
用户输入
  → 创建工作轮次及最小交付配置（无额外复杂度分类调用）
  → Lead 规划并创建类型化 WorkOrder
  → 角色 Agent 执行现有受控工具
  → Pydantic 解析 Handoff
  → Harness 校验引用、执行来源与必要业务规则
  → 校验收据进入持久化状态，Lead 只消费已通过边界校验的交接
  → Lead 提出最终输出候选
  → CompletionService 决定：接受 / 修正 / 等待输入 / 部分交付 / 失败
  → 写入明确的结果元数据；符合资格且人工接受后才可提炼成功经验
```

结构修复只修格式与字段；业务修正才允许回到取数或分析。二者使用独立、持久、受总预算约束的计数。现有审批和角色权限全程继续生效。

## 4. 第一阶段：真正定义交接协议

### 4.1 用同一份定义约束提示和代码

建议新增 `handoffs.py`，定义三种不同的 Pydantic 输出模型。用 `model_json_schema()` 生成对应角色提示中的 Schema；运行时按持久化 `AgentRun.role` 选择解析器，不能由模型自报 role 决定权限。统一设置 `extra="forbid"`，并限制文本、列表长度和引用数量。

保留现有 64,000 字节上限；新增模型应小于该上限。Curator 的现有候选 Markdown 协议单独处理，不强行套用业务交接格式。

| 模型 | 建议字段 | 必须由 Harness 做的核对 |
| --- | --- | --- |
| KnowledgeHandoff | `protocol_version`、`status`、`summary`、`definitions`、`unresolved_questions`；definition 带 `knowledge_id`、`version` 及采用的口径 | 知识条目存在、项目范围允许、版本匹配；允许没有找到定义并返回 blocked，不能伪造一个定义当作 ready |
| SqlHandoff | `protocol_version`、`status`、`summary`、`output_artifact_ids`、`limitations`；后续加 `claims` | 输出存在且可读，确为本轮工具生成或显式复用；输入血缘允许；ready 状态必须满足相应 WorkOrder 的输出要求 |
| ReportHandoff | `protocol_version`、`status`、`summary`、`report_artifact_id`、`chart_artifact_ids`、`claim_ids`、`limitations` | 报告类型、来源和引用关系有效；要求报告时不得只返回文字摘要；图表是否必需由交付配置决定 |

`status` 建议限定为 `ready / partial / blocked`，它只表达该子任务的业务结果。teammate 的 `AgentRunStatus.COMPLETED` 仍可表示交接协议处理完毕，两者不能互相替代。blocked 状态必须有阻塞原因，partial 必须有可交付内容及剩余缺口。

例如 SQL 交接的最小模型可以如下定义；这是拟议协议示例，不是已经接入的代码：

```python
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=1000)]
ArtifactId = Annotated[str, Field(min_length=1, max_length=128)]

class SqlHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1"]
    status: Literal["ready", "partial", "blocked"]
    summary: ShortText
    output_artifact_ids: list[ArtifactId] = Field(max_length=16)
    limitations: list[ShortText] = Field(max_length=8)

    @model_validator(mode="after")
    def check_status_fields(self):
        if self.status in {"ready", "partial"} and not self.output_artifact_ids:
            raise ValueError("ready/partial requires evidence artifacts")
        if self.status in {"partial", "blocked"} and not self.limitations:
            raise ValueError("partial/blocked requires limitations")
        return self
```

运行时先检查字节上限，再按可信角色调用相应模型的 `model_validate_json(raw)`；然后调用独立的 HandoffValidationService 查数据库和证据文件。上述模型仅约束结构与状态字段关系，绝不承担 Artifact 真实存在、归属或数字验证。ready 是否满足 WorkOrder、模型是否隐瞒 limitations，仍分别由应用校验及任务评测处理。

**不要接受模型写的 `verified=true`。** 模型只能提出事实及证据引用。验证收据由应用创建，例如 `rule_id / rule_version / subject_id / result / evidence_hash / checked_at`，并与工作轮次关联。

### 4.2 输入也要结构化，但先控制范围

`SpawnTeammateInput` 第一版可增加：`input_artifact_ids`、`knowledge_refs`、`expected_output_kind`、可选的 `plan_step_id`。Harness 将其验证后保存成 WorkOrder，绑定真实 task、cycle、role 和策略范围；这些可信标识不由模型自由填写。

下游收到明确的输入和预期输出，不再单靠 objective 重述整个上下文。完整的业务任务约定可以后做，但交接所需的输入引用和技术输出要求不能省略。

新建 teammate 前校验：所用 plan step 属于当前有效 revision；Artifact 在项目范围及当前允许输入集中；知识版本存在。审批结果仍经原网关处理，WorkOrder 不提供新的执行授权。

### 4.3 修复反馈必须可执行且有上限

将现有“follow the required handoff schema”改为从真实模型定义生成的提示，并附脱敏后的结构错误，例如：

```json
{
  "code": "HANDOFF_SCHEMA_INVALID",
  "errors": [{"field": "output_artifact_ids", "reason": "required_for_ready"}],
  "repair_remaining": 1
}
```

最多允许一次额外结构修复。修复模式实际发送空工具列表，并拒绝任何工具调用，不能仅在提示中要求“不要调用工具”。如果缺的是数据或产物，则交给业务修正路径，不能把重新执行混入格式修复。

旧的 `{"summary":"..."}` 历史交接不回写、不伪造新字段；读取时明确标为 legacy_unvalidated。新创建运行执行 v1；旧记录展示和 Replay 保持可读，旧结果若用于新轮次必须重新校验或显式导入。

### 4.4 证据产物化是这一阶段的组成部分

对 `query_data` 的分析结果，不论是否少于 200 行，都生成或注册可复用的受控证据快照。保留 preview 作为模型视图，完整结果在受限行数 / 字节预算内保存。零行结果也保留 Schema 和行数，以区分“确实为空”与“没有执行”。

建议由工具上下文提供 task / cycle / execution 关联，复用现有 `tool_executions` 记录；工具 handler 当前只返回产物 ID，落地时需要将 registry 已创建的 execution ID 传给 handler 或在同一可信提交路径补全关联，不能让模型提供 producer ID。

首版无需把 Artifact 表全部改成 run 唯一归属。可以增加“工作轮次—产物—用途—生产执行”的关联表，兼容共享源数据和显式复用；为新证据保存内容摘要，读取时核对；对已有 `task_id=None` 或没有内容摘要的历史产物不批量猜测归属或验证状态。

产物校验规则：

| 类型 | 允许条件 |
| --- | --- |
| 本次新输出 | 本轮成功工具执行产生，有可信生产关联，类型符合预期 |
| 项目源数据 | 在 WorkOrder 明确输入集中，项目范围及当前权限有效 |
| 历史派生产物 | 明确登记为复用输入，重新检查范围、版本 / 内容摘要和当前策略 |
| 其他任务或项目的未授权产物 | 拒绝，不能因为模型知道 ID 就导入 |

## 5. 第二阶段：把停止条件和完成验收分开

### 5.1 增加 CompletionService，先兼容现有模型输出

借鉴 OpenHands 的显式结束动作，但第一版不必立即新增 `finish_task` 工具或改变已适配的本地 JSON 动作协议。Lead 无工具输出时，将其视为 FinalCandidate，交给 CompletionService；不要先把它展示为已完成的最终回答，再进行验收。

CompletionService 根据持久化状态生成决策，不能相信候选中自报的“已经完成”。原始终止输出可以留作受控审计记录，但验收不通过时不应按正式交付展示。

第一版在任务创建 API 增加可选 `delivery_mode=answer|report`，默认 answer；UI 可提供显式选择，已有调用方仍可不填。它只登记最小技术交付要求，不执行模型复杂度分类。模型不能自行把 report 降级为 answer。

若只有自然语言而没有登记交付要求，首版只能验证技术证据充分性，不能声称已完整理解和验收所有用户意图。完整的目标、口径、交付项映射留到第三阶段；这是能力边界，需要在结果中保留。

### 5.2 明确首版验收规则

| 规则 | 代码检查对象 | 不通过后的处理 |
| --- | --- | --- |
| 没有尚未处理的执行 | 活跃 teammate、进行中的工具执行 | 等待已有工作，不重复派发 |
| 没有待消费的人类决定 | pending approval / 真实缺失输入 | 沿用 NEEDS_INPUT；审批等待不算分析错误，也不消耗业务修正次数 |
| 必需交接已通过 | 本轮 WorkOrder 对应的交接收据 | 结构问题走一次修复；业务缺口进入有限修正 |
| 交付物存在 | answer 的证据或知识引用；report 的 Markdown Artifact | 缺失则不能 full；不得用最终回答文本代替要求的报告 |
| 引用具有当前有效性 | 产物关联、知识版本、内容摘要、策略范围 | 失效引用重新验证；越权和完整性问题禁止继续包装成成功 |
| 必需检查已通过 | 本轮要求的 verification receipts | failed 进入修正；unverified 不自动变成 passed |
| 计划状态符合事实 | 必需步骤的执行 / 验证收据及显式跳过原因 | 不再批量将 pending 步骤改为 completed |

第一阶段只完成引用层校验时，规则只能证明“证据存在且来源合规”。API 应标示 `verification_level=reference`；等第三阶段数值检查上线后再标 numeric，避免把技术验收通过宣传成所有结论正确。

### 5.3 部分完成：推荐增加结果元数据，而非立即扩充 TaskStatus

两种方案都可行：

| 方案 | 优点 | 代价 |
| --- | --- | --- |
| 新增 `TaskStatus.PARTIALLY_COMPLETED` | 前端状态直观 | 要修改任务转移、运行状态、重试 / 追问、列表筛选、SSE、成功接受和 Curator 资格，迁移面较大 |
| 保留执行状态，新增 `outcome` 与验收元数据（推荐） | 区分“执行已结束”和“交付是否完整”，兼容现有终态结构 | 仍必须修改数据库、API、前端、计划与经验入口，不能只存一个未展示的 JSON 字段 |

建议按每一轮用户请求建立持久化 WorkCycle（初始请求一轮，后续新追问新一轮），保存：

```text
cycle_id / task_id / revision
delivery_policy_version / delivery_mode
outcome = full | partial | none
verification_level = reference | numeric | legacy_unverified
deliverable_ids / missing_requirement_ids / stop_reason
format_repair_used / analysis_repair_used
```

同一个 cycle 可关联多次 analysis run，以适应当前审批后继续运行和重试可能开启新 Trace 的方式。预算按 cycle 保存；审批恢复、更换 teammate 和服务重启不刷新预算。

Task 的 COMPLETED 在新方案中只表示这一轮执行结束；API 返回对应 outcome。UI 同时显示“已完成”“部分交付”或“未通过验收”，不能继续把所有 completed 显示成 Analysis complete。已有历史结果没有收据，标为 legacy_unverified，不推断 full。

分流条件如下：

- **full**：明确登记的必需交付项和检查全部满足。
- **partial**：至少有一项可独立交付、已通过所需检查的结果；其余缺口明确；没有影响该已交付部分的权限或完整性问题。
- **NEEDS_INPUT**：确有尚待用户决定或补充的信息，任务可继续；不是达到修正上限后的通用出口。
- **failed / none**：没有可交付结果，或错误破坏了整体可信性。达到预算上限不能自动生成一个“部分成功”。
- **cancelled**：尊重取消，不通过额外模型调用强行收尾；已有产物可以保留，但不自动变为成功结果。

必须同步修改 `TraceRepository.accept_run`、`_assert_skill_eligible` 以及相关 UI 按钮：只有本轮 full、满足所需检查且被人工接受的运行，才进入“成功经验”入口。partial 可另行设计复盘，不应默默变成成功训练样本；第一版可以直接禁用其成功提炼按钮。旧记录也不得因缺少元数据而默认获得新资格。

### 5.4 三类上限及恢复行为

建议初始值为：每个 teammate 一次额外格式修复；每个 cycle 最多两次业务修正；另保留并正确执行现有总动作、工具重试和墙钟上限。这些数值是待验证的 Trace 初值，不是照抄外部项目默认值。

每次先检查权限 / 取消 / 审批，再检查是否还有执行额度。业务修正必须对应具体失败规则和受影响步骤；不允许因换 Agent 或重建 Trace 重新获得两次额度。达到上限后，由应用按已有收据生成缺口清单和终态；不要依赖最后一个模型调用保证能够收尾。

同一规则、同一输入快照连续失败且没有新证据时，可以提前停止。数字错误或缺产物通常可修正；跨项目访问、证据篡改等不能通过“再试一次”变成可接受路径。

## 6. 第三阶段：把数字验证定义成真正可执行的规则

“报告关键数字能在 Artifact 中找到”只能作初步检查。同一个 100 可能对应不同地区、单位或时间。建议将关键数字表达为 Claim，并精确绑定证据位置和口径。

示意模型（仅为拟新增协议，不是现有接口）：

```text
NumericClaim
  id / metric_id / metric_version
  period / dimensions / unit
  displayed_value / display_scale / rounding_digits
  evidence_refs: artifact_id + row_key + column
  calculation: direct | difference | growth_rate | contribution_sum
```

验证器自行读取固定快照；规则 ID、允许计算类型、舍入方式和容差由 Harness 配置控制。禁止由模型传任意 Python / SQL 给验收器执行，也不允许模型把容差改大。被验证报告的一个数字不能同时充当独立真值。

| 规则 | 确切检查 | 例子及边界 |
| --- | --- | --- |
| REF_EXISTS_SCOPE | 引用存在、可读、类型正确，并满足 WorkOrder 的输入 / 输出范围 | 不能引用外项目 ID；项目级源数据必须是已登记输入 |
| REF_FRESHNESS | 证据内容摘要、口径版本与收据一致 | 文件被更改后旧的 passed 收据失效；首版需增加内容摘要或不可变快照 |
| CELL_VALUE_MATCH | row_key 唯一定位一行，读取指定列；按声明单位换算和受控舍入后比较 | 不能通过全文搜索找到同一个数字就算通过；结果最多证明数值引用吻合 |
| DELTA_RECOMPUTE | 从两个已核对的基期 / 当期值独立计算差值 | 1000→850 的变化应为 -150，不能写 +150 |
| RATE_RECOMPUTE | 从基期和当期复算增长率，核对百分比与百分点 | 1000→850 为 -15%；基期为 0 时返回 undefined / unverified，不编造有限增长率 |
| CONTRIBUTION_RECONCILE | 分项维度互斥且覆盖声明范围，分项合计与独立计算的总变化一致 | -80、-50、-20 合计 -150；若有未解释残差，单列 residual，不自动记为全解释 |

金额使用 Decimal 和业务配置的精度；相对容差与绝对容差由指标类型确定。分母为零、重复 row_key、缺失值、单位不明都要显式处理。分项加总只证明分解在数学上闭合，不能证明因果关系；完整的归因方法和开放式建议仍需人工或后续评分体系。

Report Agent 最好传入关键 Claim ID，由模板渲染这些数值，减少“验证侧是 -15%，Markdown 手写成 +15%”的问题。首版只对显式登记的关键 Claim 承诺覆盖，不宣称能自动识别所有自由文本中的重要结论。完整开放式报告质量判定另立评测。

再在 Lead 第一轮规划中补完整任务约定：目标、口径、范围、交付项、必需 Claim 和验收规则。复用原有规划模型调用；不新增独立复杂度分类模型。Harness 校验引用、规则枚举及必要字段，用户仅处理真正未解决的业务歧义。任务约定的修改必须版本化并使相关旧收据失效，不能通过降低要求绕过验收。

## 7. SQL / Python / Report 职责怎样调整

这个缺口成立，但不建议和交接第一阶段一起改：同时变更角色权限与输出协议，会使失败原因难以定位，也会影响已有本地模型适配与测试。

先让 Report Agent 的输出区分“分析结果”和“展示产物”，用明确血缘与规则约束当前流程。若真实任务表明 SQL 后的 Python 计算确实频繁且复杂，再选择：

1. 将现有 SQL Agent 扩展为 Analysis Executor，可用受控 SQL 与 Python，Report 仅用已注册结果表达；这需要更新角色名 / 枚举兼容、提示、白名单、前端、测试和训练数据协议。
2. 仅增加受限的 Python 分析能力或执行模式，继续保留内部 sql 角色名，减少持久化迁移，但对外必须清楚解释角色职责。

不为此自动增加第四个并发 Agent。Python 仍经原执行器及权限；职责重分不能解决沙箱隔离问题。

## 8. 修改位置与最小测试集合

以下均为拟议改动。

| 范围 | 主要文件 | 必须验证的行为 |
| --- | --- | --- |
| 交接 Schema 与 WorkOrder | 新增 `handoffs.py`；`tools.py`、`team.py`、`prompts.py` | 各角色合法样本；缺字段、错角色、额外字段；空 / 超长 / 非有限数；JSON Schema 与实际解析同源；一次修复后确定性失败；修复模式不能调用工具 |
| 引用与产物化 | `execution.py`、`tools.py`、`repositories.py`、`db.py` | 0 / 1 / 200 / 201 行均有证据；新输出有可信归属；共享输入可用；跨任务未授权复用拒绝；丢失文件和旧内容摘要失效 |
| 结束验收与预算 | 新增 `completion.py`；`agent.py`、`settings.py`、持久化层 | 无工具文本不能空跑成功；已有 teammate 仍自动 await；缺报告拒绝 full；两次修正后停止；审批等待不扣修正次数；换 teammate / 恢复不重置预算 |
| 部分结果与计划 | `domain.py`、`repositories.py`、`db.py`、`api.py`、`tracing.py` | partial 有合格交付及缺口；无交付仍失败；pending 步骤不再自动完成；历史数据迁移可读；follow-up 新轮次不改写旧 outcome |
| 前端与经验入口 | `web/src/types.ts`、`api.ts`、`Conversation.tsx`、`ProjectNav.tsx`、`ArtifactWorkspace.tsx`；`skills.py` | partial 清晰展示；列表和详情一致；SSE 后状态更新；partial / legacy_unverified 不能按新规则提炼成功经验；full 仍需人工接受 |
| 数值规则 | 新增 `verification.py`；`reporting.py`、证据读取服务 | 同数异口径、错误正负号、百分比 / 百分点、重复键、零分母、单位换算、容差、残差、模板与 Claim 不一致 |

优先扩展现有 `test_teammate_runner.py`，同时调整 `test_tasks.py` 中“完成时批量关闭计划”的旧假设；结束流程还需覆盖 `test_agent.py`、`test_api.py`、`test_skill_curator.py` 和前端状态测试。只补 teammate 单测无法覆盖部分完成的跨层行为。

协议更新也会改变现有脚本化测试中的简短 JSON 样本，需要集中提供每角色 fixture。建议增加一条完整验收：查询→结构化交接→报告→检查失败→局部修正→full；另一条故意无法修正→partial / failed；审批恢复路径单独覆盖。

## 9. 工作量和可选决策包

按熟悉项目的一名开发者估算，含实现、针对性测试和基本 UI 联调；不含模型训练、长时间模型评测或生产部署。不同项共用基础设施，不能再机械叠加旧评审中的工时。

| 决策包 | 内容 | 粗估 | 能得到什么；尚不能证明什么 |
| --- | --- | --- | --- |
| **S：交接基础包** | 三角色 Pydantic 协议、WorkOrder、一次结构修复、查询证据产物化与归属 | **3–5 日** | 交接可解析、引用有来源；尚不能阻止所有不完整终止，也不能证明数字或结论正确 |
| **M：可验收流程包（推荐）** | S，加结束验收、持久预算、部分结果元数据、计划完成语义修正、UI 与成功经验入口联动 | **累计 7–11 日** | 明确的交付项不会被无条件标成完成；修正有上限；参考层检查可重复验证；完整自然语言意图覆盖仍有限 |
| **L：数值与任务约定包** | M，加有限计算类型的 Claim 校验、关键数字模板化、完整任务约定与版本失效规则 | **累计 10–16 日** | 选定指标的数值与口径检查可执行；不等于开放式归因和建议全部正确 |
| 后置角色重分 | SQL / Python 分析执行职责调整 | 另约 3–6 日 | 减少计算和表达混杂；应有真实任务和基线再决定 |

**我的选择建议：先选 S，用现有样例和故意损坏的交接验证收益；如果目标包含可靠交付与 Harness 展示，再选 M。** L 适合开始有明确的数值错误案例和评测任务时投入。S 的源码模型部分可能很小，但产物化、历史兼容与已有测试调整决定了它不是“加三个 Pydantic 类”就结束。

事件唤醒保留为后置性能优化：当前已有自动 await，重点观察 await 超时后的额外 Lead 调用数量，再判断收益。任务复杂度分级最后做；优先使用显式交付模式和实际执行信号，不新增前置分类模型。

## 10. 评估是否值得做的衡量方法

这些项目的实现只能证明机制可行，不能证明 Trace 必然获得某个提升比例。建议在固定模型、固定数据上保留当前版本作基线，记录：

- 损坏或无证据的交接被接受的次数，以及合法交接被错误拒绝的次数。
- 必需交付缺失但任务仍标为完成的次数；与独立任务清单对照，不以 Harness 自己的验收结果充当全部真值。
- 达到修正上限后是否确定性停止；审批恢复是否保持预算和一次性授权。
- 数值检查对预先构造错误的检出率、误拒率，以及对真实报告的覆盖范围。
- 额外模型调用数、总 token、端到端时间，及 partial 结果的可用性人工复核。

先用确定性 fixture 证明边界，再用代表性真实模型任务测副作用。高 star、typed output 或有限循环本身，都不是最终分析质量的证据。

本次工作仅做文档与源码调研，未安装这些框架、未迁移运行时、未实施或测试上述新协议。原有用户代码修改保留；已读取的相关原始源码和官方链接在本文中列明。
