# ProData 与 Trace 合并实现方案（审查稿）

## 这份方案要做什么

本文对照 ProData 文档代码目录 `E:\77-自进化数据分析类智能体-prodata\77-自进化数据分析类智能体-prodata` 和 Trace 项目 `E:\agent\Data-Analysis-Agent`，说明双方能接在哪里、哪些做法值得复用、合并时有哪些容易忽略的冲突，以及最后建议采用的数据流。

目标是保留 Trace 现有的业务流程、数据权限和用户界面，只把 Trace 现在通过模型服务获取回答的部分，换成使用 ProData 的 SFT/RL 方法训练并在本地运行的模型。这里的“不调 API”指不再向外部大模型服务发送推理请求；Trace 自己给网页使用的 FastAPI 接口仍然保留。方案采用进程内本地推理，不要求额外启动一个 OpenAI-compatible 模型 HTTP 服务。

还有一个需要提前说明的范围：ProData 的 RL 脚本 `multi.sh` 设置了 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`WANDB_API_KEY` 和 `SWANLAB_API_KEY`；DataCOPE 的部分数据生成/Skill 流程也有模型 Provider 配置。因此“在线不用外部模型 API”可以通过本地 Provider 实现；若要求数据生成、训练、日志也完全不访问外部服务，则实施时还要逐项确认这些配置的实际调用点，并关闭外部模型与云端日志，改用本地模型/本地日志。训练脚本里出现这些变量不等于 RL rollout 一定调用了云模型，但不能在未核验前宣称整条训练链路完全离线。

本文先作为实现设计和审查材料。当前已完成第一阶段的 Trace 代码接入：本地模型 Provider、共享模型配置，以及失败运行复盘候选的受控生成；尚未训练模型，也没有把 DataPRM 或 DataCOPE 的完整运行框架接入线上。合并时仍需要制作适配 Trace 工具协议的训练数据和训练环境，ProData 现成的 checkpoint 不会因为换一个模型路径就自动兼容 Trace。

## 先直接回答：Trace 哪些模块要改

如果你的目标是“Trace 原业务流程不变，只把外部大模型换成本地 SFT/RL 模型”，主要改下面这些位置：

| 优先级 | Trace 文件/目录 | 要改什么 | 是否改变原业务流程 |
| --- | --- | --- | --- |
| 必须 | `src/data_analysis_agent/providers.py` | 新增本地模型 Provider。把 Trace 的消息和工具 Schema 交给本地模型，并把模型输出解析成现有的 `ModelResponse` / `ToolCall`。 | 否，只换模型回答从哪里来 |
| 必须 | `src/data_analysis_agent/api.py` | 应用启动时初始化并共享一个本地模型运行时；把它注入 Lead、teammate、Curator 的 Provider。现在后台任务和 teammate 会各自重新组装 services，因此要避免每个运行器重复加载一份大模型。 | 否，仍使用现有 Agent 和工具流程 |
| 必须 | `src/data_analysis_agent/settings.py` | 增加本地 checkpoint 路径、推理设备、精度、上下文长度和并发配置。 | 否，只增加本地模型配置 |
| 必须 | `src/data_analysis_agent/model_config.py`、`src/data_analysis_agent/db.py` | 把当前保存云端 provider/base URL/API Key 的模型配置改成或补充为本地模型配置，并处理数据库迁移。若模型路径只通过环境变量配置，可缩小数据库改动。 | 否，只改变模型配置来源 |
| 必须 | `src/data_analysis_agent/prompts.py` | 明确模型必须输出 Trace 现有的结构化工具动作，保持 Lead/Knowledge/SQL/Report/Curator 的角色和工具白名单语义。 | 否，角色职责不变 |
| 需要本地模型设置界面时 | `web/src/components/ModelSettings.tsx`、`web/src/types.ts`、`web/src/api.ts`，以及 `src/data_analysis_agent/api.py` 的 `/api/model-config` | 把“选云端厂商、填 URL 和 Key”改成“查看/选择本地模型版本及状态”；若模型只由部署环境配置，则移除前端填写云端 Key 的流程。 | 不改变分析流程，只改变配置界面 |
| 建议 | `src/data_analysis_agent/context.py` | 使用本地模型 tokenizer 计算输入长度，或至少让上下文上限与本地模型的真实限制一致。 | 否，只影响上下文预算 |
| 新增 | `training/`（目录名可定） | 新增 Trace 轨迹导出/脱敏/校验、SFT 数据转换、RL rollout 环境、奖励计算、离线评测和训练配置；调用 ProData 的 LLaMA-Factory/verl 训练方法。 | 在线业务不变；新增离线训练流程 |
| 验证 | `tests/test_providers.py`、`tests/test_api.py`、相关 Agent/Team 测试 | 验证本地 Provider 的工具调用解析、无效输出拒绝、模型只初始化一次，以及审批和角色白名单仍生效。 | 只增加测试 |

