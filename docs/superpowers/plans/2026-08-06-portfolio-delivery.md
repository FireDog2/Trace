# Portfolio UI and Repository Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose data governance, teammate status, trace replay, and candidate skill review in the workbench, then ship a deterministic golden demo, CI, security documentation, and a recruiter-readable README.

**Architecture:** Extend the existing three-column workbench without replacing its layout. Project-level data governance lives in the left navigation, task-level agents and trace live in the central workbench, and one-time approvals remain in the conversation panel; deterministic backend fixtures feed both acceptance tests and checked-in example trace material.

**Tech Stack:** React 19, TypeScript 5.7, Vite 6, Vitest/Testing Library, FastAPI/pytest, PostgreSQL 16 in GitHub Actions, Markdown documentation.

## Global Constraints

- Preserve the fixed-height three-column layout and independent scrolling introduced by the existing workbench redesign.
- Keep credentials write-only in the browser: never store a DSN in React state after a successful save and never return it from the API.
- Make approvals, agent states, retry outcomes, and trace relationships readable without exposing raw model internals or sensitive query results.
- The deterministic demo and CI must not require an external model API key.
- The README must state that PostgreSQL cost is heuristic, Python execution is locally trusted rather than a hostile-code sandbox, and multi-agent execution is bounded to three teammates.
- Do not add animation that delays interaction or obscures failure states.

---

### Task 1: Data-source and policy configuration UI

**Files:**
- Create: `web/src/components/DataSourceSettings.tsx`
- Create: `web/src/components/DataSourceSettings.test.tsx`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/ProjectNav.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: data-source and policy APIs from the PostgreSQL plan.
- Produces a project-level settings drawer with source creation, schema inspection, and table/field access controls.

- [ ] **Step 1: Write the interaction tests**

```tsx
it("saves a postgres source without retaining the dsn", async () => {
  const createDataSource = vi.fn(async () => SOURCE);
  render(<DataSourceSettings {...props} createDataSource={createDataSource} />);
  fireEvent.change(screen.getByLabelText("数据源名称"), { target: { value: "Warehouse" } });
  fireEvent.change(screen.getByLabelText("PostgreSQL DSN"), {
    target: { value: "postgresql://analyst:secret@localhost/portfolio" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并检查连接" }));
  await waitFor(() => expect(createDataSource).toHaveBeenCalled());
  expect(screen.getByLabelText("PostgreSQL DSN")).toHaveValue("");
  expect(screen.queryByText(/secret/)).not.toBeInTheDocument();
});

it("marks a restricted column and saves one policy version", async () => {
  render(<DataSourceSettings {...props} schema={SCHEMA} />);
  fireEvent.change(screen.getByLabelText("orders.customer_email 权限"), {
    target: { value: "approval_required" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存访问策略" }));
  await waitFor(() => expect(props.replacePolicies).toHaveBeenCalledWith(
    "source-1",
    expect.arrayContaining([
      { table: "orders", column: "customer_email", access: "approval_required" },
    ]),
  ));
});
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run: `npm --prefix web test -- --run DataSourceSettings`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Add client types and methods**

```typescript
export type AccessLevel = "allowed" | "approval_required" | "denied";

export interface DataSource {
  id: string;
  project_id: string;
  name: string;
  dialect: "postgres";
  policy_version: number;
  created_at: string;
  updated_at: string;
}

