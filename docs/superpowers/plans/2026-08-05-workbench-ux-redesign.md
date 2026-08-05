# 迹析工作台体验重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让项目管理、分析画布和对话协作在同一视口内各司其职，长对话不再制造中间空白，并让核心 Agent 结论沉淀到工作台。

**Architecture:** 保持现有 React 单页和 API 不变，只调整组件接口、语义结构与样式。`App` 管理对话栏开合状态，`ProjectNav` 呈现项目列表，`TaskWorkbench` 从任务消息中提取最近 Agent 结论，`Conversation` 负责独立滚动消息和固定输入。

**Tech Stack:** React 19、TypeScript 5.7、Vite 6、Vitest、Testing Library、原生 CSS。

## Global Constraints

- 不新增运行时依赖。
- 不改变后端 API、任务领域模型或已有模型配置流程。
- 桌面端三栏高度固定为视口高度且各自滚动。
- 现有数据上传、任务切换、审批、发送消息功能必须保持可用。
- 所有新增交互提供可访问名称和键盘焦点样式。

---

### Task 1: 锁定工作台核心交互行为

**Files:**
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: 现有 `fakeApiWithCompletedTask()` 测试夹具。
- Produces: 项目列表、当前结论、对话开合的行为契约。

- [ ] **Step 1: 写项目列表测试**

新增测试，断言项目名称以按钮呈现、当前项目按钮具有 `aria-current="page"`，并可直接切换项目。

- [ ] **Step 2: 写当前结论测试**

在完成任务夹具中加入足够长的 Agent 消息，断言中间工作台存在“当前结论”标题与消息正文。

- [ ] **Step 3: 写对话开合测试**

断言点击“收起对话”后对话区域消失、“展开对话”按钮出现；再次点击后对话区域恢复。

- [ ] **Step 4: 运行测试并确认按预期失败**

Run: `npm test -- --run src/App.test.tsx`

Expected: 新增断言因项目仍为下拉框、没有当前结论、没有收起按钮而失败；原有断言继续通过。

---

### Task 2: 重构项目栏与对话开合

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/ProjectNav.tsx`
- Modify: `web/src/components/Conversation.tsx`

**Interfaces:**
- Consumes: `projects`, `selectedProjectId`, `onSelectProject` 和现有消息、审批回调。
- Produces: `conversationOpen: boolean` 页面状态；`Conversation` 的 `onCollapse(): void`；语义化项目按钮列表。

- [ ] **Step 1: 在 `App` 管理对话栏开合**

新增 `conversationOpen` 状态。任务存在且打开时渲染 `Conversation`；关闭时给根节点增加 `conversation-collapsed` 类，并在 `TaskWorkbench` 上方传入 `onOpenConversation` 回调。

- [ ] **Step 2: 把项目选择器改为按钮列表**

在 `ProjectNav` 中移除原生 `<select>`，用 `<ul className="project-list">` 和项目按钮渲染全部项目。选中按钮设置 `aria-current="page"`，项目标题区提供可切换的新建表单。

- [ ] **Step 3: 为对话栏增加收起操作**

给 `ConversationProps` 增加 `onCollapse(): void`，在标题栏渲染“收起对话”按钮。保持消息输入 state 在组件未被卸载时不丢失；`App` 关闭时隐藏而不是重建消息数据。

- [ ] **Step 4: 运行聚焦测试**

Run: `npm test -- --run src/App.test.tsx`

Expected: 项目列表和对话开合测试通过；当前结论测试仍失败。

- [ ] **Step 5: 提交交互结构**

```powershell
git add web/src/App.tsx web/src/components/ProjectNav.tsx web/src/components/Conversation.tsx web/src/App.test.tsx
git commit -m "feat: restructure project and conversation navigation"
```

---

### Task 3: 把 Agent 核心输出沉淀到中间工作台

**Files:**
- Modify: `web/src/components/TaskWorkbench.tsx`

**Interfaces:**
- Consumes: `TaskDetail.messages`，其中 `role === "assistant"` 表示 Agent 输出。
- Produces: “当前结论”卡片；`onOpenConversation(): void` 回调入口。

- [ ] **Step 1: 提取最近 Agent 消息**

在组件内从后向前查找最近一条 `assistant` 消息；不存在时显示有方向性的空状态。

- [ ] **Step 2: 渲染当前结论卡片**

在任务标题下方加入 `section.analysis-summary`。消息内容保留换行，超过可读高度时用原生 `<details>` 提供“展开完整结论”。

- [ ] **Step 3: 增加展开对话入口**

在任务标题操作区渲染“展开对话”按钮，由 `App` 根据对话关闭状态控制可见性。

- [ ] **Step 4: 运行测试**

Run: `npm test -- --run src/App.test.tsx`

Expected: 新增行为测试与原有测试全部通过。

- [ ] **Step 5: 提交中间工作台输出**

```powershell
git add web/src/components/TaskWorkbench.tsx web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: surface agent conclusions in workbench"
```

---

### Task 4: 重建响应式布局与视觉层级

**Files:**
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `app-shell`, `project-rail`, `task-workbench`, `conversation-panel`, `project-list`, `analysis-summary` 等结构类。
- Produces: 固定视口三栏、独立滚动、可收起对话、窄屏抽屉式布局。

- [ ] **Step 1: 建立新的设计 token**

设置墨蓝、证据绿、纸面白、分隔灰和琥珀变量；统一中文正文字体与等宽工具字体，补充 `:focus-visible`。

- [ ] **Step 2: 固定桌面工作台高度**

让 `html`, `body`, `#root`, `.app-shell` 使用 `height: 100%` 或 `100dvh` 与 `overflow: hidden`。三栏添加 `min-height: 0`，各自的内容容器独立滚动。

