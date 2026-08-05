# 迹析 · Data Analysis Agent

迹析是一个本地优先、产物可追溯的数据分析 Agent。对话负责表达目标和追问；分析计划、执行记录、代码、数据集、图表与报告则持续保存在项目工作台中。当前 MVP 支持 CSV/Excel、只读 DuckDB 查询、受控 Python 分析、证据引用报告和 OpenAI 兼容模型接口。

## 环境要求

- Windows 10/11 与 PowerShell
- Python 3.12（当前包明确限制为 `>=3.12,<3.13`）
- Node.js 20 或更高版本
- 一个支持 Chat Completions 与 function calling 的 OpenAI 兼容模型服务

## 安装

在项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix web ci
```

如果 PowerShell 阻止激活脚本，可以直接把后续命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。

## 配置模型

三个变量缺一不可。`DAA_MODEL_BASE_URL` 应指向兼容服务的 API 根路径，程序会在其后请求 `/chat/completions`。

```powershell
$env:DAA_MODEL_BASE_URL = "https://your-model-service.example/v1"
$env:DAA_MODEL_API_KEY = "your-api-key"
$env:DAA_MODEL_NAME = "your-function-calling-model"
```

模型配置只从当前进程环境读取，不会写入项目数据库。没有配置模型时，项目、数据上传和工作台仍可使用，但新分析任务不会自动执行。

## 本地运行

打开两个 PowerShell 窗口，并在两个窗口中激活同一个虚拟环境。

窗口一启动 API：

```powershell
data-analysis-agent serve --host 127.0.0.1 --port 8765
```

窗口二启动工作台：

```powershell
npm --prefix web run dev
```

然后访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到本地 API。

## 推荐体验流程

1. 新建项目，例如“经营复盘”。
2. 上传 `tests/fixtures/operations.csv`。
3. 新建任务：“为什么 East 区域利润下降？”
4. 在任务工作台查看计划修订、执行时间线、代码、派生表、图表与最终报告。
5. 打开报告中的 `artifact://...` 证据引用，核对结论对应的持久化产物。
6. 任务完成后继续追问，例如“再和 West 比较”。即使重启应用，追问仍在同一个任务和项目上下文中继续。
7. 当操作越过自动权限边界时，在对话侧栏查看审批卡；批准只对完全相同的一次操作生效，使用后即失效。

## 数据目录

默认运行数据保存在当前目录的 `.data-analysis-agent/`，结构如下：

```text
.data-analysis-agent/
├── metadata.sqlite3
└── projects/
    └── <project-id>/
        ├── source/      # 导入后不可由分析工具修改的原始数据
        ├── derived/     # 派生数据集
        ├── code/        # 每次 Python 分析代码
        ├── charts/      # HTML 或图片图表
        ├── reports/     # Markdown 报告
        ├── logs/        # 执行日志
        └── knowledge/   # 预留的业务知识目录
```

SQLite 保存项目、任务、消息、计划修订、工具执行、权限审批、事件、产物关系与报告证据关系。删除 `.data-analysis-agent/` 会删除所有本地项目历史，请先备份。

## 信任边界

本产品会在本机子进程中执行模型生成的 Python，因此只适用于本地可信用户和可信模型输出。当前控制包括项目路径约束、原始数据恢复保护、声明输出、超时、有限环境变量、危险导入/调用拦截、禁止任意 Shell、禁止外部网络工具，以及只读 SQL 校验。

这些控制用于降低误操作风险，不是针对恶意代码的安全沙箱。不要把本服务直接暴露到公网，也不要用它执行不受信任人员提交的代码。原始数据不会被分析工具覆盖；变换结果必须写入 `derived/` 等产物目录。

## 验证

所有后端测试都使用确定性模型替身，不需要真实 API Key：

```powershell
python -m pytest -v
python -m pytest tests/test_acceptance.py -v
npm --prefix web test -- --run
npm --prefix web run build
```

端到端验收会导入经营数据、执行分析代码、生成派生 CSV 和图表，并发布含有效证据关系的 Markdown 报告。

## MVP 边界

当前版本不包含数据库直连、云端多人协作、定时任务、多 Agent 调度、Word/PPT 导出或完整 Notebook 编辑器。外部文件读取、联网和依赖安装也不是模型可自动调用的能力。