**不会因为换模型而修改的核心业务模块：** `tools.py`、`permissions.py`、`postgres.py`、`sql_safety.py`、`execution.py`、`repositories.py`、`tracing.py`、`reporting.py` 和前端工作台的任务/Artifact/Trace 页面。模型仍然只能提出工具调用，执行、权限、审批、证据保存和 Replay 继续由这些 Trace 模块负责。

最小改动顺序是：`providers.py` → `api.py`/`settings.py` → 模型配置 → `prompts.py` → 新增训练目录 → 测试。`agent.py` 和 `team.py` 已经依赖通用 Provider 接口，原则上不需要为了换成本地模型重写；只有当本地推理需要新增取消、流式输出或特殊并发控制时，才扩展它们。

## 再直接回答：是换模型吗？DataPRM、DataCOPE 接不接？

**在线使用时，核心变化确实是把 Trace 的外部模型 Provider 换成本地 SFT/RL 模型。**模型仍通过 Trace 的 Provider 接口产生文本或工具调用；Trace 原有的工具执行、权限、审批、任务状态、证据保存和 Trace 记录都继续运行。因此准确说是“替换模型推理来源”，不是把 Trace 的 API 服务、Agent Runtime 或工具系统换成 ProData。

| ProData 部分 | 本方案第一版是否直接接入 Trace | 具体关系 |
| --- | --- | --- |
| SFT/RL 训练方法 | 是，作为训练手段 | 用 ProData 的训练代码/方法训练一个遵循 Trace 工具协议的模型；部署后由本地 Provider 推理 |
| DataPRM | 否，不作为第一版必需组件 | 第一版先用可解释的规则和工具执行结果做离线 RL 奖励与评测。后续若静默错误难以识别，可再评估把 DataPRM 用作离线过程评分器；它不替代 Trace 的权限、审批或在线校验 |
| DataCOPE | 不接入它的完整运行框架 | 只借鉴数据集/Agent/Verifier 可插拔、轨迹筛选和能力迭代的思路。Trace 仍运行自己的 AgentRunner、TeamManager、ToolRegistry 和 Skill Candidate 审核流程 |
| DataCOPE Skill Manager 的自动迭代闭环 | 否 | 不把生成的 `SKILL.md` 自动注入下一轮线上任务；Trace 的候选 Skill 仍须人工审核并保持禁用 |

当前代码还增加了一个受控的失败复盘入口：失败运行必须由用户显式请求复盘，Curator 才能生成 `failure_review` 类型候选。该候选与成功流程候选分开标记，仍保持 `enabled=false`，审批后也不会自动注入线上提示词。

所以第一版可以概括为：**ProData 负责提供 SFT/RL 训练方法；训练出的本地模型替代 Trace 的外部模型服务；DataPRM 暂不接入；DataCOPE 只参考部分方法，不搬它的 Agent/Skill 运行框架。**

### 通俗解释：DataPRM 和 DataCOPE 是什么，为什么先不接

**DataPRM 可以理解成“检查解题过程的教练”。**普通判分通常只看最后答案对不对；DataPRM 还会检查分析中间步骤有没有根据数据、计算是否合理。在 ProData 的做法中，它可以主动写代码验证某些中间结果，并给步骤打分。这样训练模型时，不只奖励“最后蒙对”，也能鼓励可靠的分析过程。

