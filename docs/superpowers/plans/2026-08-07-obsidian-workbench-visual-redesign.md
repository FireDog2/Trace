# TRACE Obsidian Workbench Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 TRACE 数据分析工作台重塑为高级、深沉、克制的黑曜石研究室，并系统修正三栏比例和模型配置弹窗。

**Architecture:** 保持 React 19 单页结构、现有组件职责和 API 不变。`ProjectNav` 只更新品牌文案，`ModelSettings` 增加厂商区与配置区的语义分组，全部视觉与响应式规则集中在 `styles.css`；测试锁定品牌和弹窗信息架构，浏览器验收锁定视觉比例与响应式行为。

**Tech Stack:** React 19、TypeScript 5.7、Vite 6、Vitest、Testing Library、原生 CSS。

## Global Constraints

- 产品英文名为 `TRACE`，品牌副标识为 `LOCAL ANALYSIS SYSTEM`。
- 中文操作界面保持不变，不翻译用户创建的项目名称。
- 不新增运行时依赖，不修改后端 API、领域模型或数据。
- 桌面宽屏使用左栏 328px、中间最小 620px、右侧默认 400px 且限制在 380–440px 的比例。
- `980px–1319px` 聊天区使用覆盖式抽屉；`< 980px` 项目栏和聊天栏不同时挤占主画布；`< 640px` 主画布与配置弹窗使用单列布局。
- 所有交互保留键盘焦点、可访问名称和 `prefers-reduced-motion` 支持。
- 保留现有项目切换、数据上传、任务切换、对话收起、审批、发送消息和模型配置行为。

---

### Task 1: 锁定 TRACE 品牌与弹窗信息架构

**Files:**
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/components/ProjectNav.tsx:46-50`
- Modify: `web/src/components/ModelSettings.tsx:90-161`

**Interfaces:**
- Consumes: `ProjectNav` 现有品牌区；`ModelSettingsProps` 和 `configuration.providers`。
- Produces: `TRACE` 品牌文案；`LOCAL ANALYSIS SYSTEM` 副标识；弹窗内“模型供应商”和“连接配置”两个语义分区。

- [ ] **Step 1: 写品牌与弹窗分区的失败测试**

在 `web/src/App.test.tsx` 增加以下测试：

```tsx
it("uses the TRACE brand and groups model settings by responsibility", async () => {
  render(<App api={fakeApiWithCompletedTask()} />);

  expect(await screen.findByText("TRACE", { selector: ".brand-block strong" })).toBeVisible();
  expect(screen.getByText("LOCAL ANALYSIS SYSTEM")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "更改模型配置" }));
  expect(screen.getByRole("heading", { name: "模型供应商" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "连接配置" })).toBeVisible();
});
```

- [ ] **Step 2: 运行测试并确认失败原因正确**

Run: `npm test -- --run src/App.test.tsx`

Expected: FAIL，因为当前品牌主标题仍为“迹析”，弹窗没有两个分区标题。

- [ ] **Step 3: 更新品牌文案**

将 `ProjectNav.tsx` 品牌区改为：

```tsx
<header className="brand-block">
  <span className="brand-index">LOCAL ANALYSIS SYSTEM</span>
  <strong>TRACE</strong>
  <p>每个结论，都有来路。</p>