export interface DatabaseTable {
  name: string;
  columns: string[];
}
```

Add `listDataSources`, `createDataSource`, `getDataSourceSchema`, and `replaceDataSourcePolicies` to `ApiClient` and `HttpApiClient`.

- [ ] **Step 4: Build the settings drawer**

Use a password-type DSN input with `autoComplete="off"`. Render each table as a collapsible group and each field as a native select with labels `自动允许`, `需要审批`, and `禁止访问`. Require explicit save; display returned policy version and never echo the DSN.

Open the drawer from a `数据库治理` button in `ProjectNav`. Keep model settings and data-source settings as separate dialogs.

```tsx
export function DataSourceSettings({ open, projectId, api, onClose }: Props) {
  const [dsn, setDsn] = useState("");
  const [source, setSource] = useState<DataSource | null>(null);

  async function saveSource(event: FormEvent) {
    event.preventDefault();
    const saved = await api.createDataSource(projectId, { name, dsn });
    setDsn("");
    setSource(saved);
  }

  return open ? (
    <aside role="dialog" aria-label="数据库治理">
      <form onSubmit={saveSource}>
        <input aria-label="PostgreSQL DSN" type="password" autoComplete="off"
          value={dsn} onChange={(event) => setDsn(event.target.value)} />
        <button type="submit">保存并检查连接</button>
      </form>
      {source && <PolicyEditor source={source} api={api} />}
      <button type="button" onClick={onClose}>关闭</button>
    </aside>
  ) : null;
}
```

- [ ] **Step 5: Run component and application tests**

Run: `npm --prefix web test -- --run`

Run: `npm --prefix web run build`

Expected: tests and TypeScript/Vite build PASS.

- [ ] **Step 6: Commit governance UI**

```powershell
git add web/src/components/DataSourceSettings.tsx web/src/components/DataSourceSettings.test.tsx web/src/types.ts web/src/api.ts web/src/App.tsx web/src/components/ProjectNav.tsx web/src/styles.css
git commit -m "feat: configure database access policies"
```

---

### Task 2: Agent team, trace replay, and skill review UI

**Files:**
- Create: `web/src/components/AgentTeamPanel.tsx`
- Create: `web/src/components/TraceTimeline.tsx`
- Create: `web/src/components/SkillCandidatePanel.tsx`
- Create: `web/src/components/AgentObservability.test.tsx`
- Modify: `web/src/components/TaskWorkbench.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `AgentRun[]`, `AnalysisRun[]`, `TraceEvent[]`, and skill candidate client methods.
- Produces central workbench tabs `分析`, `Agent 团队`, and `Trace` plus a candidate skill review card.

- [ ] **Step 1: Write accessible behavior tests**