- [ ] **Step 3: 设计项目列表和任务导航**

为项目按钮、当前项目状态、文件行、任务行和新建项目展开态建立紧凑层级，避免原生下拉框和大面积装饰留白。

- [ ] **Step 4: 设计分析画布**

限制任务标题字号，给当前结论、计划、执行记录和产物建立清晰层级；网格只出现在主画布并降低对比度。

- [ ] **Step 5: 设计固定对话结构**

对话栏使用 `grid-template-rows: auto minmax(0, 1fr) auto auto`，消息区独立滚动，输入区固定在底部；工具消息使用 `<details>` 的折叠外观。

- [ ] **Step 6: 添加响应式规则**

在 1180px 以下把对话栏变为覆盖式抽屉，在 760px 以下让项目栏和主画布单列，保证输入和按钮可操作。

- [ ] **Step 7: 运行前端测试和构建**

Run: `npm test -- --run`

Expected: 全部测试通过。

Run: `npm run build`

Expected: TypeScript 与 Vite 构建均成功。

- [ ] **Step 8: 提交视觉重构**

```powershell
git add web/src/styles.css web/src/components/Conversation.tsx
git commit -m "feat: redesign analysis workbench layout"
```

---

### Task 5: 浏览器验收与收尾

**Files:**
- Modify only if browser verification reveals a defect.

**Interfaces:**
- Consumes: 本地 Vite 页面和已有真实项目数据。
- Produces: 桌面与窄屏验收证据。

- [ ] **Step 1: 桌面视口验收**

在 1280 × 720 下验证：页面 `scrollHeight` 接近视口高度；左、中、右栏各自滚动；输入框可见；中间显示当前结论；对话可收起。

- [ ] **Step 2: 长对话验收**

确认右侧消息很多时，`.task-workbench` 高度仍等于视口高度，主页面不产生数千像素空白。

- [ ] **Step 3: 窄屏验收**

在 820 × 900 与 390 × 844 下检查项目、工作台、对话入口和模型设置，确认没有不可达操作和水平溢出。

- [ ] **Step 4: 检查浏览器错误**

读取页面控制台，只接受无错误、无 React 警告的结果。

- [ ] **Step 5: 完整验证与最终提交**

Run: `npm test -- --run && npm run build`

Expected: 退出码为 0，无失败测试。

Run: `git diff --check`

Expected: 无空白错误。

```powershell
git add web/src docs/superpowers/plans/2026-08-05-workbench-ux-redesign.md
git commit -m "feat: complete workbench ux redesign"
```