第一版先不接 DataPRM，是因为 Trace 里的任务不只有算数字：还涉及不同角色、SQL 审批、工具权限、报告引用和 Artifact。要让 DataPRM 判断这些步骤，需要先把 Trace 的每类动作和中间结果转换成它能检查的格式，再验证它的评分确实可靠。若评分器把错误步骤判成好步骤，RL 反而会把模型往错误方向训练。因此先用 Trace 已经能确认的工具结果、答案和规则做基线；发现这些信号抓不住关键错误后，再单独评估接入 DataPRM。

**DataCOPE 可以理解成“做题、复盘、整理方法、再做下一轮”的智能体迭代框架。**它让数据分析 Agent 先完成任务，Verifier 再比较答案或轨迹，Skill Manager 从经验中整理出 `SKILL.md`，之后 Agent 带着这些方法继续做任务。重点是让智能体从过去的轨迹里积累可复用经验。

第一版不搬入完整 DataCOPE，是因为 Trace 已经有自己的 Agent 分工、权限白名单、SQL 审批、工具执行和 Trace 记录。完整接入会多出一套 Agent Runtime 和执行流程；而 DataCOPE 的 Skill 可以进入后续运行，Trace 当前则要求 Skill 先经过人工审核并保持禁用。两套方式直接叠加会重复管理任务和工具，也会让“哪些经验能自动改变线上行为”变得不清楚。因此先借鉴它的轨迹复盘和可插拔设计，训练数据仍从 Trace 受控导出，线上仍走 Trace 自己的流程。

## 先看两个项目各自在解决什么问题

Trace 是一个有项目隔离的分析工作台。用户通过网页创建项目、上传数据、设置数据源和业务知识，再提交分析任务。Lead Agent 按职责调用 Knowledge、SQL、Report teammate；工具执行前会经过参数校验、项目范围、权限和审批检查。结果保存成有来源关系的 Artifact 和报告；Trace 记录运行事件，支持 Replay。成功运行也不会自动变成生效 Skill，必须由人接受运行、审核候选 Skill。

可从 Trace 的 `src/data_analysis_agent/api.py`、`agent.py`、`tools.py`、`team.py`、`tracing.py`、`skills.py`、`context.py`、`postgres.py` 和 `execution.py` 看到这些边界。

ProData 的重点是训练数据分析模型和改进 Agent 能力。目录里包含 ProData-12K 数据和 SFT 配置、基于 verl 的多轮 rollout/RL 训练代码、DataPRM 过程奖励相关代码，以及 DataCOPE 的 Agent/Verifier/Skill Manager 循环。DataCOPE 通过 Agent registry 接不同的 Agent harness，通过 Dataset/Verifier registry 接数据集和验证方式；ReAct harness 使用 `<python>`、`<answer>` 之类的文本标记，并由 AllocatedCodeEnv 执行代码。

可重点查看 `ProData/prodata/train/sft/LLaMA-Factory/examples/train_full/`、`ProData/prodata/train/rl/verl/multi.sh`、`ProData/prodata/train/rl/verl/agent/async_interpreter.py`、`ProData/datacope/general/src/core/loop.py`、`src/da_agent/da_agent.py`、`src/runtimes/agents/react_ds_agent.py`、`src/verifier/` 和 `src/skill_manager/`。

所以，合并不是把一个 Agent 整个搬进另一个项目，而是让 ProData 提供训练方法和训练好的本地模型，让 Trace 继续负责任务、工具、权限、持久化、证据和用户交互。

## 一、可以对接的接口

### 最合适的接点：Trace 的模型 Provider

Trace 已把模型调用收在统一的 `ModelProvider.complete(ModelRequest) -> ModelResponse` 接口下。请求包含系统提示、任务目标、对话消息和当前可用的工具 Schema；响应包含文本或一个/多个 `ToolCall`。`AgentRunner` 和 `TeammateRunner` 通过 Provider 生成下一步，再把工具动作交给 Trace 自己的 `ToolRegistry` 执行。