```tsx
it("shows teammate role, objective, state, and safe failure", async () => {
  render(<AgentTeamPanel agents={AGENTS} />);
  expect(screen.getByText("SQL Agent")).toBeVisible();
  expect(screen.getByText("查询区域利润")).toBeVisible();
  expect(screen.getByText("执行中")).toBeVisible();
  expect(screen.getByText("query_timeout")).toBeVisible();
});

it("orders trace events and exposes retry relationships", () => {
  render(<TraceTimeline events={TRACE_EVENTS} />);
  const rows = screen.getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("Lead Agent 启动");
  expect(rows[1]).toHaveTextContent("SQL 校验");
  expect(screen.getByText("第 2 次尝试")).toBeVisible();
});

it("approves a candidate without enabling it", async () => {
  render(<SkillCandidatePanel candidate={CANDIDATE} onDecision={onDecision} />);
  fireEvent.click(screen.getByRole("button", { name: "批准候选 Skill" }));
  await waitFor(() => expect(onDecision).toHaveBeenCalledWith(CANDIDATE.id, true));
  expect(screen.getByText("批准后仍保持未启用")).toBeVisible();
});
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run: `npm --prefix web test -- --run AgentObservability`

Expected: FAIL because the three components do not exist.

- [ ] **Step 3: Implement the agent team panel**

Group agents by role and render status badges for `created`, `working`, `waiting`, `completed`, `failed`, and `cancelled`. Show objective, duration, result summary, and safe error category. Do not render teammate transcripts.

```tsx
export function AgentTeamPanel({ agents }: { agents: AgentRun[] }) {
  return <ul className="agent-team-list">
    {agents.map((agent) => <li key={agent.id}>
      <span>{ROLE_LABELS[agent.role]}</span>
      <strong>{agent.objective}</strong>
      <span className={`agent-status ${agent.status}`}>{STATUS_LABELS[agent.status]}</span>
      {agent.error_category && <code>{agent.error_category}</code>}
    </li>)}
  </ul>;
}
```

- [ ] **Step 4: Implement the trace timeline**

Render events by ascending sequence. Use indentation derived from `parent_event_id`, not array nesting assumptions. Summarize known event types in Chinese while retaining the raw event type in a monospace label. Collapsed details may show redacted payload JSON, duration, attempt, error category, and artifact links.

```tsx
export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const depth = traceDepths(events);
  return <ol className="trace-timeline">
    {[...events].sort((a, b) => a.sequence - b.sequence).map((event) => (
      <li key={event.id} style={{ "--trace-depth": depth.get(event.id) ?? 0 } as CSSProperties}>
        <strong>{EVENT_LABELS[event.event_type] ?? event.event_type}</strong>
        <code>{event.event_type}</code>
        {event.attempt > 1 && <span>第 {event.attempt} 次尝试</span>}
      </li>
    ))}
  </ol>;
}
```

- [ ] **Step 5: Implement candidate review**

Show source run, version, generator model, static validation status, and Markdown diff/preview. Approval and rejection are terminal actions. Always display `默认未启用` beside an approved candidate.

```tsx
export function SkillCandidatePanel({ candidate, onDecision }: Props) {
  return <section aria-label="候选 Skill">
    <header>{candidate.name} v{candidate.version}</header>
    <pre>{candidate.markdown}</pre>
    <p>批准后仍保持未启用</p>
    <button onClick={() => onDecision(candidate.id, true)}>批准候选 Skill</button>
    <button onClick={() => onDecision(candidate.id, false)}>拒绝候选 Skill</button>
  </section>;
}
```

- [ ] **Step 6: Integrate task tabs and refresh behavior**

In `App`, fetch agents and runs when a task is selected and refresh them on `agent.*` SSE events. Fetch trace only when the Trace tab is opened or its selected run changes. Preserve the current analysis tab as the default and keep the conversation panel independent.

```tsx
useEffect(() => {
  if (!selectedTaskId) return;
  void Promise.all([api.listAgents(selectedTaskId), api.listRuns(selectedTaskId)])
    .then(([nextAgents, nextRuns]) => {
      setAgents(nextAgents);
      setRuns(nextRuns);
    });
}, [api, selectedTaskId]);

async function openTrace(runId: string) {
  setSelectedWorkbenchTab("trace");
  setTraceEvents(await api.getTrace(runId));
}
```

- [ ] **Step 7: Run frontend verification**

Run: `npm --prefix web test -- --run`

Run: `npm --prefix web run build`

Expected: both commands PASS.

- [ ] **Step 8: Commit observability UI**

```powershell
git add web/src/components/AgentTeamPanel.tsx web/src/components/TraceTimeline.tsx web/src/components/SkillCandidatePanel.tsx web/src/components/AgentObservability.test.tsx web/src/components/TaskWorkbench.tsx web/src/App.tsx web/src/styles.css
git commit -m "feat: visualize agent teams and traces"
```

---

### Task 3: Deterministic PostgreSQL golden-path acceptance test

**Files:**
- Create: `tests/fixtures/postgres_business_review.sql`
- Create: `tests/test_portfolio_acceptance.py`
- Create: `examples/postgres-business-review/README.md`
- Create: `examples/postgres-business-review/question.md`
- Create: `examples/postgres-business-review/expected-findings.md`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: all backend capabilities from the three preceding plans.
- Produces one API-key-free scenario covering delegation, approval, timeout recovery, evidence, replay, and candidate skill generation.

- [ ] **Step 1: Create a deterministic PostgreSQL fixture**

The SQL fixture creates `region_operations` with columns:

```sql
month date NOT NULL,
region text NOT NULL,
revenue numeric NOT NULL,
logistics_cost numeric NOT NULL,
profit numeric NOT NULL,
manager_email text NOT NULL
```

Insert 24 rows for East and West across twelve months. East logistics cost rises in the final quarter while revenue remains approximately flat and profit falls. Grant the test user `SELECT` only.

- [ ] **Step 2: Write the acceptance test provider and assertions**

```python
def test_portfolio_golden_path_is_safe_observable_and_replayable(portfolio_services) -> None:
    task = portfolio_services.create_golden_task()
    completed = portfolio_services.agent().run(task.id)

    assert completed.status is TaskStatus.COMPLETED
    agents = portfolio_services.team_repository.list_runs(task.id)
    assert {agent.role for agent in agents} == {
        AgentRole.KNOWLEDGE, AgentRole.SQL, AgentRole.REPORT
    }
    approvals = portfolio_services.approvals.list_for_task(task.id)
    assert any(item.status == "consumed" for item in approvals)
    trace = portfolio_services.trace_for_task(task.id)
    assert any(item.error_category == "query_timeout" for item in trace)
    assert any(item.attempt == 2 and item.status == "completed" for item in trace)
    report = portfolio_services.report_for_task(task.id)
    assert portfolio_services.artifacts.evidence_for(report.id)
    run = portfolio_services.traces.list_runs(task.id)[0]
    portfolio_services.traces.accept_run(run.id)
    candidate = portfolio_services.curator.create_candidate(run.id, "deterministic-curator")
    assert candidate.enabled is False
