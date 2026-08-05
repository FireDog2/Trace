# Exclusive Analysis Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure users see the new-task objective input or the active-task conversation input, never both at once.

**Architecture:** `App` remains the owner of task-selection state and conditionally renders `Conversation` only after a task detail is loaded. A root state class switches the desktop grid between two and three columns; `Conversation` becomes task-only and drops its unused disabled state.

**Tech Stack:** React 19, TypeScript, Testing Library, Vitest, CSS.

## Global Constraints

- No backend or API changes.
- Preserve project navigation, model settings, task workbench, messages, and approvals.
- On every viewport, only one analysis textarea is rendered at a time.

---

### Task 1: Make new-task and active-task inputs mutually exclusive

**Files:**
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/Conversation.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `task: TaskDetail | null` in `App`.
- Produces: `.app-shell.with-conversation` only when `task` exists; `ConversationProps` without `disabled`.

- [ ] **Step 1: Write the failing behavior test**

Add a no-task API fixture by overriding `listTasks` to return `[]`, then assert:

```tsx
it("shows exactly one analysis input for the current task state", async () => {
  const noTaskApi = fakeApiWithCompletedTask();
  noTaskApi.listTasks = async () => [];
  noTaskApi.listArtifacts = async () => [];
  render(<App api={noTaskApi} />);

  expect(await screen.findByRole("textbox", { name: "分析目标" })).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "给分析 Agent 留言" })).not.toBeInTheDocument();
});
```

Extend the existing completed-task test with:

```tsx
expect(screen.getByRole("textbox", { name: "给分析 Agent 留言" })).toBeVisible();
expect(screen.queryByRole("textbox", { name: "分析目标" })).not.toBeInTheDocument();
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/App.test.tsx`

Expected: the no-task assertion fails because the disabled conversation textarea is still rendered.

- [ ] **Step 3: Implement conditional rendering and layout**

In `App.tsx`, make the root class conditional and render `Conversation` only for an active task:

```tsx
<div className={task ? "app-shell with-conversation" : "app-shell"}>
  {/* navigation and workbench */}
  {task && (
    <Conversation
      messages={task.messages}
      events={events}
      onSend={sendMessage}
      onApproval={decideApproval}
    />
  )}
</div>
```

In `Conversation.tsx`, remove `disabled?: boolean` from `ConversationProps`, remove it from destructuring, and use only `sending`/content state for composer disabling.

In `styles.css`, make two columns the default and three columns task-only:

```css
.app-shell { grid-template-columns: 248px minmax(540px, 1fr); }
.app-shell.with-conversation { grid-template-columns: 248px minmax(540px, 1fr) 360px; }
```

At the `max-width: 1100px` breakpoint, apply the existing two-column stacked-conversation rules only to `.app-shell.with-conversation`.

- [ ] **Step 4: Run tests and build to verify GREEN**

Run:

```powershell
npm test -- --run
npm run build
```

Expected: 7 frontend tests pass and the TypeScript/Vite production build exits 0.

- [ ] **Step 5: Commit**

```powershell
git add web/src/App.test.tsx web/src/App.tsx web/src/components/Conversation.tsx web/src/styles.css
git commit -m "fix: show one analysis input at a time"
```