建议新增一个本地 Provider，直接读取本地 SFT/RL checkpoint，在 Python 进程内调用本地推理引擎。该 Provider 把 Trace 的 `ModelRequest` 转成模型训练时使用的消息格式，再把模型输出解析回 Trace 的 `ModelResponse`。这样更换的是模型推理来源，不是业务工具的执行权。

Trace 的 Provider 工厂和服务组装位于 `api.py` 的 `build_services`、`_build_services`、`AppServices.resolve_provider` 一带。Lead、teammate 和 Curator 都需要接到同一个本地模型运行时，避免只替换 Lead 而漏掉其他模型调用。现有 `ScriptedProvider` 保留，用于不加载模型的确定性测试。

### 需要共享的消息和动作格式

训练数据需要覆盖 Trace 真正使用的消息类型：`system`、`user`、`assistant`、`assistant_tool_call` 和 `tool`。工具调用必须带上 Trace 当前可用的工具名和 JSON 参数；工具结果必须能被下一轮模型读取。训练时还要保留角色信息，因为 Lead、Knowledge、SQL、Report、Curator 的职责和工具白名单不同。

建议为训练数据和本地 Provider 定义一份稳定的“动作协议”：一轮模型输出只能是“最终答复”或“一个/多个工具调用”，工具名与参数遵循 Trace 的 Schema。Provider 负责解析、校验模型输出并为调用生成 ID；格式错误或未知工具名作为安全的模型输出错误处理，绝不能绕过 `ToolRegistry` 直接执行。

### 其他能接的接口

| 接口或边界 | 可对接方式 | 适用范围 |
| --- | --- | --- |
| ProData Dataset / Agent / Verifier registry | 参考其注册方式，在 Trace 的训练侧提供数据适配器和评测器 | 训练数据导入、任务集扩展和离线评测，不替换 Trace 在线工具 |
| ProData SFT 数据和训练配置 | 把已审核的 Trace 轨迹转换成兼容 SFT 的多轮训练样本，再用 ProData 的 LLaMA-Factory 配置思路训练 | SFT 冷启动 |
| ProData verl 多轮 rollout / GRPO / DAPO | 为 Trace 的工具协议实现训练用环境和奖励函数，再运行 ProData 的 RL 方法 | 第二阶段 RL；不能直接拿当前线上 Trace 数据库当训练环境 |
| Trace Replay / Trace event | 从已脱敏并经筛选的轨迹导出训练记录；训练结果带模型版本和数据版本 | Trace 回流到离线训练 |
| ProData AllocatedCodeEnv 的多轮交互思想 | 只参考“生成动作—执行—观察—继续”的多轮形式 | 不照搬 `<python>` 协议或替换 Trace 的安全工具层 |

Trace 网页本身还有一组 FastAPI REST 接口，负责项目、任务、数据源、审批、Artifact、Trace 和 Replay。这些是产品功能接口，不是模型 Provider 接口，应继续保留。两边没有一个可直接复用的“模型 API”：ProData 的训练项目和 Trace 的 Provider 接口采用不同的动作协议。

## 二、可以复用的模块和做法

### 建议直接保留在 Trace 的部分

Trace 的项目/任务/团队管理、角色工具白名单、SQL AST 校验、只读事务、人工审批、Python/DuckDB 执行、Artifact Provenance、业务知识、Context Checkpoint、Trace 脱敏和 Replay 都是在线产品逻辑。换成训练模型之后，这些仍然是模型必须经过的固定关口。

其中最重要的是 `ToolRegistry.execute()`：即使模型由本地训练，模型输出也仍然是不可信输入。参数 Schema、任务所属项目、权限级别、审批、超时和错误恢复继续由 Trace 控制。模型训练不能替代这些检查。

### 建议复用 ProData 的训练方法