```

The scripted lead spawns knowledge and SQL agents, awaits them, then spawns the report agent. The scripted SQL agent first selects `manager_email`, pauses for approval, executes a deliberately slow fixture query that times out, then retries with a filtered aggregate. The test harness makes the approval decision explicitly; it does not auto-approve inside the model double.

- [ ] **Step 3: Run the acceptance test and verify the expected failure**

Run: `python -m pytest tests/test_portfolio_acceptance.py -v`

Expected: FAIL at the first missing orchestration or safety behavior; implement only fixture/test wiring needed to exercise already-planned components.

- [ ] **Step 4: Add example instructions and expected findings**

Document the exact question, expected approval, expected timeout/retry, expected business conclusion, and commands for starting the app against the fixture PostgreSQL database. Do not commit a real credential; use `PORTFOLIO_POSTGRES_DSN` in manual instructions.

- [ ] **Step 5: Run acceptance and full backend suites**

Run: `python -m pytest tests/test_portfolio_acceptance.py -v`

Run: `python -m pytest -v`

Expected: both commands PASS.

- [ ] **Step 6: Commit the golden path**

```powershell
git add tests/fixtures/postgres_business_review.sql tests/test_portfolio_acceptance.py tests/conftest.py examples/postgres-business-review
git commit -m "test: add governed multi agent postgres demo"
```

---

### Task 4: CI and checked-in replay example

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/export_golden_trace.py`
- Create: `examples/traces/golden-run.json`
- Modify: `.gitignore`

**Interfaces:**
- Produces reproducible backend/frontend CI and a sanitized trace that GitHub visitors can inspect without running the app.

- [ ] **Step 1: Add a PostgreSQL-backed GitHub Actions workflow**

Configure a `postgres:16` service with health checks and a non-secret test password. The workflow checks out code, sets up Python 3.12 and Node 20, installs `-e ".[dev]"` plus `npm --prefix web ci`, initializes the SQL fixture, then runs:

```yaml
- run: python -m pytest -v
  env:
    PORTFOLIO_POSTGRES_DSN: postgresql://portfolio:portfolio@localhost:5432/portfolio
- run: npm --prefix web test -- --run
- run: npm --prefix web run build
```

- [ ] **Step 2: Add deterministic trace export**

`scripts/export_golden_trace.py` runs the deterministic scenario against `PORTFOLIO_POSTGRES_DSN`, loads the completed run through `TraceRepository.events_for_run`, asserts that `redact_payload(serialized) == serialized`, and writes stable JSON sorted by sequence with generated UUIDs and timestamps replaced by readable fixture IDs/times.