</header>
```

- [ ] **Step 4: 为模型配置弹窗增加双区结构**

在 `ModelSettings.tsx` 的 `<form>` 内，将厂商选择与字段包进以下结构；保留现有字段、事件处理和 footer：

```tsx
<div className="model-settings-layout">
  <section className="settings-provider-panel" aria-labelledby="provider-heading">
    <h3 id="provider-heading">模型供应商</h3>
    <p>选择分析任务使用的推理服务。</p>
    <fieldset className="provider-selector">
      <legend className="sr-only">选择厂商</legend>
      {configuration.providers.map((item, index) => (
        <label key={item.provider} className={provider === item.provider ? "selected" : ""}>
          <input
            type="radio"
            aria-label={item.label}
            name="provider"
            value={item.provider}
            checked={provider === item.provider}
            onChange={() => chooseProvider(item.provider)}
          />
          <span className="provider-number">0{index + 1}</span>
          <span>
            <strong>{item.label}</strong>
            <small>{item.custom_url ? "OpenAI 兼容接口" : "官方接口"}</small>
          </span>
        </label>
      ))}
    </fieldset>
  </section>

  <section className="settings-config-panel" aria-labelledby="configuration-heading">
    <h3 id="configuration-heading">连接配置</h3>
    <p>设置当前供应商的服务地址、模型和访问密钥。</p>
    <div className="settings-fields">
      <label>
        <span>Base URL</span>
        <input
          aria-label="Base URL"
          type="url"
          value={baseUrl}
          disabled={!definition.custom_url}
          required
          onChange={(event) => setBaseUrl(event.target.value)}
        />
        {!definition.custom_url && <small>官方地址已锁定，避免误配。</small>}
      </label>

      <label>
        <span>模型</span>
        {definition.models.length > 0 ? (
          <select aria-label="模型" value={model} onChange={(event) => setModel(event.target.value)}>
            {definition.models.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        ) : (
          <input
            aria-label="模型名称"
            value={model}
            required
            placeholder="例如：provider/model-name"
            onChange={(event) => setModel(event.target.value)}
          />
        )}
      </label>

      <label>
        <span>API Key</span>
        <input
          aria-label="API Key"
          type="password"
          value={apiKey}
          required={!preservesKey}
          autoComplete="new-password"
          placeholder={preservesKey ? "留空则继续使用已保存的密钥" : "输入厂商 API Key"}
          onChange={(event) => setApiKey(event.target.value)}
        />
        <small>{preservesKey ? "已安全保存；此处不会回显。" : "密钥仅保存在当前 Windows 用户的加密存储中。"}</small>
      </label>
    </div>
  </section>
</div>
```

新增 `.sr-only` 供不可见 legend 使用，但不移除 fieldset/legend 语义。

- [ ] **Step 5: 运行聚焦测试**

Run: `npm test -- --run src/App.test.tsx`

Expected: 全部通过。

- [ ] **Step 6: 提交品牌与结构**

```powershell
git add web/src/App.test.tsx web/src/components/ProjectNav.tsx web/src/components/ModelSettings.tsx
git commit -m "feat: establish TRACE brand and settings structure"
```

---

### Task 2: 建立黑曜石设计 token 与三栏比例

**Files:**
- Modify: `web/src/styles.css:1-86`

**Interfaces:**
- Consumes: `.app-shell`, `.project-rail`, `.task-workbench`, `.conversation-panel` 现有类名。
- Produces: 黑曜石 token；宽屏 `328px / minmax(620px, 1fr) / clamp(380px, 28vw, 440px)` 三栏布局。

- [ ] **Step 1: 替换根级设计 token**

使用以下 token 替换现有浅色变量，并让浏览器原生控件进入暗色模式：

```css
:root {
  font-family: Inter, "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif;
  color: #e7e3da;
  background: #090b0d;
  color-scheme: dark;
  font-synthesis: none;
  --obsidian: #090b0d;
  --graphite: #101316;
  --carbon: #15191d;
  --carbon-raised: #1a1f24;
  --fog: #e7e3da;
  --silver: #b4b5b2;
  --slate: #83898f;
  --rule: #282d31;
  --rule-strong: #3a4147;
  --gold: #b99a5e;
  --gold-bright: #d0b578;
  --gold-deep: #806a3d;
  --gold-soft: rgba(185, 154, 94, 0.12);
  --danger: #c46d62;
  --danger-soft: rgba(196, 109, 98, 0.12);
  --shadow: 0 28px 80px rgba(0, 0, 0, 0.48);
  --mono: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
}
```

- [ ] **Step 2: 更新基础状态与焦点样式**

保持 `box-sizing`、固定视口和表单字体规则；将 `:focus-visible` 改为 `2px solid var(--gold-bright)`。为 `::selection`、滚动条轨道和滑块添加低对比暗色样式，并加入 Task 1 需要的 `.sr-only`。

- [ ] **Step 3: 固定宽屏三栏比例**

将布局核心更新为：

```css
.app-shell {
  display: grid;
  grid-template-columns: 328px minmax(0, 1fr);
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  background: var(--obsidian);
}

.app-shell.with-conversation {
  grid-template-columns: 328px minmax(620px, 1fr) clamp(380px, 28vw, 440px);
}

.app-shell.conversation-collapsed {
  grid-template-columns: 328px minmax(0, 1fr);
}
```

项目栏和聊天栏使用 `var(--graphite)`，主画布使用 `var(--obsidian)`；保留三个区域独立滚动。

- [ ] **Step 4: 运行测试与构建**

Run: `npm test -- --run`

Expected: 全部通过。

Run: `npm run build`

Expected: TypeScript 和 Vite 构建成功。

- [ ] **Step 5: 提交 token 和布局比例**

```powershell
git add web/src/styles.css
git commit -m "feat: establish obsidian shell proportions"
```

---

### Task 3: 重塑项目控制台、分析画布与聊天区

**Files:**
- Modify: `web/src/styles.css:87-708`

**Interfaces:**
- Consumes: Task 2 的黑曜石 token 和三栏结构；现有组件类名。
- Produces: TRACE 项目控制台、金色证据轨迹、暗色分析画布、低干扰聊天区。

- [ ] **Step 1: 重塑品牌区与项目导航**

设置 `.brand-block` 为约 124px 高的品牌基座；`TRACE` 使用 Bahnschrift、32–36px、扩展字距。模型路由、项目行、文件行和任务行使用 Carbon 层级与低对比边线。当前项目使用：

```css
.project-link.active {
  background: linear-gradient(90deg, var(--gold-soft), rgba(185, 154, 94, 0.025));
  box-shadow: inset 2px 0 0 var(--gold);
}
```

项目列表、数据和任务增加行高与左右空间；将所有旧 `--trace*`、`--paper*`、`--ink` 和浅色硬编码替换为 Task 2 token。

- [ ] **Step 2: 重塑任务标题和当前结论**

将任务标题上限控制在 36px；标题区域使用低对比底边。`.analysis-summary` 改为 Carbon 面板，使用 `border-left: 2px solid var(--gold)`，背景加入轻微内高光，不添加大面积金色渐变。

- [ ] **Step 3: 建立金色证据轨迹**

计划连接线默认使用 `var(--rule-strong)`；当前/运行步骤使用 `var(--gold)`；完成步骤降低为银灰。编号、执行序号和产物类型仅在可交互或关键状态时使用金色。

- [ ] **Step 4: 重塑产物索引与空状态**

产物行使用暗色表面、稳定的四列布局和细微 hover 浮起；空状态使用 Slate 文本，不使用装饰性大面积背景。

- [ ] **Step 5: 降低聊天区视觉权重**

聊天栏使用更深 Graphite 背景；Agent 消息保留透明背景，用户消息使用 Carbon 面板。工具日志改为单行低亮记录，输入区使用 Carbon 面板和顶部边线。审批卡使用 Danger/Gold 语义，不使用通用高饱和颜色。

- [ ] **Step 6: 添加克制动效**

只给项目行、按钮、产物行、当前步骤与弹窗加入 140–220ms 的过渡；使用 `@keyframes evidence-pulse` 为运行状态提供低频透明度变化，并在现有 `prefers-reduced-motion` 规则中禁用。

- [ ] **Step 7: 运行完整前端验证**

Run: `npm test -- --run`

Expected: 全部通过。

Run: `npm run build`

Expected: 构建成功。

- [ ] **Step 8: 提交工作台视觉系统**

```powershell
git add web/src/styles.css
git commit -m "feat: redesign TRACE analysis surfaces"
```

---

### Task 4: 重做模型配置弹窗与响应式布局

**Files:**
- Modify: `web/src/App.tsx:25-159`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/components/ProjectNav.tsx:5-115`
- Modify: `web/src/styles.css:709-877`

**Interfaces:**
- Consumes: Task 1 的 `.model-settings-layout`、`.settings-provider-panel`、`.settings-config-panel`；Task 2 的 token。
- Produces: `projectNavOpen: boolean`；`ProjectNav` 的 `drawerOpen` 与 `onClose()`；桌面 760px 双区弹窗；980px、640px 响应式断点；覆盖式项目与聊天抽屉。

- [ ] **Step 1: 写项目抽屉开合的失败测试**

在 `web/src/App.test.tsx` 增加：

```tsx
it("opens and closes the project console for narrow screens", async () => {
  render(<App api={fakeApiWithCompletedTask()} />);

  const toggle = await screen.findByRole("button", { name: "打开项目导航" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");

  fireEvent.click(screen.getByRole("button", { name: "关闭项目导航" }));
  expect(toggle).toHaveAttribute("aria-expanded", "false");
});
```

Run: `npm test -- --run src/App.test.tsx`

Expected: FAIL，因为当前不存在项目抽屉按钮和状态。

- [ ] **Step 2: 增加项目抽屉状态与控制接口**

在 `App.tsx` 增加 `projectNavOpen` 状态和根级按钮：

```tsx
const [projectNavOpen, setProjectNavOpen] = useState(false);

<button
  type="button"
  className="project-drawer-toggle"
  aria-label="打开项目导航"
  aria-expanded={projectNavOpen}
  onClick={() => setProjectNavOpen(true)}
>
  <span aria-hidden="true">TRACE</span>
  项目
</button>
```

给 `ProjectNav` 增加：

```ts
drawerOpen: boolean;
onClose(): void;
```

将 `<nav>` 类名设为 `project-rail drawer-open` 或 `project-rail`，并在品牌区增加 `aria-label="关闭项目导航"` 的关闭按钮。选择项目或任务后调用现有选择回调，再调用 `onClose()`。桌面端通过 CSS 隐藏抽屉控制，不改变常驻导航行为。

- [ ] **Step 3: 运行项目抽屉测试**

Run: `npm test -- --run src/App.test.tsx`

Expected: 全部通过。

- [ ] **Step 4: 重做弹窗容器和遮罩**

`.model-settings-backdrop` 使用 `rgba(3, 4, 5, 0.78)` 与适度 `backdrop-filter: blur(16px)`；`.model-settings` 使用：

```css
.model-settings {
  width: min(760px, 92vw);
  max-height: min(780px, 90dvh);
  overflow: auto;
  border: 1px solid var(--rule-strong);
  border-radius: 16px;
  background: var(--carbon);
  box-shadow: var(--shadow), inset 0 1px 0 rgba(255, 255, 255, 0.04);
}
```

- [ ] **Step 5: 建立厂商/配置双区网格**

`.model-settings-layout` 使用 `grid-template-columns: minmax(220px, .78fr) minmax(0, 1.22fr)`。左侧厂商区使用更深背景和右边线；右侧字段区保留充足留白。选中厂商使用暗金边线、Carbon Raised 背景和可见 radio 状态。

- [ ] **Step 6: 重做字段和操作区**

输入框、下拉框、密码框统一 44px 最小高度；focus 使用暗金边线与轻微外环。Footer 与内容之间增加顶边线，保存按钮为唯一暗金主按钮，取消按钮保持透明。

- [ ] **Step 7: 实现 1320px、980px 和 640px 断点**

- `max-width: 1319px`：左侧变为 288px，聊天栏固定覆盖在右侧，宽度 `min(420px, 88vw)`。
- `max-width: 979px`：页面只保留主画布网格列；项目栏固定覆盖在左侧并默认移出视口，`.drawer-open` 时滑入；聊天继续覆盖在右侧，两者都不挤压主内容。`.project-drawer-toggle` 在此断点显示。
- `max-width: 639px`：工作台内容单列、任务标题缩小、聊天占满宽度；弹窗改为单列，双区边线改为底边线，footer 按钮保持可触达。

- [ ] **Step 8: 运行测试和构建**

Run: `npm test -- --run`

Expected: 全部通过。

Run: `npm run build`

Expected: 构建成功，无 TypeScript 错误。

- [ ] **Step 9: 提交弹窗与响应式规则**

```powershell
git add web/src/App.tsx web/src/App.test.tsx web/src/components/ProjectNav.tsx web/src/styles.css
git commit -m "feat: refine TRACE settings and responsive layout"
```

---

### Task 5: 浏览器视觉验收与收尾

**Files:**
- Modify only if verification reveals a defect: `web/src/styles.css`
- Modify only if verification reveals a semantic defect: `web/src/components/ProjectNav.tsx`, `web/src/components/ModelSettings.tsx`

**Interfaces:**
- Consumes: 本地 API `http://127.0.0.1:8765` 和 Vite 页面 `http://127.0.0.1:5173`。
- Produces: 桌面/平板/移动视觉验收结果，无控制台错误的最终工作台。

- [ ] **Step 1: 在真实数据下验收 1440×900**

检查：TRACE 品牌可见；左栏计算宽度约 328px；聊天栏计算宽度在 380–440px；中间宽度不少于 620px；三栏独立滚动；当前结论是首要焦点。

- [ ] **Step 2: 打开模型配置弹窗**

检查：桌面宽度约 760px；厂商与配置左右分区；保存为唯一高优先级按钮；弹窗内容不溢出视口；Escape 和点击遮罩仍可关闭。

- [ ] **Step 3: 验收 1280×720 与 1024×768**

检查聊天栏成为右侧覆盖层，不把中间画布压到不可读；左栏内容仍可独立滚动；输入框保持在聊天栏底部。

- [ ] **Step 4: 验收 390×844**

检查主画布、项目内容、聊天入口和单列弹窗均无水平溢出；按钮、输入框和关闭操作可达。

- [ ] **Step 5: 检查浏览器错误与可访问性状态**

读取控制台 error/warn；检查键盘焦点可见；模拟 `prefers-reduced-motion: reduce` 后确认动画被禁用。

- [ ] **Step 6: 运行最终验证**

Run: `npm test -- --run`

Expected: 全部测试通过。

Run: `npm run build`

Expected: TypeScript 与 Vite 构建成功。

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 7: 提交浏览器修正（仅在产生修正时）**

```powershell
git add web/src/styles.css web/src/components/ProjectNav.tsx web/src/components/ModelSettings.tsx
git commit -m "fix: polish TRACE visual verification findings"
```