- **SFT 数据整理方法**：把高质量的多轮任务、动作、工具返回和最终答复整理成监督样本；使用 ProData 仓库里的 LLaMA-Factory 数据/配置实践作为训练起点。现成配置包括 `prodata_12k_full_sft.yaml` 和 `prodata_da_dataset_full_sft.yaml`，但模型、模板、轨迹字段和训练硬件都要按 Trace 数据重新核对。
- **由易到难和硬筛选**：借鉴 ProData 按任务动作拆分、组合难度、再经过格式/执行/答案校验的思路，为 Trace 产生训练样本。训练样本不能只因为模型认为“看起来正确”就入库；要以 Trace 的工具返回、数据验证、证据引用和人工确认作为筛选依据。
- **多轮 RL 训练**：借鉴 ProData 对多轮 rollout、GRPO/DAPO、全零奖励组过滤和轨迹有效长度处理的经验。Trace 的任务也是多轮“观察—工具动作—反馈—继续”，适合按完整轨迹评估，而不是只把最后一句话当作全部训练信号。
- **过程质量信号**：借鉴 DataPRM 主动验证中间步骤的想法。第一版应先做可解释的规则/执行器奖励，例如 SQL 是否只读、结果是否来自实际工具、引用 Artifact 是否存在、输出 Schema 是否正确；DataPRM 可作为之后评估的一种本地过程评分信号，不能直接替代 Trace 的权限校验或人工审批。
- **可插拔注册方式**：借鉴 DataCOPE 的 Agent/Dataset/Verifier registry，在训练和离线评测侧添加不同模型、数据集或评测方法时少改主流程。Trace 线上仍由它自己的 AgentRunner、TeamManager 和 ToolRegistry 决定执行。

### 适合做的小幅业务精进

1. **把训练数据做成“通过审核的轨迹集”**：现在 Trace 的 Curator 产生的是 Skill 候选，不是训练集。建议新增独立的训练样本导出/审核流程，明确记录原 Run、角色、动作协议版本、标签来源和脱敏状态。这样人工接受一个 Run 不会暗中等同于“授权它进训练集”。
2. **把“任务跑完”和“分析正确”分开**：Trace 的 `completed` 主要表示 Agent 正常结束；ProData 常用答案匹配、Self-Consistency、PRM 等质量信号。建议线上保留原状态定义，同时为离线评估增加 `answer_correctness`、`tool_grounding`、`evidence_validity`、`policy_compliance` 等独立字段，不用一个“成功率”混在一起。
3. **做分阶段模型发布**：先 SFT 模型影子/离线回放，再 SFT+RL 模型做同一批任务比较，只有通过回归门槛的 checkpoint 才能成为默认本地模型。发布记录模型哈希、训练数据版本、动作协议版本和评测报告，出现问题可回滚 checkpoint。

## 三、合并时最容易被忽略的冲突点

### 概念冲突

| 名词 | Trace 的含义 | ProData 的含义 | 合并后的统一说法 |
| --- | --- | --- | --- |
| Agent | 有持久运行状态、独立消息、角色工具白名单、租约/超时/取消的 Lead 或 teammate | 负责跑数据分析任务的 harness，通常连接模型后端和分析执行环境 | “模型”负责提出动作；“Agent Runtime”负责持久状态、分工、工具调用和约束 |
| Tool | Trace 中具备固定 Schema、项目范围、权限检查和可能审批的业务动作 | ReAct 环境里主要是 Python/SQL 执行或代码容器能力 | 统一称 Trace 工具；训练环境要模拟同名工具动作，但线上永远由 Trace 执行 |
| Skill | 从人工接受的 Trace 生成、经静态检查和人工审核、默认禁用的候选工作流 | DataCOPE 的 `SKILL.md` 是后续运行可加载/修改的任务策略，会参与下一轮执行 | “候选工作流”与“已启用运行 Skill”分开；初版只生成 Trace 候选，不自动加载或执行 |
| Verifier | 在线安全/状态检查分布在 SQL Gateway、权限、Artifact 和 Trace Replay 等模块 | ProData Verifier 主要按 Ground Truth 或不同轨迹答案一致性整理成功/失败轨迹 | 分成“在线安全校验”“离线答案/质量评测”和“轨迹一致性分析”三种名称 |
| Reward | Trace 没有一个单值 Reward 能代表所有成功条件 | ProData RL 训练用 outcome/过程奖励给 rollout 提供学习信号 | Reward 是离线训练信号，不是线上授权；分项记录，不改变 Trace 的安全决策 |
| Model API | Trace Provider 当前可连接 OpenAI-compatible 服务，也可注入 ScriptedProvider | ProData 有训练方法、不同后端和 vLLM 本地模型服务脚本 | “替换外部模型推理 Provider”，而非“取消 Trace 的 HTTP API” |