```python
def main() -> None:
    services, task = run_golden_scenario(os.environ["PORTFOLIO_POSTGRES_DSN"])
    run = services.traces.list_runs(task.id)[0]
    payload = [asdict(event) for event in services.traces.events_for_run(run.id)]
    sanitized = redact_payload(payload)
    assert sanitized == payload
    stable = stabilize_trace(sanitized)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(stable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 3: Generate and inspect the checked-in trace**

Run: `python scripts/export_golden_trace.py`

Run: `rg -n -i "password|secret|api[_-]?key|postgresql://" examples/traces/golden-run.json`

Expected: the export command succeeds; the secret scan returns no matches.

- [ ] **Step 4: Run local CI-equivalent verification**

Run: `python -m pytest -v`

Run: `npm --prefix web test -- --run`

Run: `npm --prefix web run build`

Expected: all three commands PASS.

- [ ] **Step 5: Commit CI and example trace**

```powershell
git add .github/workflows/ci.yml scripts/export_golden_trace.py examples/traces/golden-run.json .gitignore
git commit -m "ci: verify postgres agent portfolio"
```

---

### Task 5: Interview-oriented documentation and final verification

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/sql-safety.md`
- Create: `docs/multi-agent-runtime.md`
- Create: `docs/trace-and-skill.md`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: verified implementation behavior and checked-in golden trace.
- Produces the public GitHub narrative and exact reproduction commands.

- [ ] **Step 1: Rewrite the README opening for a 30-second scan**

Use this order:

1. Product statement and one screenshot/GIF slot that is added only after the real media file exists.
2. Four capability cards: governed PostgreSQL, bounded agent team, replayable trace, reviewed skill candidate.
3. Architecture diagram.
4. SQL safety pipeline.
5. Golden demo steps and expected approval/timeout/retry.
6. Quick start for fixture mode and optional real model mode.
7. Test commands.
8. Security boundaries and non-goals.
9. Design documents and key code entry points.

Do not claim production-grade sandboxing, arbitrary database compatibility, autonomous self-improvement, or measured performance without evidence.

- [ ] **Step 2: Write architecture and subsystem documents**

`architecture.md` maps the lead, teammates, tool dispatcher, SQL gateway, SQLite metadata, PostgreSQL, artifacts, and React UI. `sql-safety.md` documents AST rules, explain heuristics, approval binding, and defense in depth. `multi-agent-runtime.md` documents lifecycle, role allowlists, concurrency limit, cancellation, and restart behavior. `trace-and-skill.md` documents event schema, redaction, eligibility, static validation, and disabled-by-default candidates.

- [ ] **Step 3: Write security and contribution guidance**

`SECURITY.md` must state supported deployment scope, trusted local Python execution, read-only PostgreSQL requirement, secret handling, logging exclusions, known limitations, and private reporting instructions. `CONTRIBUTING.md` lists Python/Node versions, setup, tests, migration rules, and the requirement that new tools define permission and trace behavior.

- [ ] **Step 4: Verify every documented command**

Run every quick-start and test command exactly as written in the documentation. For commands requiring PostgreSQL, use the same `PORTFOLIO_POSTGRES_DSN` fixture environment as CI. Correct the documentation immediately if output differs.

- [ ] **Step 5: Run final automated verification**

Run: `python -m pytest -v`

Run: `npm --prefix web test -- --run`

Run: `npm --prefix web run build`

Run: `git diff --check`

Expected: backend tests, frontend tests, frontend build, and whitespace check all exit successfully.

- [ ] **Step 6: Perform a requirements audit**

Compare the implementation line by line with `docs/superpowers/specs/2026-08-06-agent-engineer-portfolio-design.md`. Record any intentionally deferred acceptance criterion under README `Known limitations`; do not silently mark it complete.

- [ ] **Step 7: Commit documentation**

```powershell
git add README.md docs/architecture.md docs/sql-safety.md docs/multi-agent-runtime.md docs/trace-and-skill.md SECURITY.md CONTRIBUTING.md
git commit -m "docs: present agent engineering portfolio"
```