### 口径冲突

- **答案准确率和轨迹质量不是一个数。** ProData 的数值答案/字符串答案匹配以及 Self-Consistency，可用于离线比较；Trace 的完整 Trace 还包含工具执行、审批、Artifact 和错误恢复。轨迹完整不代表最终业务结论正确；答案相同也不代表过程安全。
- **任务完成状态不是标签真值。** Trace 的 `completed`、ProData Verifier 的 `success`、RL 的 `seq_final_reward` 都有不同定义。训练数据需保存各自字段及产生方式，不能把其中一个直接映射为另外两个。
- **工具失败不总是模型失败。** Trace 会区分 SQL 风险、审批等待、数据库不可用、工具超时、模型输出错误等情况。只有模型可控制的错误才应成为训练惩罚；外部数据库临时故障等应标为环境噪声，防止模型学会逃避正常任务。
- **self-consistency 不等于正确性。** 多个 rollout 给出同一答案只能表示一致，不证明数据来源、指标定义和计算过程正确。应使用正确答案/规则验证/Artifact 证据/人工审核补足。
- **成功率分母要统一。** 评测需明确排除哪些任务、超时是否计失败、需要人工审批的任务如何计数，以及一个任务多轮/多次 rollout 是按任务还是按轨迹计数。报告中分开给出答案正确率、工具动作合规率、证据有效率、任务完成率和资源/延迟，不把它们混成单一准确率。

### 叙事冲突

ProData 的 DataCOPE 强调从轨迹提炼并改进 Skill；Trace 则明确把 Skill Candidate 留在禁用状态，避免自动改变线上能力。合并后的叙事建议写成：

> “Trace 是负责受控数据分析和证据审计的工作台；SFT/RL 是其中用于提升模型决策能力的训练组件，ProData 的轨迹学习和 Skill 提炼方法为它提供训练经验。”

这样既承认训练和自进化能力是组件，也不会让人误以为 Trace 已经变成自动改写自身权限或流程的 Agent 平台。模型可以学习更好的分析步骤，但它不能自行扩大白名单、跳过审批、改写业务知识或启用 Skill。

### 动作协议和执行环境冲突

这是最关键的工程冲突。ProData ReAct 轨迹中的模型可能输出 Python/答案标记，再由 AllocatedCodeEnv 执行；Trace 的模型应输出结构化 `ToolCall`，再由 `ToolRegistry` 检查并执行。若训练时教模型输出自由 Python，在线解析器又期待 JSON 工具调用，模型可能答非所问；若把 ProData 执行器直接接入在线链路，还会绕开 Trace 已有的工具白名单、SQL 风险检查和审批。

因此要把 Trace 的工具 Schema 和消息顺序转成训练格式。训练用环境可以调用 Trace 的同一批只读分析逻辑或隔离副本，产生与线上相同形状的工具结果；真实数据库凭据、审批消费、跨项目数据和发布报告等副作用不能在 RL rollout 中开放。

## 四、合并后的统一数据流

```text
用户在 Trace 创建任务
  -> Trace 组装项目知识、角色提示、历史消息和允许工具
  -> 本地 SFT/RL 模型在当前进程内生成“最终答复”或“结构化工具动作”
  -> Trace 校验动作 Schema、角色白名单、项目范围、权限与审批
  -> Trace 执行 SQL / DuckDB / Python / 知识检索 / teammate / Artifact 等原有动作
  -> 工具结果回到模型上下文；Trace 同时追加消息、事件、错误分类和 Artifact 关系
  -> Lead/Teammate 继续多轮，最后形成原有答复或证据链接报告
  -> 人工接受一个成功运行，并单独审核该运行是否可作为训练来源
  -> 脱敏、筛选、标注、按项目/任务隔离切分训练集和留出集
  -> ProData SFT 训练冷启动模型；在隔离训练环境中用多轮 rollout + 验证奖励做 RL
  -> 离线回归评测并登记 checkpoint、数据版本、动作协议版本
  -> 通过审核的模型 checkpoint 作为新的本地 Provider 模型版本
  -> 后续 Trace 运行继续沿用同一业务链路
```

线上推理时不直接复用 ProData 的 vLLM HTTP server 示例，也不向云端模型发送请求。目标部署方式是在 Trace 服务进程中只加载一个本地推理运行时，通过进程内方法生成结果；多 Agent 并发请求需进入一个有界推理队列或加锁，避免每个 teammate 重复加载一份模型、耗尽 GPU。若之后因性能必须拆成单独推理进程，需要另行决定内部通信方式，但这不属于本方案的第一版目标。

## 五、Trace 需要改哪些部分

具体文件级清单见文档开头“先直接回答：Trace 哪些模块要改”。实现时有三个关键点：

1. `providers.py` 只负责模型输入/输出适配；不在 Provider 里执行 SQL、Python 或其他工具。
2. `api.py` 负责把共享的本地模型运行时注入所有 Runner。否则当前后台任务、teammate 的独立 service 构造可能导致重复加载大模型。
3. SFT/RL 数据和 rollout 环境是新增的离线训练代码，不是把 `tools.py` 或 `team.py` 整个替换成 ProData 的实现。

本地推理对象应由应用生命周期创建；Lead、teammate、Curator 可以各自拿到轻量 Provider，但它们共用同一模型实例，并通过有界队列或锁安排推理请求。

## 六、ProData 训练流程如何适配 Trace

### SFT 阶段

1. **挑选来源**：以合成任务、公开/脱敏任务和人工确认可复用的 Trace 为主。不要默认把任意项目中的 Transcript、SQL、原始数据行、凭据或业务机密纳入训练。
2. **轨迹转换**：把每轮“模型工具调用—Trace 工具返回—模型后续动作”转换成多轮对话样本；不同角色使用明确的角色标记。拒绝或审批等待也要有样例，使模型学会按 Trace 协议提出动作，而不是编造权限。
3. **硬校验**：校验动作 JSON、工具名/参数、工具结果关联、最终答案格式、Artifact 引用和来源；把无效或标签不确定轨迹隔离，不作为正例。
4. **训练**：使用 ProData 仓库的 LLaMA-Factory SFT 方法训练。ProData 原有 `<python>/<answer>` 数据可提供分析知识/代码能力，但需要重写动作外壳，不能直接与 Trace function-call 样本混用而不做协议适配。

### RL 阶段

1. 先有能遵守动作协议的 SFT checkpoint，再以该 checkpoint 初始化 RL。
2. 在 ProData verl 的多轮 rollout/GRPO/DAPO 思路上搭 Trace 训练环境。rollout 的工具必须是沙箱/只读/固定数据的副本；无用户生产写操作、无真实审批消费、无跨项目访问。
3. 奖励至少拆分成可观察的几项：最终答案是否正确；工具名和参数是否符合 Schema；是否正确利用工具返回；报告引用的 Artifact 是否存在且同项目；是否遵守角色和只读规则；是否按要求结束。环境故障、缺失真值、人工审批暂停应单独标记，不能误当作模型推理错误。
4. 可借鉴 ProData 的全零 reward 组过滤和混合 SFT/RL 稳定训练方式，但其参数（如 rollout 数、最大轮次、损失比例）不能直接照抄。应根据 Trace 的动作空间、轨迹长度、模型规模和 GPU 条件重新评估。
5. 首版不强制引入 DataPRM。先建立可解释的执行器/规则奖励和轨迹级评测；如果静默分析错误仍难以检测，再离线接入 DataPRM 或训练兼容的过程评分器，并验证评分器与真实答案、人工判断之间的相关性。

## 七、评测和上线门槛

新模型应与当前 Provider 版本在同一份冻结任务集上对比。指标分开报告：

- **答案质量**：有 Ground Truth 的任务按匹配规则评估；开放式报告由盲评或结构化 rubric 评估。
- **工具动作质量**：有效工具调用率、参数 Schema 通过率、错误重试后恢复率、重复无效动作率。
- **证据质量**：重要结论是否指向同一项目中已注册的 Artifact，引用是否有效。
- **安全/权限**：SQL/工具违规尝试数、审批是否被正确等待、跨角色/跨项目越权是否为零。该部分需设置硬门槛，不能用答案准确率提升抵消违规。
- **业务运行**：任务完成率、超时率、延迟、显存和并发占用。Trace 的 `completed` 仍只作运行状态，不替代答案正确率。

上线前最少完成：本地 Provider 的离线加载/卸载和无外网推理验证；四类常见动作与工具返回的解析测试；无效 JSON/未知工具的拒绝测试；各角色白名单和审批流程的回归测试；代表性任务的 Trace Replay 对照；SFT 与 SFT+RL 对冻结集的对比；安全硬门槛和资源使用检查。测试中仍可注入 `ScriptedProvider`，不需每次都加载大模型。

建议按“离线转换和评测 → 本地 SFT Provider 小范围接入 → 多轮 RL → 新旧模型对照发布”的顺序实施。模型 checkpoint 通过门槛后才改为默认版本，并保留上一版 checkpoint 便于回滚。

## 八、审查时请重点确认

1. “不调 API”按本方案解释为：不调用外部模型推理服务，模型在 Trace 服务进程内本地推理；Trace 自身给 UI 用的 HTTP API 继续存在。这个边界是否符合你的要求。
2. ProData 原有的自由 Python 动作不会成为 Trace 在线动作；在线模型使用 Trace 的结构化工具协议。训练阶段需要为此生成新轨迹格式。
3. 线上 SQL、Python、审批、Artifact、权限和 Replay 继续由 Trace 处理；训练出的模型只有提出动作的能力，没有执行权限或越权能力。
4. 轨迹进入 SFT/RL 前需要独立筛选和脱敏，不把“运行成功”或“人工接受 Trace”自动当作正确标签或训练授权。
5. 实际 SFT/RL 是否可运行取决于可用 GPU、目标基座模型和训练数据规模；文档中的 ProData 参数是参考经验，不保证可直接移植到 Trace。
6. Skill 首版继续走 Trace 的候选—人工审查—禁用状态，不自动应用 DataCOPE 的下一轮 Skill 注入行为。

## 依据代码位置

- Trace Provider 与对话协议：`src/data_analysis_agent/providers.py`、`agent.py`、`team.py`
- Trace 工具参数、权限和执行：`src/data_analysis_agent/tools.py`、`permissions.py`、`postgres.py`、`execution.py`
- Trace 持久化、Context、Replay 与 Skill Candidate：`src/data_analysis_agent/tracing.py`、`context.py`、`skills.py`、`api.py`
- ProData SFT 配置：`ProData/prodata/train/sft/LLaMA-Factory/examples/train_full/prodata_12k_full_sft.yaml`、`prodata_da_dataset_full_sft.yaml`
- ProData 多轮 RL 配置和执行：`ProData/prodata/train/rl/verl/multi.sh`、`agent/async_interpreter.py`、`agent/tensor_helper.py`
- ProData DataCOPE 循环和适配接口：`ProData/datacope/general/src/core/loop.py`、`da_agent/da_agent.py`、`runtimes/agents/react_ds_agent.py`、`verifier/`、`skill_manager/`
- ProData 本地模型评测示例：`ProData/prodata-analysis/eval/local_model.sh`。它通过 vLLM 暴露本地兼容接口；本方案只借鉴其 checkpoint/模型运行思路，目标运行时不采用该 HTTP 调用方式。
